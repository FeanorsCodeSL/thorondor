import asyncio
import sqlite3
import time
from dataclasses import replace

import anyio
import pytest

from orchestrator import fakes
from orchestrator.fetch_pipeline import run_fetch
from orchestrator.models import FetchRequest, SearchRequest
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.page_cache import (
    PageCacheRecord,
    PageRefreshCoordinator,
    SqlitePageCache,
    StoredTargetSnapshot,
    build_cache_key,
    cache_bypass_reason,
    conservative_url_identity,
)
from orchestrator.pipeline import run_search
from orchestrator.robots_policy import RobotsRule, RobotsSnapshot
from orchestrator.resource_policy import ResourcePolicy, RuntimeAdmission
from orchestrator.types import FetchStageOutcome, Page


def _outcome(
    markdown: str,
    html: str,
    *,
    status: int = 200,
    etag: str | None = None,
) -> FetchStageOutcome:
    url = "https://shop.test/product"
    page = Page(
        url,
        "Product",
        markdown,
        html=html,
        final_url=url,
        status_code=status,
        content_type="text/html",
        etag=etag,
    )
    return FetchStageOutcome(
        requested_url=url,
        final_url=url,
        code=FetchOutcomeCode.CONTENT,
        retrieval_method="test_browser",
        elapsed_ms=1,
        status_code=status,
        content_type="text/html",
        title="Product",
        etag=etag,
        page=page,
    )


class SequenceFetcher:
    supported_capabilities = frozenset(
        {"markdown", "javascript", "links", "metadata", "raw_html"}
    )

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def fetch(self, _urls, _capabilities, _include_raw_html):
        self.calls += 1
        return [self.outcomes.pop(0)]


class PassthroughCleaner:
    cleaner_version = "test@1"

    def clean(self, page):
        return type("Cleaned", (), {"page": page})()


def _cache_deps(tmp_path, fetcher, **overrides):
    values = {
        "extractor": fetcher,
        "page_cache": SqlitePageCache(str(tmp_path / "cache.sqlite3")),
        "page_cache_ttl_s": 60,
        "page_cache_stale_s": 120,
        "page_cache_retention_s": 600,
        "markdown_cleaner": PassthroughCleaner(),
        "crawl_respect_robots_txt": False,
    }
    values.update(overrides)
    deps = fakes.deps(**values)
    deps.crawl_url_safety = lambda _url: True
    return deps


def test_cache_identity_is_conservative_and_variant_aware():
    base = frozenset({"markdown", "javascript"})
    key, identity = build_cache_key(
        "HTTPS://Example.com:443/a?x=1&x=2#fragment",
        base,
        "browser",
        "cleaner@1",
    )
    equivalent, equivalent_identity = build_cache_key(
        "https://example.com/a?x=1&x=2",
        base,
        "browser",
        "cleaner@1",
    )

    assert key == equivalent
    assert identity == equivalent_identity == "https://example.com/a?x=1&x=2"
    assert build_cache_key(
        "https://www.example.com/a?x=1&x=2", base, "browser", "cleaner@1"
    )[0] != key
    assert build_cache_key(
        "https://example.com/a?x=2&x=1", base, "browser", "cleaner@1"
    )[0] != key
    assert build_cache_key(
        "https://example.com/a?x=1&x=2", base, "http", "cleaner@1"
    )[0] != key
    assert build_cache_key(
        "https://example.com/a?x=1&x=2", base, "browser", "cleaner@2"
    )[0] != key
    assert build_cache_key(
        "https://example.com/a?x=1&x=2", base | {"links"}, "browser", "cleaner@1"
    )[0] != key

    with pytest.raises(ValueError, match="credential-bearing"):
        build_cache_key(
            "https://user:secret@example.com/a",
            base,
            "browser",
            "cleaner@1",
        )


def test_cache_bypasses_raw_html_and_unrepresented_documents():
    assert cache_bypass_reason(False, frozenset({"markdown"}), False) == "disabled"
    assert cache_bypass_reason(
        True, frozenset({"markdown", "raw_html"}), False
    ) == "raw_html_persistence_disabled"
    assert cache_bypass_reason(
        True, frozenset({"markdown", "pdf"}), True
    ) == "unsupported_persistent_capability"


