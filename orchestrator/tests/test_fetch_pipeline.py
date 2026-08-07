from dataclasses import replace

import anyio

from orchestrator import fakes
from orchestrator.fetch_pipeline import run_fetch
from orchestrator.models import FetchRequest
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.types import FetchStageOutcome, Page


class RecordingFetcher:
    supported_capabilities = frozenset(
        {"markdown", "javascript", "links", "metadata", "raw_html", "pdf", "document"}
    )

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def fetch(self, urls, capabilities, include_raw_html):
        self.calls.append((urls, capabilities, include_raw_html))
        return self.outcomes


class EmptyCleaner:
    def clean(self, page):
        return type("Cleaned", (), {"page": replace(page, markdown="")})()


class PassthroughCleaner:
    def clean(self, page):
        return type("Cleaned", (), {"page": page})()


class BrokenCleaner:
    def clean(self, _page):
        raise TypeError("broken cleaner")


def _content_outcome(url="https://a.test/article"):
    return FetchStageOutcome(
        requested_url=url,
        final_url="https://a.test/final",
        code=FetchOutcomeCode.CONTENT,
        status_code=200,
        content_type="text/html; charset=utf-8",
        title="Article",
        links={"internal": [{"href": "/next"}]},
        metadata={"language": "en"},
        retrieval_method="crawl4ai_browser",
        elapsed_ms=12,
        page=Page(
            url,
            "Article",
            "# Article\n\nUseful body.",
            html="<article>Useful body.</article>",
            final_url="https://a.test/final",
            status_code=200,
            content_type="text/html; charset=utf-8",
            links={"internal": [{"href": "/next"}]},
            metadata={"language": "en"},
        ),
    )


def test_fetch_returns_typed_content_and_aggregate_counts():
    fetcher = RecordingFetcher([_content_outcome()])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown", "links", "metadata", "raw_html"],
        ),
        deps,
    )

    assert response.schema_version == "thorondor.fetch.v1"
    assert response.stats.requested == 1
    assert response.stats.succeeded == 1
    assert response.stats.failed == 0
    assert response.stats.outcomes[0].outcome == "content"
    result = response.results[0]
    assert result.requested_url == "https://a.test/article"
    assert result.final_url == "https://a.test/final"
    assert result.markdown == "# Article\n\nUseful body."
    assert result.raw_html == "<article>Useful body.</article>"
    assert result.links == {"internal": [{"href": "/next"}]}
    assert result.metadata == {"language": "en"}
    assert result.response_headers == {
        "content-type": "text/html; charset=utf-8"
    }
    assert fetcher.calls == [
        (
            ["https://a.test/article"],
            frozenset({"markdown", "links", "metadata", "raw_html"}),
            True,
        )
    ]


def test_fetch_falls_back_to_crawl4ai_markdown_when_html_cleaning_is_empty():
    outcome = _content_outcome()
    fetcher = RecordingFetcher([outcome])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = EmptyCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://a.test/article"]),
        deps,
    )

    assert response.results[0].outcome == "content"
    assert response.results[0].markdown == "# Article\n\nUseful body."


def test_fetch_rejects_unsafe_targets_without_dispatch():
    fetcher = RecordingFetcher([])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: False

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["http://127.0.0.1/admin"]),
        deps,
    )

    assert fetcher.calls == []
    assert response.results[0].outcome == "unsafe_target"
    assert response.stats.succeeded == 0
    assert response.stats.failed == 1


def test_fetch_reports_unsupported_capability_without_dispatch():
    fetcher = RecordingFetcher([_content_outcome()])
    fetcher.supported_capabilities = frozenset({"markdown"})
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://a.test/article"], capabilities=["pdf"]),
        deps,
    )

    assert fetcher.calls == []
    assert response.results[0].outcome == "unsupported_capability"


def test_fetch_preserves_terminal_failure_reason():
    fetcher = RecordingFetcher(
        [
            FetchStageOutcome(
                requested_url="https://a.test/article",
                final_url=None,
                code=FetchOutcomeCode.UPSTREAM_TIMEOUT,
                retrieval_method="crawl4ai_browser",
                elapsed_ms=50,
            )
        ]
    )
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://a.test/article"]),
        deps,
    )

    assert response.results[0].outcome == "upstream_timeout"
    assert response.results[0].retryable is True
    assert response.stats.outcomes[0].outcome == "upstream_timeout"
    assert response.stats.outcomes[0].count == 1


def test_fetch_maps_cleaner_failure_to_local_processing_outcome():
    fetcher = RecordingFetcher([_content_outcome()])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = BrokenCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://a.test/article"]),
        deps,
    )

    result = response.results[0]
    assert result.outcome == "local_processing_failure"
    assert result.retryable is False
    assert result.markdown is None
