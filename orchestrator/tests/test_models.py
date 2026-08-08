import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import orchestrator.models as models_module
from orchestrator.models import (
    MAX_QUERY_CHARS,
    MAX_SELECTED_URLS,
    MAX_SITE_URL_BYTES,
    MAX_TOKEN_BUDGET,
    CrawlRequest,
    FetchOutcomeCount,
    FetchRequest,
    MapRequest,
    Passage,
    SearchRequest,
    SearchResponse,
    SearchStats,
    SiteStats,
    StructuredExtraction,
    StructuredLink,
    StructuredSourceReference,
)
from orchestrator.outcome_codes import FetchOutcomeCode


def test_search_request_defaults():
    req = SearchRequest(query="x")
    assert req.decompose is True
    assert req.search_profile is None
    assert req.token_budget is None
    assert req.max_urls is None
    assert req.max_passages is None
    assert req.freshness is None
    assert req.domains is None
    assert req.exclude_domains is None
    assert req.include_raw_markdown is False


def test_invalid_freshness_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", freshness="hour")


def test_invalid_search_profile_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", search_profile="wide")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "x" * (MAX_QUERY_CHARS + 1)},
        {"query": "x", "token_budget": 0},
        {"query": "x", "token_budget": MAX_TOKEN_BUDGET + 1},
        {"query": "x", "max_urls": 0},
        {"query": "x", "max_urls": MAX_SELECTED_URLS + 1},
        {"query": "x", "max_passages": 0},
    ],
)
def test_search_request_bounds_reject_invalid_values(kwargs):
    with pytest.raises(ValidationError):
        SearchRequest(**kwargs)