def test_credential_bearing_url_bypasses_cache(tmp_path):
    credential_url = "https://user:secret@shop.test/product"
    one = _outcome("One", "<main>One</main>")
    two = _outcome("Two", "<main>Two</main>")
    one = replace(
        one,
        requested_url=credential_url,
        final_url=credential_url,
        page=replace(one.page, url=credential_url, final_url=credential_url),
    )
    two = replace(
        two,
        requested_url=credential_url,
        final_url=credential_url,
        page=replace(two.page, url=credential_url, final_url=credential_url),
    )
    fetcher = SequenceFetcher(
        [one, two]
    )
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(urls=[credential_url])

    first = anyio.run(run_fetch, request, deps).results[0]
    second = anyio.run(run_fetch, request, deps).results[0]

    assert first.cache.reason == second.cache.reason == "url_credentials"
    assert fetcher.calls == 2


def test_fetch_cache_miss_hit_force_refresh_change_and_diff(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("# Product\n\nOut of stock", '<button id="stock">Out of stock</button>'),
            _outcome("# Product\n\nIn stock", '<button id="stock">In stock</button>'),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(urls=["https://shop.test/product"])

    first = anyio.run(run_fetch, request, deps).results[0]
    cached = anyio.run(run_fetch, request, deps).results[0]
    refreshed = anyio.run(
        run_fetch,
        FetchRequest(urls=request.urls, force_refresh=True),
        deps,
    ).results[0]

    assert first.cache.state == "revalidated"
    assert first.change.state == "new"
    assert cached.cache.state == "fresh"
    assert cached.change.state == "same"
    assert refreshed.cache.state == "revalidated"
    assert refreshed.change.state == "changed"
    assert refreshed.diff.added_lines == ["In stock"]
    assert refreshed.diff.removed_lines == ["Out of stock"]
    assert fetcher.calls == 2


def test_status_transition_to_removed_is_cached_as_tombstone(tmp_path):
    removed = FetchStageOutcome(
        requested_url="https://shop.test/product",
        final_url="https://shop.test/product",
        code=FetchOutcomeCode.UPSTREAM_FAILURE,
        retrieval_method="test_browser",
        elapsed_ms=1,
        status_code=404,
        content_type="text/html",
    )
    fetcher = SequenceFetcher(
        [_outcome("Available", '<button id="stock">Available</button>'), removed]
    )
    deps = _cache_deps(tmp_path, fetcher)

    anyio.run(run_fetch, FetchRequest(urls=["https://shop.test/product"]), deps)
    changed = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/product"], force_refresh=True),
        deps,
    ).results[0]
    cached = anyio.run(
        run_fetch, FetchRequest(urls=["https://shop.test/product"]), deps
    ).results[0]

    assert changed.change.state == "removed"
    assert changed.change.previous_status == 200
    assert changed.change.current_status == 404
    assert cached.cache.state == "fresh"
    assert cached.status_code == 404
    assert fetcher.calls == 2


def test_failed_refresh_does_not_report_change_or_replace_cached_page(tmp_path):
    failure = FetchStageOutcome(
        requested_url="https://shop.test/product",
        final_url=None,
        code=FetchOutcomeCode.UPSTREAM_TIMEOUT,
        retrieval_method="test_browser",
        elapsed_ms=10,
    )
    fetcher = SequenceFetcher(
        [_outcome("Available", "<main>Available</main>"), failure]
    )
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(urls=["https://shop.test/product"])

    anyio.run(run_fetch, request, deps)
    failed = anyio.run(
        run_fetch,
        FetchRequest(urls=request.urls, force_refresh=True),
        deps,
    ).results[0]
    cached = anyio.run(run_fetch, request, deps).results[0]

    assert failed.change is None
    assert failed.diff is None
    assert cached.markdown == "Available"
    assert fetcher.calls == 2


def test_challenge_with_removed_status_is_not_cached_as_tombstone(tmp_path):
    challenge = FetchStageOutcome(
        requested_url="https://shop.test/product",
        final_url="https://shop.test/product",
        code=FetchOutcomeCode.CHALLENGE,
        retrieval_method="test_browser",
        elapsed_ms=1,
        status_code=404,
    )
    fetcher = SequenceFetcher([challenge, challenge])
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(urls=["https://shop.test/product"])

    first = anyio.run(run_fetch, request, deps).results[0]
    second = anyio.run(run_fetch, request, deps).results[0]

    assert first.change is None
    assert second.change is None
    assert fetcher.calls == 2


