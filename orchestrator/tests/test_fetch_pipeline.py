from dataclasses import replace

import anyio

from orchestrator import fakes
from orchestrator.clients.structured_extractor import StructuredModelResult
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


class StaticStructuredExtractor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def extract(self, markdown, schema):
        self.calls.append((markdown, schema))
        return self.result


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


def test_fetch_returns_source_addressed_structured_profiles():
    html = (
        '<a href="/next">Next</a>'
        "<table><tr><th>Item</th><th>Stock</th></tr><tr><td>Widget</td><td>Out</td></tr></table>"
    )
    outcome = _content_outcome()
    outcome = replace(
        outcome,
        page=replace(outcome.page, html=html, raw_html=html),
    )
    fetcher = RecordingFetcher([outcome])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown", "links"],
            structured_formats=["links", "tables"],
        ),
        deps,
    )

    result = response.results[0]
    assert result.cache.reason == "structured_source_required"
    assert [item.format for item in result.structured] == ["links", "tables"]
    assert result.structured[0].links[0].url == "https://a.test/next"
    assert result.structured[1].tables[0].rows[1][1].text == "Out"
    assert result.structured[0].document_id == result.structured[1].document_id
    assert fetcher.calls[0][2] is True


def test_fetch_returns_schema_validated_fields_with_exact_markdown_evidence():
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    model = StaticStructuredExtractor(
        StructuredModelResult(
            data={"title": "Article"},
            evidence={"/title": "# Article"},
            prompt_bytes=200,
            output_bytes=80,
            input_tokens=30,
            output_tokens=10,
        )
    )
    fetcher = RecordingFetcher([_content_outcome()])
    deps = fakes.deps(extractor=fetcher, structured_extractor=model)
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "ok"
    assert extracted.data == {"title": "Article"}
    assert extracted.fields[0].path == "/title"
    assert extracted.fields[0].source is not None
    source = extracted.fields[0].source
    assert response.results[0].markdown[source.start_index : source.end_index] == "# Article"
    assert source.document_id == extracted.document_id
    assert extracted.model_usage.input_tokens == 30
    assert model.calls == [("# Article\n\nUseful body.", schema)]
    assert fetcher.calls[0][2] is False


def test_fetch_rejects_null_schema_leaves_without_raising():
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    model = StaticStructuredExtractor(
        StructuredModelResult(
            data={"title": None},
            evidence={"/title": "# Article"},
        )
    )
    deps = fakes.deps(
        extractor=RecordingFetcher([_content_outcome()]),
        structured_extractor=model,
    )
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "validation_failed"
    assert [(item.path, item.code) for item in extracted.validation_failures] == [
        ("/title", "type_mismatch")
    ]
    assert extracted.data is None
    assert extracted.fields == []


def test_fetch_rejects_evidence_that_does_not_contain_its_value():
    schema = {
        "type": "object",
        "properties": {"price": {"type": "string"}},
        "required": ["price"],
    }
    model = StaticStructuredExtractor(
        StructuredModelResult(
            data={"price": "1.00"},
            evidence={"/price": "Useful body"},
        )
    )
    deps = fakes.deps(
        extractor=RecordingFetcher([_content_outcome()]),
        structured_extractor=model,
    )
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "validation_failed"
    assert [(item.path, item.code) for item in extracted.validation_failures] == [
        ("/price", "evidence_value_mismatch")
    ]
    assert extracted.data is None
    assert extracted.fields == []


def test_fetch_reports_schema_and_evidence_failures_without_trusting_model_output():
    schema = {
        "type": "object",
        "properties": {"stock": {"type": "integer"}},
        "required": ["stock"],
    }
    model = StaticStructuredExtractor(
        StructuredModelResult(
            data={"stock": "available", "injected": "secret"},
            evidence={"/stock": "not in the page"},
        )
    )
    deps = fakes.deps(
        extractor=RecordingFetcher([_content_outcome()]),
        structured_extractor=model,
    )
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "validation_failed"
    assert {(item.path, item.code) for item in extracted.validation_failures} == {
        ("/injected", "additional_property"),
        ("/injected", "evidence_missing"),
        ("/stock", "type_mismatch"),
        ("/stock", "evidence_not_found"),
    }
    assert extracted.data is None
    assert extracted.fields == []


