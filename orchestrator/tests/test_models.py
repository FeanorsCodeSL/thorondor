import pytest
from pydantic import ValidationError

from orchestrator.models import SearchRequest, SearchStats


def test_search_request_defaults():
    req = SearchRequest(query="x")
    assert req.decompose is True
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


def test_search_stats_defaults():
    stats = SearchStats()
    assert stats.reranked is False
    assert stats.reason is None
    assert stats.sub_queries == []