def test_challenge_and_raw_html_without_operator_opt_in_are_not_cached(tmp_path):
    challenge = FetchStageOutcome(
        requested_url="https://shop.test/product",
        final_url="https://shop.test/product",
        code=FetchOutcomeCode.CHALLENGE,
        retrieval_method="test_browser",
        elapsed_ms=1,
        status_code=403,
    )
    fetcher = SequenceFetcher([challenge, challenge])
    deps = _cache_deps(tmp_path, fetcher)

    first = anyio.run(
        run_fetch, FetchRequest(urls=["https://shop.test/product"]), deps
    ).results[0]
    second = anyio.run(
        run_fetch, FetchRequest(urls=["https://shop.test/product"]), deps
    ).results[0]

    assert first.outcome == second.outcome == "challenge"
    assert fetcher.calls == 2

    raw_fetcher = SequenceFetcher(
        [_outcome("Body", "<main>Body</main>"), _outcome("Body", "<main>Body</main>")]
    )
    raw_deps = _cache_deps(tmp_path / "raw", raw_fetcher)
    raw_request = FetchRequest(
        urls=["https://shop.test/product"], capabilities=["markdown", "raw_html"]
    )
    raw_first = anyio.run(run_fetch, raw_request, raw_deps).results[0]
    raw_second = anyio.run(run_fetch, raw_request, raw_deps).results[0]

    assert raw_first.cache.reason == raw_second.cache.reason == "raw_html_persistence_disabled"
    assert raw_fetcher.calls == 2


def test_raw_html_is_cached_only_with_operator_opt_in(tmp_path):
    fetcher = SequenceFetcher([_outcome("Body", "<main>Body</main>")])
    deps = _cache_deps(
        tmp_path,
        fetcher,
        page_cache_raw_html_enabled=True,
    )
    request = FetchRequest(
        urls=["https://shop.test/product"], capabilities=["markdown", "raw_html"]
    )

    first = anyio.run(run_fetch, request, deps).results[0]
    cached = anyio.run(run_fetch, request, deps).results[0]

    assert first.raw_html == cached.raw_html == "<main>Body</main>"
    assert cached.cache.state == "fresh"
    assert fetcher.calls == 1


def test_stale_while_revalidate_returns_stale_then_updates(monkeypatch, tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("Old", "<main>Old</main>"),
            _outcome("New", "<main>New</main>"),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher, page_cache_ttl_s=10, page_cache_stale_s=20)
    now = [100.0]
    monkeypatch.setattr("orchestrator.fetch_pipeline.time.time", lambda: now[0])
    deps.page_cache._clock = lambda: now[0]
    request = FetchRequest(urls=["https://shop.test/product"])
    cache_key = build_cache_key(
        request.urls[0],
        frozenset(request.capabilities),
        "browser",
        "test@1",
    )[0]

    async def exercise():
        first = await run_fetch(request, deps)
        now[0] = 110.0
        stale = await run_fetch(
            FetchRequest(
                urls=["https://shop.test/product"],
                stale_while_revalidate=True,
            ),
            deps,
        )
        for _ in range(100):
            record = await deps.page_cache.get(cache_key)
            if record is not None and record.markdown == "New":
                break
            await asyncio.sleep(0.001)
        refreshed = await run_fetch(request, deps)
        await deps.page_refresh.aclose()
        return first, stale, refreshed

    first, stale, refreshed = anyio.run(exercise)

    assert first.results[0].markdown == "Old"
    assert stale.results[0].cache.state == "stale"
    assert stale.results[0].cache.reason == "refresh_started"
    assert stale.results[0].markdown == "Old"
    assert refreshed.results[0].cache.state == "fresh"
    assert refreshed.results[0].markdown == "New"
    assert fetcher.calls == 2


