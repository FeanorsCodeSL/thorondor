import anyio
import pytest

from orchestrator import fakes
from orchestrator.clients.chunker_client import ChunkerUnavailable
from orchestrator.clients.searxng_client import DiscoveryUnavailable
from orchestrator.models import SearchRequest
from orchestrator.pipeline import SearchDependencyUnavailable, run_search


def test_happy_path_returns_cited_budgeted_passages():
    deps = fakes.deps()
    resp = anyio.run(run_search, SearchRequest(query="eu ai act 2025"), deps)
    assert resp.passages and resp.citations
    assert resp.stats.reranked is True
    assert sum(p.token_count for p in resp.passages) <= 4000
    assert all(p.citation_id in {c.id for c in resp.citations} for p in resp.passages)


def test_no_discovery_returns_reason():
    deps = fakes.deps(discovery=fakes.EmptyDiscovery())
    resp = anyio.run(run_search, SearchRequest(query="x"), deps)
    assert resp.passages == [] and resp.stats.reason == "no_results_from_discovery"


def test_reranker_down_degrades_not_fails():
    deps = fakes.deps(reranker=fakes.DownReranker())
    resp = anyio.run(run_search, SearchRequest(query="x"), deps)
    assert resp.stats.reranked is False and resp.passages


def test_all_crawls_failed_reason():
    deps = fakes.deps(extractor=fakes.EmptyExtractor())
    resp = anyio.run(run_search, SearchRequest(query="x"), deps)
    assert resp.stats.reason == "all_crawls_failed"


def test_exclude_domains_merge_with_blocklist():
    deps = fakes.deps(blocklist={"a.test"})
    resp = anyio.run(run_search, SearchRequest(query="x", exclude_domains=["b.test"]), deps)
    assert resp.passages == []
    assert resp.stats.reason == "no_results_from_discovery"


def test_domains_allowlist_limits_selection():
    resp = anyio.run(run_search, SearchRequest(query="x", domains=["b.test"]), fakes.deps())
    assert resp.citations
    assert all("b.test" in citation.url for citation in resp.citations)


def test_freshness_passed_to_discovery():
    discovery = fakes.FakeDiscovery()
    anyio.run(run_search, SearchRequest(query="x", freshness="week"), fakes.deps(discovery=discovery))
    assert discovery.freshness_seen == ["week"]


def test_custom_planner_called_when_decompose_true():
    planner = fakes.FakePlanner()
    anyio.run(run_search, SearchRequest(query="x", decompose=True), fakes.deps(planner=planner))
    assert planner.called is True


def test_raw_markdown_attached_for_returned_citations_only():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", domains=["a.test"], include_raw_markdown=True),
        fakes.deps(),
    )
    assert resp.raw_markdown
    assert {r.citation_id for r in resp.raw_markdown} == {c.id for c in resp.citations}


class _DiscoveryDown:
    async def search(self, subquery: str, freshness: str | None = None):
        raise DiscoveryUnavailable("down")


class _ChunkerDown:
    async def chunk(self, pages):
        raise ChunkerUnavailable("down")


def test_discovery_unavailable_raises_dependency_error():
    with pytest.raises(SearchDependencyUnavailable) as exc:
        anyio.run(run_search, SearchRequest(query="x"), fakes.deps(discovery=_DiscoveryDown()))
    assert exc.value.dependency == "searxng"


def test_chunker_unavailable_raises_dependency_error():
    with pytest.raises(SearchDependencyUnavailable) as exc:
        anyio.run(run_search, SearchRequest(query="x"), fakes.deps(chunker=_ChunkerDown()))
    assert exc.value.dependency == "chunker"
