from dataclasses import replace

import anyio
import anyio.lowlevel
import pytest

from orchestrator import fakes
from orchestrator.models import CrawlRequest, MapRequest
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.resource_policy import RouteDeadlineExceeded
from orchestrator.site_pipeline import run_crawl, run_crawl_job, run_map
from orchestrator.types import (
    DiscoveryEngineFailure,
    DiscoveryOutcome,
    DiscoveryResult,
    FetchStageOutcome,
    Page,
)


def _content(
    url,
    *,
    final_url=None,
    body="body",
    html=None,
    links=None,
    status=200,
    content_type="text/html",
):
    final = final_url or url
    page = Page(
        url=url,
        title=final,
        markdown=body,
        html=body if html is None else html,
        requested_url=url,
        final_url=final,
        status_code=status,
        content_type=content_type,
        links=links or {},
    )
    return FetchStageOutcome(
        requested_url=url,
        final_url=final,
        code=FetchOutcomeCode.CONTENT,
        retrieval_method="fixture_browser",
        elapsed_ms=1,
        status_code=status,
        content_type=content_type,
        title=final,
        links=links or {},
        page=page,
    )


def _failure(url, status=404):
    return FetchStageOutcome(
        requested_url=url,
        final_url=url,
        code=FetchOutcomeCode.UPSTREAM_FAILURE,
        retrieval_method="fixture_browser",
        elapsed_ms=1,
        status_code=status,
    )


class SiteExtractor:
    supported_capabilities = frozenset(
        {"markdown", "javascript", "links", "metadata", "raw_html", "pdf", "document"}
    )

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def fetch(self, urls, capabilities, include_raw_html):
        self.calls.append((list(urls), frozenset(capabilities), include_raw_html))
        return [self.outcomes.get(url, _failure(url)) for url in urls]


def _site_deps(overrides=None):
    seed = "https://example.com/docs/start"
    robots = "https://example.com/robots.txt"
    sitemap = "https://example.com/sitemap.xml"
    sitemap_index = "https://example.com/sitemap_index.xml"
    outcomes = {
        seed: _content(
            seed,
            body="# Start",
            links={
                "internal": [
                    {"href": "/docs/linked"},
                    {"href": "/private/hidden"},
                ]
            },
        ),
        robots: _content(
            robots,
            body="User-agent: ThorondorBot\nDisallow: /private\nSitemap: /sitemap.xml",
        ),
        sitemap: _content(
            sitemap,
            body="""<urlset>
<url><loc>https://example.com/docs/sitemap-page</loc><lastmod>2026-08-04</lastmod></url>
<url><loc>https://example.com/private/hidden</loc></url>
</urlset>""",
        ),
        sitemap_index: _failure(sitemap_index),
        "https://example.com/docs/linked": _content(
            "https://example.com/docs/linked",
            body="# Linked",
        ),
        "https://example.com/docs/sitemap-page": _content(
            "https://example.com/docs/sitemap-page",
            body="# Sitemap page",
        ),
    }
    outcomes.update(overrides or {})
    extractor = SiteExtractor(outcomes)
    deps = fakes.deps(extractor=extractor)
    deps.crawl_url_safety = lambda url: "unsafe" not in url
    return deps, extractor


def test_map_fuses_sitemap_and_bfs_with_robots_and_terminal_states():
    deps, extractor = _site_deps()

    response = anyio.run(
        run_map,
        MapRequest(
            url="https://example.com/docs/start",
            max_pages=3,
            max_depth=2,
        ),
        deps,
    )

    records = {record.url: record for record in response.urls}
    assert response.schema_version == "thorondor.map.v1"
    assert response.outcome == "completed"
    assert response.effective_url == "https://example.com/docs/start"
    assert records["https://example.com/docs/start"].states[-1] == "fetched"
    assert records["https://example.com/docs/sitemap-page"].sources == ["sitemap"]
    assert records["https://example.com/docs/sitemap-page"].modified_at == "2026-08-04"
    assert records["https://example.com/docs/linked"].sources == ["link"]
    assert records["https://example.com/private/hidden"].reason in {
        "outside_seed_path",
        "robots_refused",
    }
    assert response.stats.sitemap_documents == 1
    assert response.stats.pages_succeeded == 3
    assert response.stats.robots_source == "network"
    assert all("unsafe" not in call[0][0] for call in extractor.calls)