def test_cleaner_version_changes_key_while_chunker_changes_do_not(tmp_path):
    class VersionedCleaner(PassthroughCleaner):
        def __init__(self, version):
            self.cleaner_version = version

    fetcher = SequenceFetcher(
        [
            _outcome("One", "<main>One</main>"),
            _outcome("Two", "<main>Two</main>"),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher, markdown_cleaner=VersionedCleaner("cleaner@1"))
    request = FetchRequest(urls=["https://shop.test/product"])

    anyio.run(run_fetch, request, deps)
    deps.chunker = object()
    chunker_changed = anyio.run(run_fetch, request, deps).results[0]
    deps.markdown_cleaner = VersionedCleaner("cleaner@2")
    cleaner_changed = anyio.run(run_fetch, request, deps).results[0]

    assert chunker_changed.cache.state == "fresh"
    assert cleaner_changed.cache.reason == "miss"
    assert cleaner_changed.markdown == "Two"
    assert fetcher.calls == 2


def test_cache_hit_never_bypasses_current_robots_policy(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("Cached", "<main>Cached</main>"),
            _outcome("Live", "<main>Live</main>"),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher, crawl_respect_robots_txt=True)
    origin = "https://shop.test"
    deps.robots_cache.put(
        RobotsSnapshot(
            origin,
            "ThorondorBot",
            "available",
            (),
            (),
            None,
            1_000_000_000_000,
        )
    )

    first = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/product"]),
        deps,
    ).results[0]
    deps.robots_cache.put(
        RobotsSnapshot(
            origin,
            "ThorondorBot",
            "available",
            (RobotsRule(False, "/product"),),
            (),
            None,
            1_000_000_000_000,
        )
    )
    second = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/product"]),
        deps,
    ).results[0]

    assert first.markdown == "Cached"
    assert second.cache.state == "bypass"
    assert second.cache.reason == "robots_policy"
    assert second.markdown == "Live"
    assert fetcher.calls == 2


def test_same_origin_fetches_coalesce_unavailable_robots_lookup(tmp_path):
    class RobotsAndPageFetcher:
        supported_capabilities = SequenceFetcher.supported_capabilities

        def __init__(self):
            self.robots_calls = 0
            self.page_calls = 0

        async def fetch(self, urls, _capabilities, _include_raw_html):
            url = urls[0]
            if url.endswith("/robots.txt"):
                self.robots_calls += 1
                return [
                    FetchStageOutcome(
                        requested_url=url,
                        final_url=url,
                        code=FetchOutcomeCode.UPSTREAM_FAILURE,
                        retrieval_method="test_http",
                        elapsed_ms=1,
                        status_code=404,
                    )
                ]
            self.page_calls += 1
            outcome = _outcome("Body", "<main>Body</main>")
            return [
                replace(
                    outcome,
                    requested_url=url,
                    final_url=url,
                    page=replace(outcome.page, url=url, final_url=url),
                )
            ]

    fetcher = RobotsAndPageFetcher()
    deps = _cache_deps(tmp_path, fetcher, crawl_respect_robots_txt=True)

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/a", "https://shop.test/b"]),
        deps,
    )

    assert [result.outcome for result in response.results] == ["content", "content"]
    assert fetcher.robots_calls == 1
    assert fetcher.page_calls == 2


def test_cache_robots_policy_uses_unescaped_plain_text_body(tmp_path):
    class RobotsAndPageFetcher:
        supported_capabilities = SequenceFetcher.supported_capabilities

        def __init__(self):
            self.calls = 0

        async def fetch(self, urls, _capabilities, _include_raw_html):
            self.calls += 1
            url = urls[0]
            if url.endswith("/robots.txt"):
                page = Page(
                    url,
                    "robots.txt",
                    "User-agent: \\*\nDisallow: /private",
                    html="<pre>User-agent: \\*\nDisallow: /private</pre>",
                    raw_html="<pre>User-agent: *\nDisallow: /private</pre>",
                    content_type="text/plain",
                )
                return [
                    FetchStageOutcome(
                        requested_url=url,
                        final_url=url,
                        code=FetchOutcomeCode.CONTENT,
                        retrieval_method="test_http",
                        elapsed_ms=1,
                        status_code=200,
                        content_type="text/plain",
                        page=page,
                    )
                ]
            outcome = _outcome("Private", "<main>Private</main>")
            return [
                replace(
                    outcome,
                    requested_url=url,
                    final_url=url,
                    page=replace(outcome.page, url=url, final_url=url),
                )
            ]

    fetcher = RobotsAndPageFetcher()
    deps = _cache_deps(tmp_path, fetcher, crawl_respect_robots_txt=True)

    result = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/private"]),
        deps,
    ).results[0]

    assert result.cache.reason == "robots_policy"
    assert anyio.run(deps.page_cache.stats).entries == 0
    assert fetcher.calls == 2


