import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.models import (
    MAX_QUERY_CHARS,
    MAX_SELECTED_URLS,
    MAX_SITE_URL_BYTES,
    MAX_TOKEN_BUDGET,
    FetchOutcomeCount,
    MapRequest,
    Passage,
    SearchRequest,
    SearchResponse,
    SearchStats,
    SiteStats,
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