def test_site_pipeline_parses_browser_wrapped_robots_and_sitemap_documents():
    seed = "https://example.com/docs/start"
    robots = "https://example.com/robots.txt"
    sitemap = "https://example.com/sitemap.xml"
    outcomes = {
        seed: _content(seed, body="# Start"),
        robots: _content(
            robots,
            body="```\nUser-agent: *\nDisallow: /docs/private\nSitemap: /sitemap.xml\n```",
            html=(
                '<html><body><pre style="white-space: pre-wrap;">'
                "User-agent: *\nDisallow: /docs/private\nSitemap: /sitemap.xml"
                "</pre></body></html>"
            ),
            content_type="text/plain",
        ),
        sitemap: _content(
            sitemap,
            body="https://example.com/docs/from-sitemap",
            html=(
                '<html><body><div id="webkit-xml-viewer-source-xml">'
                "<urlset><url><loc>https://example.com/docs/from-sitemap</loc></url>"
                "<url><loc>https://example.com/docs/private/hidden</loc></url></urlset>"
                "</div></body></html>"
            ),
            content_type="application/xml",
        ),
        "https://example.com/sitemap_index.xml": _failure(
            "https://example.com/sitemap_index.xml"
        ),
        "https://example.com/docs/from-sitemap": _content(
            "https://example.com/docs/from-sitemap"
        ),
    }
    deps = fakes.deps(extractor=SiteExtractor(outcomes))

    response = anyio.run(
        run_map,
        MapRequest(url=seed, sitemap="only", max_pages=2),
        deps,
    )

    records = {record.url: record for record in response.urls}
    assert "https://example.com/docs/from-sitemap" in records
    assert records["https://example.com/docs/private/hidden"].reason == "robots_refused"
    assert response.stats.sitemap_documents == 1


def test_sitemap_only_does_not_admit_page_links():
    deps, _extractor = _site_deps()

    response = anyio.run(
        run_map,
        MapRequest(
            url="https://example.com/docs/start",
            sitemap="only",
            max_pages=3,
        ),
        deps,
    )

    assert "https://example.com/docs/linked" not in {record.url for record in response.urls}
    assert "https://example.com/docs/sitemap-page" in {
        record.url for record in response.urls
    }


def test_site_operation_reports_when_robots_snapshot_comes_from_cache():
    deps, extractor = _site_deps()
    request = MapRequest(
        url="https://example.com/docs/start",
        sitemap="skip",
        max_pages=1,
    )

    first = anyio.run(run_map, request, deps)
    second = anyio.run(run_map, request, deps)

    robots_url = "https://example.com/robots.txt"
    assert first.stats.robots_source == "network"
    assert second.stats.robots_source == "cache"
    assert [call[0][0] for call in extractor.calls].count(robots_url) == 1


def test_crawl_returns_typed_page_results_and_shared_map_diagnostics():
    deps, _extractor = _site_deps()

    response = anyio.run(
        run_crawl,
        CrawlRequest(
            url="https://example.com/docs/start",
            max_pages=2,
            capabilities=["markdown", "links", "metadata"],
        ),
        deps,
    )

    assert response.schema_version == "thorondor.crawl.v1"
    assert len(response.results) == 2
    assert all(result.outcome == "content" for result in response.results)
    assert all(result.markdown for result in response.results)
    assert response.stats.pages_succeeded == 2
    assert response.outcome == "limit_reached"