def test_fetch_uses_page_final_url_for_all_structured_source_identity():
    outcome = _content_outcome()
    html = '<a href="/next">Next</a>'
    outcome = replace(
        outcome,
        final_url=None,
        page=replace(outcome.page, html=html, raw_html=html),
    )
    deps = fakes.deps(extractor=RecordingFetcher([outcome]))
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown", "links"],
            structured_formats=["links"],
        ),
        deps,
    )

    result = response.results[0]
    assert result.final_url == "https://a.test/final"
    assert result.structured[0].links[0].source.final_url == "https://a.test/final"


def test_fetch_trims_structured_payloads_to_the_response_budget():
    rows = "".join(
        "<tr>" + "".join(f"<td>value-{row}-{cell}</td>" for cell in range(10)) + "</tr>"
        for row in range(100)
    )
    html = f"<table>{rows}</table>"
    urls = [f"https://a.test/article-{index}" for index in range(4)]
    outcomes = []
    for url in urls:
        outcome = _content_outcome(url)
        outcomes.append(
            replace(
                outcome,
                page=replace(outcome.page, html=html, raw_html=html),
            )
        )
    deps = fakes.deps(extractor=RecordingFetcher(outcomes))
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()
    deps.resource_policy = replace(
        deps.resource_policy,
        max_response_body_bytes=100_000,
        max_content_bytes=90_000,
    )

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=urls,
            capabilities=["markdown"],
            structured_formats=["tables"],
        ),
        deps,
    )

    assert all(result.structured[0].status == "truncated" for result in response.results)
    assert all(result.structured[0].omitted_items > 0 for result in response.results)
    assert len(response.model_dump_json().encode("utf-8")) <= 100_000


def test_fetch_reports_unavailable_schema_model_without_claiming_source_data():
    schema = {"type": "object", "properties": {}}
    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        fakes.deps(extractor=RecordingFetcher([_content_outcome()])),
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "unsupported"
    assert extracted.document_id is None
    assert extracted.validation_failures[0].code == "model_unavailable"


def test_fetch_caps_schema_leaf_fields_even_when_model_output_is_bounded():
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["items"],
    }
    data = {"items": [f"value-{index}" for index in range(40)]}
    evidence = {f"/items/{index}": "Useful body" for index in range(40)}
    model = StaticStructuredExtractor(
        StructuredModelResult(data=data, evidence=evidence)
    )
    deps = fakes.deps(
        extractor=RecordingFetcher([_content_outcome()]),
        structured_extractor=model,
    )
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "validation_failed"
    assert extracted.data is None
    assert extracted.fields == []
    assert any(
        failure.code == "field_limit_exceeded"
        for failure in extracted.validation_failures
    )


def test_fetch_contains_unexpected_schema_model_failures():
    class BrokenStructuredExtractor:
        async def extract(self, _markdown, _schema):
            raise RuntimeError("provider failed")

    schema = {"type": "object", "properties": {}}
    deps = fakes.deps(
        extractor=RecordingFetcher([_content_outcome()]),
        structured_extractor=BrokenStructuredExtractor(),
    )
    deps.crawl_url_safety = lambda _url: True
    deps.markdown_cleaner = PassthroughCleaner()

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        ),
        deps,
    )

    extracted = response.results[0].structured[0]
    assert extracted.status == "model_failed"
    assert extracted.validation_failures[0].code == "model_error"


def test_failed_fetch_never_extracts_or_addresses_challenge_content():
    outcome = replace(
        _content_outcome(),
        code=FetchOutcomeCode.CHALLENGE,
    )
    fetcher = RecordingFetcher([outcome])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True

    response = anyio.run(
        run_fetch,
        FetchRequest(
            urls=["https://a.test/article"],
            capabilities=["markdown", "links"],
            structured_formats=["links", "tables"],
        ),
        deps,
    )

    assert response.results[0].outcome == "challenge"
    assert all(item.status == "unsupported" for item in response.results[0].structured)
    assert all(item.document_id is None for item in response.results[0].structured)
    assert all(not item.links and not item.tables for item in response.results[0].structured)


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


def test_fetch_exposes_only_the_allowlisted_retry_after_header():
    outcome = replace(_content_outcome(), retry_after="120")
    fetcher = RecordingFetcher([outcome])
    deps = fakes.deps(extractor=fetcher)
    deps.crawl_url_safety = lambda _url: True

    response = anyio.run(
        run_fetch,
        FetchRequest(urls=["https://a.test/article"]),
        deps,
    )

    assert response.results[0].response_headers == {
        "content-type": "text/html; charset=utf-8",
        "retry-after": "120",
    }


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