def test_cache_hit_rechecks_current_final_url_policy(tmp_path):
    redirected = _outcome("Cached", "<main>Cached</main>")
    redirected = replace(redirected, final_url="https://cdn.test/product")
    live = _outcome("Live", "<main>Live</main>")
    fetcher = SequenceFetcher([redirected, live])
    deps = _cache_deps(tmp_path, fetcher)
    allowed = {"https://shop.test/product", "https://cdn.test/product"}
    deps.crawl_url_safety = lambda url: url in allowed
    request = FetchRequest(urls=["https://shop.test/product"])

    first = anyio.run(run_fetch, request, deps).results[0]
    allowed.remove("https://cdn.test/product")
    second = anyio.run(run_fetch, request, deps).results[0]
    third = anyio.run(run_fetch, request, deps).results[0]

    assert first.final_url == "https://cdn.test/product"
    assert second.cache.state == "revalidated"
    assert second.cache.reason == "cached_final_url_policy"
    assert second.markdown == "Live"
    assert third.cache.state == "fresh"
    assert third.markdown == "Live"
    assert fetcher.calls == 2


def test_non_markdown_capability_still_keeps_internal_diff_source(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("# Stock\n\nOut", "<main>Out</main>"),
            _outcome("# Stock\n\nIn", "<main>In</main>"),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(
        urls=["https://shop.test/product"],
        capabilities=["links"],
    )

    first = anyio.run(run_fetch, request, deps).results[0]
    refreshed = anyio.run(
        run_fetch,
        FetchRequest(urls=request.urls, capabilities=request.capabilities, force_refresh=True),
        deps,
    ).results[0]

    assert first.markdown is None
    assert refreshed.markdown is None
    assert refreshed.diff.added_lines == ["In"]
    assert refreshed.diff.removed_lines == ["Out"]


def test_conditional_revalidation_reuses_document_on_304(tmp_path):
    class ConditionalFetcher(SequenceFetcher):
        supported_capabilities = frozenset({"markdown"})

        def __init__(self):
            super().__init__([_outcome("Body", "<main>Body</main>", etag='"v1"')])
            self.validators = None

        async def revalidate(
            self, url, _capabilities, etag, last_modified, _include_raw_html
        ):
            self.validators = (etag, last_modified)
            return FetchStageOutcome(
                requested_url=url,
                final_url=url,
                code=FetchOutcomeCode.CONTENT,
                retrieval_method="test_http",
                elapsed_ms=1,
                status_code=304,
            )

    fetcher = ConditionalFetcher()
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(
        urls=["https://shop.test/product"], capabilities=["markdown"]
    )

    anyio.run(run_fetch, request, deps)
    refreshed = anyio.run(
        run_fetch,
        FetchRequest(
            urls=request.urls,
            capabilities=request.capabilities,
            force_refresh=True,
        ),
        deps,
    ).results[0]

    assert refreshed.cache.reason == "not_modified"
    assert refreshed.change.state == "same"
    assert refreshed.markdown == "Body"
    assert fetcher.validators == ('"v1"', None)
    assert fetcher.calls == 1


def test_last_modified_revalidation_accepts_changed_content(tmp_path):
    class ConditionalFetcher(SequenceFetcher):
        supported_capabilities = frozenset({"markdown"})

        def __init__(self):
            initial = _outcome("Old", "<main>Old</main>")
            initial = replace(
                initial,
                last_modified="Wed, 06 Aug 2026 12:00:00 GMT",
                page=replace(
                    initial.page,
                    last_modified="Wed, 06 Aug 2026 12:00:00 GMT",
                ),
            )
            super().__init__([initial])
            self.validators = None

        async def revalidate(
            self, url, _capabilities, etag, last_modified, _include_raw_html
        ):
            self.validators = (etag, last_modified)
            return _outcome("New", "<main>New</main>")

    fetcher = ConditionalFetcher()
    deps = _cache_deps(tmp_path, fetcher)
    request = FetchRequest(urls=["https://shop.test/product"], capabilities=["markdown"])

    anyio.run(run_fetch, request, deps)
    refreshed = anyio.run(
        run_fetch,
        FetchRequest(urls=request.urls, capabilities=request.capabilities, force_refresh=True),
        deps,
    ).results[0]

    assert fetcher.validators == (None, "Wed, 06 Aug 2026 12:00:00 GMT")
    assert refreshed.change.state == "changed"
    assert refreshed.markdown == "New"


def test_304_revalidation_does_not_extend_absolute_retention(monkeypatch, tmp_path):
    class ConditionalFetcher(SequenceFetcher):
        supported_capabilities = frozenset({"markdown"})

        async def revalidate(
            self, url, _capabilities, _etag, _last_modified, _include_raw_html
        ):
            return FetchStageOutcome(
                requested_url=url,
                final_url=url,
                code=FetchOutcomeCode.CONTENT,
                retrieval_method="test_http",
                elapsed_ms=1,
                status_code=304,
            )

    initial = _outcome("Body", "<main>Body</main>", etag='"v1"')
    fetcher = ConditionalFetcher([initial])
    now = [100.0]
    deps = _cache_deps(tmp_path, fetcher, page_cache_retention_s=600)
    deps.page_cache._clock = lambda: now[0]
    monkeypatch.setattr("orchestrator.fetch_pipeline.time.time", lambda: now[0])
    request = FetchRequest(urls=["https://shop.test/product"], capabilities=["markdown"])
    cache_key = build_cache_key(request.urls[0], frozenset(request.capabilities), "http", "test@1")[0]

    anyio.run(run_fetch, request, deps)
    original = anyio.run(deps.page_cache.get, cache_key)
    now[0] = 200.0
    anyio.run(
        run_fetch,
        FetchRequest(urls=request.urls, capabilities=request.capabilities, force_refresh=True),
        deps,
    )
    refreshed = anyio.run(deps.page_cache.get, cache_key)

    assert refreshed.retained_until == original.retained_until == 700.0


def test_ttl_boundary_refreshes_at_exact_expiry(monkeypatch, tmp_path):
    now = [100.0]
    fetcher = SequenceFetcher(
        [_outcome("Old", "<main>Old</main>"), _outcome("New", "<main>New</main>")]
    )
    deps = _cache_deps(tmp_path, fetcher, page_cache_ttl_s=60)
    deps.page_cache._clock = lambda: now[0]
    monkeypatch.setattr("orchestrator.fetch_pipeline.time.time", lambda: now[0])
    request = FetchRequest(urls=["https://shop.test/product"])

    anyio.run(run_fetch, request, deps)
    now[0] = 159.999
    fresh = anyio.run(run_fetch, request, deps).results[0]
    now[0] = 160.0
    refreshed = anyio.run(run_fetch, request, deps).results[0]

    assert fresh.cache.state == "fresh"
    assert refreshed.cache.state == "revalidated"
    assert refreshed.markdown == "New"
    assert fetcher.calls == 2


def test_concurrent_force_refreshes_are_coalesced(tmp_path):
    class BlockingFetcher(SequenceFetcher):
        def __init__(self):
            super().__init__([_outcome("Body", "<main>Body</main>")])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch(self, urls, capabilities, include_raw_html):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return [self.outcomes.pop(0)]

    fetcher = BlockingFetcher()
    deps = _cache_deps(tmp_path, fetcher)
    second_lock_request = asyncio.Event()
    lock_requests = 0

    class ObservedCoordinator(PageRefreshCoordinator):
        def lock_for(self, cache_key):
            nonlocal lock_requests
            lock_requests += 1
            if lock_requests == 2:
                second_lock_request.set()
            return super().lock_for(cache_key)

    coordinator = ObservedCoordinator()
    deps.page_refresh = coordinator
    request = FetchRequest(urls=["https://shop.test/product"], force_refresh=True)

    async def exercise():
        first = asyncio.create_task(run_fetch(request, deps))
        await fetcher.started.wait()
        second = asyncio.create_task(run_fetch(request, deps))
        await second_lock_request.wait()
        fetcher.release.set()
        return await asyncio.gather(first, second)

    responses = anyio.run(exercise)

    assert fetcher.calls == 1
    assert {response.results[0].cache.reason for response in responses} == {
        "miss",
        "coalesced",
    }


def test_background_refresh_factory_is_not_created_twice():
    coordinator = PageRefreshCoordinator()
    release = asyncio.Event()
    created = 0

    async def operation():
        await release.wait()

    def factory():
        nonlocal created
        created += 1
        return operation()

    async def exercise():
        assert coordinator.start_background("key", factory) is True
        assert coordinator.start_background("key", factory) is False
        release.set()
        await coordinator.aclose()

    anyio.run(exercise)

    assert created == 1


def test_background_refresh_uses_fetch_admission_slot(monkeypatch, tmp_path):
    class BlockingRefreshFetcher(SequenceFetcher):
        def __init__(self):
            super().__init__([_outcome("Old", "<main>Old</main>")])
            self.refresh_started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch(self, urls, capabilities, include_raw_html):
            if self.calls == 0:
                return await super().fetch(urls, capabilities, include_raw_html)
            self.calls += 1
            self.refresh_started.set()
            await self.release.wait()
            return [_outcome("New", "<main>New</main>")]

    now = [100.0]
    fetcher = BlockingRefreshFetcher()
    policy = ResourcePolicy(max_inflight_fetches=1, admission_wait_s=1)
    deps = _cache_deps(
        tmp_path,
        fetcher,
        page_cache_ttl_s=10,
        page_cache_stale_s=20,
        resource_policy=policy,
        admission=RuntimeAdmission(policy),
    )
    deps.page_cache._clock = lambda: now[0]
    monkeypatch.setattr("orchestrator.fetch_pipeline.time.time", lambda: now[0])
    request = FetchRequest(urls=["https://shop.test/product"])

    async def exercise():
        await run_fetch(request, deps)
        now[0] = 110.0
        stale = await run_fetch(
            FetchRequest(urls=request.urls, stale_while_revalidate=True),
            deps,
        )
        await fetcher.refresh_started.wait()
        active = deps.admission.active_fetches
        fetcher.release.set()
        await deps.page_refresh.aclose()
        return stale, active

    stale, active = anyio.run(exercise)

    assert stale.results[0].cache.state == "stale"
    assert active == 1


def test_background_refresh_failures_are_logged(caplog):
    coordinator = PageRefreshCoordinator()

    async def operation():
        raise RuntimeError("refresh failed")

    async def exercise():
        coordinator.start_background("key", operation)
        await asyncio.sleep(0)
        await coordinator.aclose()

    with caplog.at_level("ERROR"):
        anyio.run(exercise)

    assert "Background page refresh failed" in caplog.text


def test_sqlite_cache_survives_restart_cleans_retention_and_clears_one_url(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = SqlitePageCache(str(path))
    key, identity = build_cache_key(
        "https://shop.test/product",
        frozenset({"markdown"}),
        "http",
        "cleaner@1",
    )
    now = time.time()
    record = PageCacheRecord(
        cache_key=key,
        url_identity=identity,
        requested_url=identity,
        final_url=identity,
        outcome="content",
        status_code=200,
        content_type="text/html",
        title="Product",
        retrieval_method="http",
        capabilities=("markdown",),
        markdown="Body",
        raw_html=None,
        links={},
        metadata={},
        response_headers={},
        source_hash="a" * 64,
        cleaner_version="cleaner@1",
        fetched_at=now,
        expires_at=now + 10,
        retained_until=now + 20,
    )

    snapshot = StoredTargetSnapshot("found", "Out", {"data-state": "sold-out"}, now)
    anyio.run(cache.put_with_target, record, "locator", snapshot)
    restarted = SqlitePageCache(str(path))
    assert anyio.run(restarted.get, key) == record
    assert anyio.run(restarted.get_target, key, "locator") == snapshot
    assert anyio.run(restarted.stats).entries == 1
    assert anyio.run(restarted.cleanup, now + 19) == 0
    assert anyio.run(restarted.cleanup, now + 20) == 1
    assert anyio.run(restarted.stats).entries == 0

    anyio.run(restarted.put, replace(record, retained_until=now + 60))
    assert anyio.run(restarted.clear_url, identity) == 1


def test_expired_records_and_targets_are_removed_on_read(tmp_path):
    now = [100.0]
    cache = SqlitePageCache(str(tmp_path / "cache.sqlite3"), clock=lambda: now[0])
    key, identity = build_cache_key(
        "https://shop.test/product",
        frozenset({"markdown"}),
        "http",
        "cleaner@1",
    )
    record = PageCacheRecord(
        cache_key=key,
        url_identity=identity,
        requested_url=identity,
        final_url=identity,
        outcome="content",
        status_code=200,
        content_type="text/html",
        title="Product",
        retrieval_method="http",
        capabilities=("markdown",),
        markdown="Body",
        raw_html=None,
        links={},
        metadata={},
        response_headers={},
        source_hash="a" * 64,
        cleaner_version="cleaner@1",
        fetched_at=90,
        expires_at=95,
        retained_until=100,
    )
    snapshot = StoredTargetSnapshot("found", "Out", {}, 90)
    anyio.run(cache.put_with_target, record, "watch", snapshot)

    assert anyio.run(cache.get, key) is None
    assert anyio.run(cache.get_target, key, "watch") is None
    assert anyio.run(cache.stats).entries == 0


def test_retention_worker_removes_untouched_records(tmp_path):
    cache = SqlitePageCache(str(tmp_path / "cache.sqlite3"))
    key, identity = build_cache_key(
        "https://shop.test/product",
        frozenset({"markdown"}),
        "http",
        "cleaner@1",
    )
    now = time.time()
    record = PageCacheRecord(
        cache_key=key,
        url_identity=identity,
        requested_url=identity,
        final_url=identity,
        outcome="content",
        status_code=200,
        content_type="text/html",
        title="Product",
        retrieval_method="http",
        capabilities=("markdown",),
        markdown="Body",
        raw_html=None,
        links={},
        metadata={},
        response_headers={},
        source_hash="a" * 64,
        cleaner_version="cleaner@1",
        fetched_at=now,
        expires_at=now,
        retained_until=now + 0.02,
    )

    async def exercise():
        await cache.start()
        await cache.put(record)
        for _ in range(100):
            with sqlite3.connect(cache.path) as connection:
                count = connection.execute("SELECT COUNT(*) FROM page_cache").fetchone()[0]
            if count == 0:
                break
            await asyncio.sleep(0.005)
        await cache.aclose()
        return count

    assert anyio.run(exercise) == 0


def test_sqlite_cache_recovers_invalid_rows_and_corrupt_database(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = SqlitePageCache(str(path))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO page_cache VALUES (?, ?, ?, ?, ?, ?)",
            ("bad", "https://a.test/", "not-json", 1, 2, 3),
        )
    assert anyio.run(cache.get, "bad") is None

    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_path.write_bytes(b"not a sqlite database")
    recovered = SqlitePageCache(str(corrupt_path))

    assert anyio.run(recovered.stats).entries == 0
    assert list(tmp_path.glob("corrupt.corrupt-*"))


def test_sqlite_cache_initializes_schema_and_rejects_future_version(tmp_path):
    path = tmp_path / "cache.sqlite3"
    SqlitePageCache(str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(RuntimeError, match="newer than supported"):
        SqlitePageCache(str(path))


def test_default_disabled_cache_creates_no_database(tmp_path):
    path = tmp_path / "disabled.sqlite3"
    deps = fakes.deps()
    fetcher = SequenceFetcher([_outcome("Body", "<main>Body</main>")])
    deps.extractor = fetcher
    deps.crawl_url_safety = lambda _url: True

    result = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://shop.test/product"]),
        deps,
    ).results[0]

    assert result.cache.state == "bypass"
    assert result.cache.reason == "disabled"
    assert not path.exists()
    assert conservative_url_identity("https://www.shop.test") != conservative_url_identity(
        "https://shop.test"
    )


def test_default_search_never_accesses_page_cache(tmp_path):
    class ForbiddenPageCache:
        enabled = False

        def __getattr__(self, name):
            raise AssertionError(f"search accessed page cache through {name}")

    path = tmp_path / "search-cache.sqlite3"
    deps = fakes.deps(page_cache=ForbiddenPageCache())

    response = anyio.run(run_search, SearchRequest(query="current evidence"), deps)

    assert response.passages
    assert not path.exists()