def test_async_crawl_observer_reuses_safety_robots_and_typed_results():
    async def exercise():
        deps, extractor = _site_deps()
        results = []
        failures = []

        async def on_result(item):
            results.append(item)

        async def on_failure(url, reason):
            failures.append((url, reason))

        response = await run_crawl_job(
            CrawlRequest(
                url="https://example.com/docs/start",
                max_pages=3,
                capabilities=["markdown", "links", "metadata"],
            ),
            deps,
            on_result,
            on_failure,
            lambda: False,
            120,
        )

        assert response.outcome == "completed"
        assert [item.final_url for item in results] == [
            "https://example.com/docs/start",
            "https://example.com/docs/sitemap-page",
            "https://example.com/docs/linked",
        ]
        assert failures == []
        assert all(item.provenance == "external_web" for item in results)
        assert all(item.trust == "untrusted" for item in results)
        calls = [call[0][0] for call in extractor.calls]
        assert "https://example.com/private/hidden" not in calls
        assert all("unsafe" not in url for url in calls)

    anyio.run(exercise)


def test_async_crawl_observer_emits_final_target_rejections():
    async def exercise():
        linked = "https://example.com/docs/linked"
        deps, _extractor = _site_deps(
            {
                linked: _content(
                    linked,
                    final_url="https://example.com/docs/unsafe-final",
                )
            }
        )
        failures = []

        async def on_failure(url, reason):
            failures.append((url, reason))

        response = await run_crawl_job(
            CrawlRequest(
                url="https://example.com/docs/start",
                sitemap="skip",
                max_pages=2,
            ),
            deps,
            lambda _result: anyio.lowlevel.checkpoint(),
            on_failure,
            lambda: False,
            120,
        )

        assert response.outcome == "partial"
        assert failures == [(linked, "unsafe_redirect")]

    anyio.run(exercise)


def test_async_crawl_job_uses_its_own_attempt_deadline():
    class SlowExtractor(SiteExtractor):
        async def fetch(self, urls, capabilities, include_raw_html):
            await anyio.sleep(1)
            return await super().fetch(urls, capabilities, include_raw_html)

    async def exercise():
        deps, extractor = _site_deps()
        deps.extractor = SlowExtractor(extractor.outcomes)
        with pytest.raises(RouteDeadlineExceeded):
            await run_crawl_job(
                CrawlRequest(url="https://example.com/docs/start", max_pages=2),
                deps,
                lambda _result: anyio.lowlevel.checkpoint(),
                lambda _url, _reason: anyio.lowlevel.checkpoint(),
                lambda: False,
                0.001,
            )

    anyio.run(exercise)


def test_failed_page_attempts_consume_the_page_limit():
    first = "https://example.com/docs/first"
    second = "https://example.com/docs/second"
    seed = "https://example.com/docs/start"
    deps, extractor = _site_deps(
        {
            seed: _content(
                seed,
                body="# Start",
                links={"internal": [{"href": first}, {"href": second}]},
            ),
            first: _failure(first, status=503),
            second: _content(second, body="# Second"),
        }
    )

    response = anyio.run(
        run_map,
        MapRequest(url=seed, sitemap="skip", max_pages=2),
        deps,
    )

    records = {record.url: record for record in response.urls}
    calls = [call[0][0] for call in extractor.calls]
    assert response.outcome == "limit_reached"
    assert records[first].states[-1] == "failed"
    assert records[second].states[-1] == "cancelled"
    assert second not in calls


def test_site_page_limit_is_clamped_to_internal_fanout():
    deps, extractor = _site_deps()
    deps.resource_policy = replace(
        deps.resource_policy,
        max_internal_fanout=1,
    )

    response = anyio.run(
        run_map,
        MapRequest(
            url="https://example.com/docs/start",
            sitemap="skip",
            max_pages=3,
        ),
        deps,
    )

    assert response.outcome == "limit_reached"
    assert "max_pages_clamped_to_internal_fanout" in response.warnings
    assert "https://example.com/docs/linked" not in [
        call[0][0] for call in extractor.calls
    ]