def test_site_request_enforces_utf8_url_byte_limit():
    prefix = "https://example.com/"
    within_limit = prefix + "x" * (MAX_SITE_URL_BYTES - len(prefix))
    over_limit = prefix + "é" * ((MAX_SITE_URL_BYTES - len(prefix)) // 2 + 1)

    assert len(within_limit.encode("utf-8")) == MAX_SITE_URL_BYTES
    assert MapRequest(url=within_limit).url == within_limit
    with pytest.raises(ValidationError, match="UTF-8 bytes"):
        MapRequest(url=over_limit)


def test_structured_formats_are_closed_unique_and_capability_checked():
    assert FetchRequest(
        urls=["https://example.com"],
        capabilities=["markdown", "links"],
        structured_formats=["links", "tables"],
    ).structured_formats == ["links", "tables"]
    assert CrawlRequest(
        url="https://example.com",
        capabilities=["markdown", "links"],
        structured_formats=["links", "tables"],
    ).structured_formats == ["links", "tables"]

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        FetchRequest(
            urls=["https://example.com"],
            capabilities=["links"],
            structured_formats=["links", "links"],
        )
    with pytest.raises(ValidationError, match="requires the links capability"):
        FetchRequest(
            urls=["https://example.com"],
            capabilities=["markdown"],
            structured_formats=["links"],
        )
    with pytest.raises(ValidationError, match="requires the markdown capability"):
        CrawlRequest(
            url="https://example.com",
            capabilities=["links"],
            structured_formats=["tables"],
        )
    with pytest.raises(ValidationError):
        FetchRequest(
            urls=["https://example.com"],
            structured_formats=["json"],
        )


def test_json_ld_and_json_schema_request_contracts_are_bounded():
    schema = {
        "type": "object",
        "properties": {"stock": {"type": "string"}},
        "required": ["stock"],
    }
    request = FetchRequest(
        urls=["https://example.com"],
        capabilities=["markdown", "metadata"],
        structured_formats=["json_ld", "json_schema"],
        extraction_schema=schema,
    )
    assert request.extraction_schema == schema

    with pytest.raises(ValidationError, match="supplied together"):
        FetchRequest(
            urls=["https://example.com"],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
        )
    with pytest.raises(ValidationError, match="supplied together"):
        FetchRequest(
            urls=["https://example.com"],
            extraction_schema=schema,
        )
    with pytest.raises(ValidationError, match="requires the metadata capability"):
        FetchRequest(
            urls=["https://example.com"],
            capabilities=["markdown"],
            structured_formats=["json_ld"],
        )
    with pytest.raises(ValidationError, match="max_pages"):
        CrawlRequest(
            url="https://example.com",
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        )


def test_fetch_enforces_schema_url_limit_independently(monkeypatch):
    monkeypatch.setattr(models_module, "MAX_EXTRACTION_URLS", 2)
    schema = {"type": "object", "properties": {}}

    with pytest.raises(ValidationError, match="fetch urls"):
        FetchRequest(
            urls=[
                "https://one.test",
                "https://two.test",
                "https://three.test",
            ],
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema=schema,
        )


def test_structured_payloads_cannot_claim_mismatched_or_unsupported_sources():
    source = StructuredSourceReference(
        document_id="1" * 64,
        cleaned_markdown_sha256="2" * 64,
        source_html_sha256="3" * 64,
        final_url="https://example.com",
        start_index=0,
        end_index=1,
    )
    link = StructuredLink(
        url="https://example.com/next",
        text="Next",
        kind="internal",
        source=source,
    )

    with pytest.raises(ValidationError, match="cannot claim source data"):
        StructuredExtraction(
            format="links",
            status="unsupported",
            document_id="1" * 64,
            links=[link],
        )
    with pytest.raises(ValidationError, match="requires document_id"):
        StructuredExtraction(format="links", status="empty")
    with pytest.raises(ValidationError, match="must match document_id"):
        StructuredExtraction(
            format="links",
            status="ok",
            document_id="4" * 64,
            links=[link],
        )
    conflicting = link.model_copy(
        update={
            "source": source.model_copy(update={"source_html_sha256": "5" * 64})
        }
    )
    with pytest.raises(ValidationError, match="must identify one source"):
        StructuredExtraction(
            format="links",
            status="ok",
            document_id="1" * 64,
            links=[link, conflicting],
        )


def test_schema_extraction_status_and_data_invariants_fail_closed():
    with pytest.raises(ValidationError, match="only json_schema"):
        StructuredExtraction(
            format="links",
            status="model_failed",
            document_id="1" * 64,
        )
    with pytest.raises(ValidationError, match="requires valid data"):
        StructuredExtraction(
            format="json_schema",
            status="ok",
            document_id="1" * 64,
        )
    with pytest.raises(ValidationError, match="requires validation failures"):
        StructuredExtraction(
            format="json_schema",
            status="validation_failed",
            document_id="1" * 64,
            data={},
        )
    with pytest.raises(ValidationError, match="bounded JSON"):
        StructuredExtraction(
            format="json_schema",
            status="validation_failed",
            document_id="1" * 64,
            data={"value": float("nan")},
            validation_failures=[{"path": "/value", "code": "type_mismatch"}],
        )


def test_site_stats_accepts_every_closed_fetch_outcome():
    outcomes = [
        FetchOutcomeCount(outcome=outcome.value, count=1)
        for outcome in FetchOutcomeCode
    ]

    stats = SiteStats(
        discovered=0,
        admitted=0,
        queued=0,
        fetched=0,
        filtered=0,
        failed=0,
        cancelled=0,
        omitted=0,
        results_omitted=0,
        pages_succeeded=0,
        pages_failed=0,
        sitemap_documents_attempted=0,
        sitemap_documents=0,
        sitemap_entries=0,
        sitemap_truncated=0,
        robots_documents_attempted=0,
        non_http_urls_skipped=0,
        fetch_outcomes=outcomes,
        elapsed_ms=0,
    )

    assert len(stats.fetch_outcomes) == len(FetchOutcomeCode)


def test_search_stats_defaults():
    stats = SearchStats()
    assert stats.reranked is False
    assert stats.reason is None
    assert stats.sub_queries == []
    assert stats.discovery_status == "ok"
    assert stats.unresponsive_engines == []


def test_reason_codes_are_closed():
    stats = SearchStats(reason="no_urls_after_selection")
    stats.reason = "all_crawls_failed"
    stats.reason = "search_provider_unavailable"

    with pytest.raises(ValidationError):
        SearchStats(reason="not_a_reason")

    with pytest.raises(ValidationError):
        stats.reason = "not_a_reason"


def test_search_response_is_versioned():
    resp = SearchResponse(query="x", passages=[], citations=[], stats=SearchStats())
    assert resp.schema_version == "thorondor.search.v1"


def test_passages_are_labeled_external_untrusted():
    passage = SearchResponse.model_validate({
        "query": "x",
        "passages": [{"text": "body", "score": 1.0, "token_count": 1, "citation_id": 1}],
        "citations": [{"id": 1, "url": "https://a.test", "title": "A"}],
        "stats": {},
    }).passages[0]

    assert passage.provenance == "external_web"
    assert passage.trust == "untrusted"


def test_non_verbatim_passages_cannot_claim_evidence_ids():
    with pytest.raises(ValidationError):
        Passage(
            text="rewritten",
            score=1.0,
            token_count=1,
            citation_id=1,
            verbatim=False,
            document_id="1" * 64,
            evidence_id="2" * 64,
        )


def test_search_response_schema_matches_golden():
    golden = json.loads((Path(__file__).parent / "golden" / "search-response-schema.json").read_text())

    assert SearchResponse.model_json_schema() == golden