def test_seed_redirect_rehomes_scope_and_robots_origin():
    requested = "https://www.example.com/old/start"
    effective = "https://docs.example.com/guide/start"
    outcomes = {
        requested: _content(requested, final_url=effective, body="# Effective"),
        "https://docs.example.com/robots.txt": _content(
            "https://docs.example.com/robots.txt",
            body="User-agent: *\nAllow: /",
        ),
        "https://docs.example.com/sitemap.xml": _failure(
            "https://docs.example.com/sitemap.xml"
        ),
        "https://docs.example.com/sitemap_index.xml": _failure(
            "https://docs.example.com/sitemap_index.xml"
        ),
    }
    extractor = SiteExtractor(outcomes)
    deps = fakes.deps(extractor=extractor)
    deps.crawl_url_safety = lambda _url: True

    response = anyio.run(
        run_map,
        MapRequest(url=requested, sitemap="skip", max_pages=1),
        deps,
    )

    assert response.requested_origin == "https://www.example.com"
    assert response.effective_origin == "https://docs.example.com"
    assert response.effective_url == effective
    assert [call[0][0] for call in extractor.calls] == [
        requested,
        "https://docs.example.com/robots.txt",
    ]


def test_unsafe_seed_fails_without_target_dispatch():
    deps, extractor = _site_deps()
    deps.crawl_url_safety = lambda _url: False

    response = anyio.run(
        run_map,
        MapRequest(url="http://127.0.0.1/admin"),
        deps,
    )

    assert response.outcome == "unsafe_seed"
    assert extractor.calls == []


def test_unsafe_seed_redirect_has_matching_terminal_reason():
    requested = "https://example.com/docs/start"
    final = "http://127.0.0.1/admin"
    deps, _extractor = _site_deps(
        {requested: _content(requested, final_url=final)}
    )
    deps.crawl_url_safety = lambda url: url != final

    response = anyio.run(
        run_map,
        MapRequest(url=requested, sitemap="skip", max_pages=1),
        deps,
    )

    assert response.outcome == "unsafe_redirect"
    assert response.urls[0].states[-1] == "failed"
    assert response.urls[0].reason == "unsafe_redirect"
    assert {
        item.outcome: item.count for item in response.stats.fetch_outcomes
    } == {"unsafe_redirect": 1}


def test_optional_search_augmentation_has_independent_source_diagnostic():
    class SiteDiscovery:
        async def search(self, query, freshness=None):
            assert query == "site:example.com"
            assert freshness is None
            return DiscoveryOutcome(
                [
                    DiscoveryResult(
                        "Search page",
                        "https://example.com/docs/search-page",
                        "",
                        "fixture",
                        1,
                    )
                ],
                [],
            )

    search_url = "https://example.com/docs/search-page"
    deps, _extractor = _site_deps({search_url: _content(search_url, body="# Search")})
    deps.discovery = SiteDiscovery()

    response = anyio.run(
        run_map,
        MapRequest(
            url="https://example.com/docs/start",
            sitemap="skip",
            include_search=True,
            max_pages=2,
        ),
        deps,
    )

    record = next(item for item in response.urls if item.url == search_url)
    assert record.sources == ["search"]
    assert {item.source: item.count for item in response.stats.source_counts}["search"] == 1


def test_optional_search_augmentation_reports_degraded_discovery():
    class DegradedSiteDiscovery:
        async def search(self, _query, freshness=None):
            assert freshness is None
            return DiscoveryOutcome(
                [],
                [DiscoveryEngineFailure("fixture", "upstream_status_error")],
            )

    deps, _extractor = _site_deps()
    deps.discovery = DegradedSiteDiscovery()

    response = anyio.run(
        run_map,
        MapRequest(
            url="https://example.com/docs/start",
            sitemap="skip",
            include_search=True,
            max_pages=1,
        ),
        deps,
    )

    assert "search_augmentation_degraded" in response.warnings


def test_crawl_stats_reflect_local_processing_failure():
    class BrokenCleaner:
        def clean(self, _page):
            raise TypeError("broken cleaner")

    seed = "https://example.com/docs/start"
    deps, _extractor = _site_deps({seed: _content(seed, body="# Start")})
    deps.markdown_cleaner = BrokenCleaner()

    response = anyio.run(
        run_crawl,
        CrawlRequest(
            url="https://example.com/docs/start",
            sitemap="skip",
            max_pages=1,
            capabilities=["markdown", "links", "metadata"],
        ),
        deps,
    )

    assert response.outcome == "partial"
    assert response.results[0].outcome == "local_processing_failure"
    assert response.stats.pages_succeeded == 0
    assert response.stats.pages_failed == 1
    assert {
        item.outcome: item.count for item in response.stats.fetch_outcomes
    } == {"local_processing_failure": 1}


def test_non_seed_redirect_outside_scope_is_not_returned_or_traversed():
    source = "https://example.com/docs/linked"
    final = "https://outside.example/landing"
    deps, extractor = _site_deps(
        {
            source: _content(
                source,
                final_url=final,
                body="# Outside",
                links={"internal": [{"href": "https://outside.example/next"}]},
            )
        }
    )

    response = anyio.run(
        run_crawl,
        CrawlRequest(
            url="https://example.com/docs/start",
            sitemap="skip",
            max_pages=3,
            capabilities=["markdown", "links", "metadata"],
        ),
        deps,
    )

    record = next(item for item in response.urls if item.url == source)
    assert record.states[-1] == "failed"
    assert record.reason == "outside_origin"
    assert all(result.final_url != final for result in response.results)
    assert "https://outside.example/robots.txt" not in [
        call[0][0] for call in extractor.calls
    ]
    assert "https://outside.example/next" not in {
        item.url for item in response.urls
    }


def test_sitemap_indexes_are_cycle_safe_and_preserve_partial_outcomes():
    seed = "https://example.com/docs/start"
    index = "https://example.com/index.xml"
    nested = "https://example.com/nested.xml"
    redirected_sitemap = "https://outside.example/sitemap.xml"
    outcomes = {
        seed: _content(seed, body="# Start"),
        "https://example.com/robots.txt": _content(
            "https://example.com/robots.txt",
            body=(
                "User-agent: ThorondorBot\n"
                "Disallow: /private\n"
                "Sitemap: /index.xml"
            ),
        ),
        index: _content(
            index,
            body=(
                "<sitemapindex>"
                f"<sitemap><loc>{index}</loc></sitemap>"
                f"<sitemap><loc>{nested}</loc></sitemap>"
                "</sitemapindex>"
            ),
        ),
        nested: _content(
            nested,
            body="""<urlset>
<url><loc>https://example.com/docs/good</loc></url>
<url><loc>https://example.com/docs/fail</loc></url>
<url><loc>https://example.com/private/hidden</loc></url>
<url><loc>https://example.com/docs/unsafe</loc></url>
</urlset>""",
        ),
        "https://example.com/sitemap.xml": _content(
            "https://example.com/sitemap.xml",
            final_url=redirected_sitemap,
            body="<urlset/>",
        ),
        "https://example.com/sitemap_index.xml": _failure(
            "https://example.com/sitemap_index.xml"
        ),
        "https://example.com/docs/good": _content(
            "https://example.com/docs/good",
            body="# Good",
        ),
        "https://example.com/docs/fail": _failure(
            "https://example.com/docs/fail",
            status=503,
        ),
    }
    extractor = SiteExtractor(outcomes)
    deps = fakes.deps(extractor=extractor)
    deps.crawl_url_safety = lambda url: "unsafe" not in url

    response = anyio.run(
        run_map,
        MapRequest(url=seed, sitemap="only", max_pages=4),
        deps,
    )

    records = {record.url: record for record in response.urls}
    calls = [call[0][0] for call in extractor.calls]
    assert response.outcome == "partial"
    assert response.stats.sitemap_documents_attempted == 4
    assert response.stats.sitemap_documents == 2
    assert calls.count(index) == 1
    assert calls.count(nested) == 1
    assert records["https://example.com/docs/good"].states[-1] == "fetched"
    assert records["https://example.com/docs/fail"].states[-1] == "failed"
    assert records["https://example.com/private/hidden"].reason in {
        "outside_seed_path",
        "robots_refused",
    }
    assert records["https://example.com/docs/unsafe"].reason == "unsafe_target"
    assert "sitemap_redirect_outside_scope" in response.warnings
    assert "https://outside.example/robots.txt" not in calls


def test_failed_sitemap_fetches_consume_the_document_limit():
    seed = "https://example.com/docs/start"
    declared = "https://example.com/declared.xml"
    outcomes = {
        seed: _content(seed, body="# Start"),
        "https://example.com/robots.txt": _content(
            "https://example.com/robots.txt",
            body=f"User-agent: *\nAllow: /\nSitemap: {declared}",
        ),
        declared: _failure(declared, status=503),
        "https://example.com/sitemap.xml": _failure(
            "https://example.com/sitemap.xml",
            status=503,
        ),
        "https://example.com/sitemap_index.xml": _failure(
            "https://example.com/sitemap_index.xml",
            status=503,
        ),
    }
    extractor = SiteExtractor(outcomes)
    deps = fakes.deps(extractor=extractor)
    deps.max_sitemap_documents = 2

    response = anyio.run(
        run_map,
        MapRequest(url=seed, sitemap="only", max_pages=1),
        deps,
    )

    calls = [call[0][0] for call in extractor.calls]
    assert response.stats.sitemap_documents_attempted == 2
    assert response.stats.sitemap_documents == 0
    assert declared in calls
    assert "https://example.com/sitemap.xml" in calls
    assert "https://example.com/sitemap_index.xml" not in calls
    assert "sitemap_document_limit_reached" in response.warnings
    assert "sitemap_upstream_failure" in response.warnings


def test_sitemap_candidate_checks_are_bounded_before_network_dispatch():
    seed = "https://example.com/docs/start"
    sitemap_index = "https://example.com/sitemap.xml"
    nested = [f"https://sub-{index}.example.com/sitemap.xml" for index in range(6)]
    outcomes = {
        seed: _content(seed, body="# Start"),
        "https://example.com/robots.txt": _content(
            "https://example.com/robots.txt",
            body="User-agent: *\nAllow: /",
        ),
        sitemap_index: _content(
            sitemap_index,
            body=(
                "<sitemapindex>"
                + "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in nested)
                + "</sitemapindex>"
            ),
        ),
        "https://example.com/sitemap_index.xml": _failure(
            "https://example.com/sitemap_index.xml"
        ),
    }
    extractor = SiteExtractor(outcomes)
    deps = fakes.deps(extractor=extractor)
    deps.resource_policy = replace(deps.resource_policy, max_internal_fanout=3)

    response = anyio.run(
        run_map,
        MapRequest(
            url=seed,
            sitemap="only",
            max_pages=1,
            include_subdomains=True,
        ),
        deps,
    )

    assert "sitemap_candidate_limit_reached" in response.warnings
    assert len(
        {
            url.split("/", 3)[2]
            for call in extractor.calls
            for url in call[0]
            if "sub-" in url
        }
    ) <= 1


def test_robots_fetches_are_bounded_by_internal_fanout():
    seed = "https://example.com/docs/start"
    subdomain = "https://sub.example.com/docs/page"
    deps, extractor = _site_deps(
        {
            seed: _content(
                seed,
                body="# Start",
                links={"internal": [{"href": subdomain}]},
            )
        }
    )
    deps.resource_policy = replace(deps.resource_policy, max_internal_fanout=1)

    response = anyio.run(
        run_map,
        MapRequest(
            url=seed,
            sitemap="skip",
            max_pages=2,
            include_subdomains=True,
        ),
        deps,
    )

    calls = [call[0][0] for call in extractor.calls]
    record = next(item for item in response.urls if item.url == subdomain)
    assert "https://sub.example.com/robots.txt" not in calls
    assert record.reason == "robots_refused"
    assert response.stats.robots_documents_attempted == 1
    assert "robots_document_limit_reached" in response.warnings


def test_non_http_links_do_not_consume_the_discovery_budget_or_reach_the_response():
    seed = "https://example.com/docs/start"
    retained = "https://example.com/docs/retained"
    deps, _extractor = _site_deps(
        {
            seed: _content(
                seed,
                body="# Start",
                links={
                    "internal": [
                        {"href": "mailto:test@example.com"},
                        {"href": "javascript:alert(1)"},
                        {"href": retained},
                    ]
                },
            )
        }
    )

    response = anyio.run(
        run_map,
        MapRequest(
            url=seed,
            sitemap="skip",
            max_pages=1,
            max_discovered_urls=2,
        ),
        deps,
    )

    assert retained in {record.url for record in response.urls}
    assert all(record.url.startswith("https://") for record in response.urls)
    assert response.stats.non_http_urls_skipped == 2


def test_discovery_limit_stops_safety_work_for_new_candidates():
    seed = "https://example.com/docs/start"
    links = [f"https://example.com/docs/{value}" for value in ("one", "two", "three")]
    deps, _extractor = _site_deps(
        {
            seed: _content(
                seed,
                body="# Start",
                links={"internal": [{"href": link} for link in links]},
            )
        }
    )
    safety_calls = []

    def safety(url):
        safety_calls.append(url)
        return True

    deps.crawl_url_safety = safety

    response = anyio.run(
        run_map,
        MapRequest(
            url=seed,
            sitemap="skip",
            max_pages=1,
            max_discovered_urls=2,
        ),
        deps,
    )

    assert response.stats.omitted == 2
    assert links[0] in safety_calls
    assert links[1] not in safety_calls
    assert links[2] not in safety_calls


def test_site_response_envelopes_omit_items_deterministically():
    seed = "https://example.com/docs/start"
    links = [f"/docs/{index}-" + "x" * 4000 for index in range(12)]
    deps, _extractor = _site_deps(
        {
            seed: _content(
                seed,
                body="y" * 80000,
                links={"internal": [{"href": link} for link in links]},
            )
        }
    )
    deps.resource_policy = replace(
        deps.resource_policy,
        max_response_body_bytes=65536,
        max_content_bytes=65536,
    )

    mapped = anyio.run(
        run_map,
        MapRequest(
            url=seed,
            sitemap="skip",
            max_pages=1,
            max_discovered_urls=20,
        ),
        deps,
    )
    crawled = anyio.run(
        run_crawl,
        CrawlRequest(
            url=seed,
            sitemap="skip",
            max_pages=1,
            capabilities=["markdown", "links", "metadata"],
        ),
        deps,
    )

    assert mapped.stats.omitted > 0
    assert mapped.stats.discovery_limit_omitted == 0
    assert mapped.stats.response_budget_omitted == mapped.stats.omitted
    assert "url_response_budget_reached" in mapped.warnings
    assert crawled.stats.results_omitted == 1
    assert crawled.results == []
    assert "result_response_budget_reached" in crawled.warnings
    assert len(mapped.model_dump_json().encode("utf-8")) <= 65536
    assert len(crawled.model_dump_json().encode("utf-8")) <= 65536
