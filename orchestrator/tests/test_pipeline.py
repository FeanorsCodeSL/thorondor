import asyncio
import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from orchestrator import fakes, pipeline
from orchestrator.clients.chunker_client import ChunkerUnavailable
from orchestrator.clients.reranker_client import RerankerClient
from orchestrator.clients.searxng_client import DiscoveryUnavailable, SearxngDiscovery
from orchestrator.models import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_RAW_MARKDOWN_ITEM_BYTES,
    MAX_URL_DIAGNOSTICS_BYTES,
    RawMarkdown,
    SearchRequest,
    SearchStats,
)
from orchestrator.pipeline import SearchDependencyUnavailable, run_search
from orchestrator.prefilter import CandidatePrefilterImpl
from orchestrator.resource_policy import ResourcePolicy
from orchestrator.types import (
    Chunk,
    DiscoveryContribution,
    DiscoveryEngineFailure,
    DiscoveryOutcome,
    DiscoveryResult,
    Page,
    FetchStageOutcome,
    RerankerTelemetry,
    RerankOutcome,
    ScoredChunk,
)
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.url_safety import UrlSafetyPolicy, filter_safe_discovery_results
from orchestrator.url_identity import build_document_identity, evidence_id_for


class _StaticDiscovery:
    def __init__(self, urls: list[str]):
        self.urls = urls

    async def search(self, subquery: str, freshness: str | None = None):
        return DiscoveryOutcome(
            [
                DiscoveryResult(f"T{index}", url, "snip", "fake", 1.0 - (index * 0.01))
                for index, url in enumerate(self.urls)
            ],
            [],
        )


class _RecordingExtractor:
    def __init__(self):
        self.attempted_urls: list[str] = []

    async def extract(self, urls: list[str]) -> list[Page]:
        self.attempted_urls.extend(urls)
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in urls]


class _CountingDiscovery:
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, subquery: str, freshness: str | None = None):
        self.queries.append(subquery)
        return DiscoveryOutcome(
            [DiscoveryResult(subquery, f"https://{subquery}.test/article", "snip", "fake", 1.0)],
            [],
        )


class _OutcomeDiscovery:
    def __init__(self, results, failures):
        self.results = results
        self.failures = failures

    async def search(self, subquery: str, freshness: str | None = None):
        return DiscoveryOutcome(
            results=self.results,
            unresponsive_engines=[
                DiscoveryEngineFailure(engine=engine, reason=reason)
                for engine, reason in self.failures
            ],
        )


class _ManyPlanner:
    async def plan(self, query: str) -> list[str]:
        return [f"q{index}" for index in range(50)]


def test_shared_fanout_caps_subqueries_and_selected_urls():
    discovery = _CountingDiscovery()
    extractor = _RecordingExtractor()
    deps = fakes.deps(
        planner=_ManyPlanner(),
        discovery=discovery,
        extractor=extractor,
        max_subqueries=8,
        resource_policy=ResourcePolicy(max_internal_fanout=2),
    )

    anyio.run(run_search, SearchRequest(query="x", max_urls=20), deps)

    assert discovery.queries == ["q0", "q1"]
    assert len(extractor.attempted_urls) == 2


def test_planner_stage_timeout_falls_back_to_original_query_and_cancels_work():
    cancelled = asyncio.Event()
    discovery = _CountingDiscovery()

    class BlockingPlanner:
        async def plan(self, _query):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(
        planner=BlockingPlanner(),
        discovery=discovery,
        resource_policy=ResourcePolicy(discovery_stage_deadline_s=0.01),
    )

    response = anyio.run(run_search, SearchRequest(query="x"), deps)

    assert response.passages
    assert discovery.queries == ["x"]
    assert cancelled.is_set()


def test_discovery_stage_timeout_is_attributed_and_cancels_work():
    cancelled = asyncio.Event()

    class BlockingDiscovery:
        async def search(self, _subquery, _freshness=None):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(
        discovery=BlockingDiscovery(),
        resource_policy=ResourcePolicy(discovery_stage_deadline_s=0.01),
    )

    with pytest.raises(SearchDependencyUnavailable) as exc_info:
        anyio.run(run_search, SearchRequest(query="x"), deps)

    assert exc_info.value.dependency == "searxng"
    assert exc_info.value.reason == "searxng_unavailable"
    assert cancelled.is_set()


def test_crawl_stage_timeout_returns_per_url_deadline_outcome_and_cancels_work():
    cancelled = asyncio.Event()

    class BlockingFetcher:
        async def fetch(self, _urls, _capabilities, _include_raw_html):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(
        discovery=_StaticDiscovery(["https://a.test/article"]),
        extractor=BlockingFetcher(),
        resource_policy=ResourcePolicy(crawl_stage_deadline_s=0.01),
    )

    response = anyio.run(run_search, SearchRequest(query="x"), deps)

    assert response.stats.reason == "all_crawls_failed"
    assert response.stats.fetch_outcomes[0].outcome == "deadline_cancelled"
    assert response.stats.fetch_outcomes[0].retryable is True
    assert cancelled.is_set()


def test_chunk_stage_timeout_is_attributed_and_cancels_work():
    cancelled = asyncio.Event()

    class BlockingChunker:
        async def chunk(self, _pages):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(
        discovery=_StaticDiscovery(["https://a.test/article"]),
        chunker=BlockingChunker(),
        resource_policy=ResourcePolicy(chunk_stage_deadline_s=0.01),
    )

    with pytest.raises(SearchDependencyUnavailable) as exc_info:
        anyio.run(run_search, SearchRequest(query="x"), deps)

    assert exc_info.value.dependency == "chunker"
    assert exc_info.value.reason == "chunker_timeout"
    assert cancelled.is_set()


def test_rerank_stage_timeout_uses_position_fallback_and_cancels_work():
    cancelled = asyncio.Event()

    class BlockingReranker:
        async def rerank(self, _query, _chunks):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(
        discovery=_StaticDiscovery(["https://a.test/article"]),
        reranker=BlockingReranker(),
        resource_policy=ResourcePolicy(rerank_stage_deadline_s=0.01),
    )

    response = anyio.run(run_search, SearchRequest(query="x"), deps)

    assert response.passages
    assert response.stats.reranked is False
    assert response.passages[0].score_components[0].name == "position_fallback"
    assert cancelled.is_set()


class _LongUnicodePlanner:
    async def plan(self, query: str) -> list[str]:
        return ["界" * 500]


class _SinglePageExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(urls[0], "one", "unique markdown")] if urls else []


class _DuplicateBodyExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(url, url, "same markdown body") for url in urls]


class _ChromeExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [
            Page(
                urls[0],
                "chrome",
                "Navigation Menu\nSearch\n# Article\nThis is the article body about the query.",
                html="# Article\nThis is the article body about the query.",
            )
        ]


class _FallbackChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return [
            Chunk(
                text=page.markdown,
                token_count=len(page.markdown.split()),
                source_url=page.url,
                title=page.title,
                position=0,
                source_id=page.source_id,
                chunk_strategy="cluster-semantic-greedy-token",
                embedding_degraded=True,
            )
            for page in pages
        ]


class _RecordingChunker:
    def __init__(self):
        self.texts: list[str] = []

    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        self.texts.extend(page.markdown for page in pages)
        return [
            Chunk(
                text=page.markdown,
                token_count=len(page.markdown.split()),
                source_url=page.url,
                title=page.title,
                position=0,
                source_id=page.source_id,
            )
            for page in pages
        ]


class _ManyChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in pages:
            for position in range(10):
                text = "Oppenheimer was born in New York City" if position == 3 else f"filler {position}"
                chunks.append(
                    Chunk(
                        text=text,
                        token_count=len(text.split()),
                        source_url=page.url,
                        title=page.title,
                        position=position,
                        source_id=page.source_id,
                    )
                )
        return chunks


class _FixedChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return [
            Chunk("strong answer", 2, pages[0].url, pages[0].title, 0, pages[0].source_id),
            Chunk("zero score", 2, pages[0].url, pages[0].title, 1, pages[0].source_id),
            Chunk("negative score", 2, pages[0].url, pages[0].title, 2, pages[0].source_id),
        ]


class _MixedScoreReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return [
            ScoredChunk(chunks[0], 1.2),
            ScoredChunk(chunks[1], 0.0),
            ScoredChunk(chunks[2], -0.4),
        ]


class _AllNegativeReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return [ScoredChunk(chunk, -1.0 - index) for index, chunk in enumerate(chunks)]


def _mock_resolver(monkeypatch, mapping: dict[str, list[str]] | None = None):
    from orchestrator import url_safety

    addresses = mapping or {"safe.example": ["93.184.216.34"]}

    def resolve(host: str):
        if host not in addresses:
            raise OSError(host)
        return [url_safety.ipaddress.ip_address(address) for address in addresses[host]]

    monkeypatch.setattr(url_safety, "resolve_host_ips", resolve)


def _url_safety():
    from orchestrator import url_safety

    policy = UrlSafetyPolicy(
        blocked_ip_categories={
            "loopback",
            "link_local",
            "private",
            "reserved",
            "multicast",
            "unspecified",
        },
        blocked_special_ips={
            url_safety.ipaddress.ip_address("169.254.169.254"),
            url_safety.ipaddress.ip_address("fd00:ec2::254"),
        },
        nat64_networks=[url_safety.ipaddress.ip_network("64:ff9b::/96")],
        six_to_four_networks=[url_safety.ipaddress.ip_network("2002::/16")],
        ipv4_compat_networks=[url_safety.ipaddress.ip_network("::/96")],
    )
    return lambda results: filter_safe_discovery_results(results, policy)


def test_happy_path_returns_cited_budgeted_passages():
    deps = fakes.deps()
    resp = anyio.run(run_search, SearchRequest(query="eu ai act 2025"), deps)
    assert resp.passages and resp.citations
    assert resp.stats.reranked is True
    assert sum(p.token_count for p in resp.passages) <= 4000
    assert all(p.citation_id in {c.id for c in resp.citations} for p in resp.passages)
    assert {p.provenance for p in resp.passages} == {"external_web"}
    assert {p.trust for p in resp.passages} == {"untrusted"}
    assert all(p.score_components[-1].score == p.score for p in resp.passages)


def test_search_profile_supplies_defaults_when_request_omits_explicit_caps():
    discovery = _StaticDiscovery([f"https://{index}.test/article" for index in range(15)])

    resp = anyio.run(
        run_search,
        SearchRequest(query="x", search_profile="research"),
        fakes.deps(discovery=discovery, relevance_score_floor=None),
    )

    assert resp.stats.urls_selected == 12
    assert len(resp.passages) == 12
    assert resp.stats.tokens_returned <= 8000


def test_explicit_caps_override_search_profile_defaults():
    discovery = _StaticDiscovery([f"https://{index}.test/article" for index in range(15)])

    resp = anyio.run(
        run_search,
        SearchRequest(
            query="x",
            search_profile="research",
            max_urls=3,
            max_passages=2,
            token_budget=10,
        ),
        fakes.deps(discovery=discovery),
    )

    assert resp.stats.urls_selected == 3
    assert len(resp.passages) == 2
    assert resp.stats.tokens_returned <= 10


def test_importable_fake_selector_and_assembler_can_isolate_pipeline_branches():
    deps = fakes.deps(selector=fakes.FakeSelector(), assembler=fakes.FakeAssembler())
    resp = anyio.run(run_search, SearchRequest(query="x", max_urls=1), deps)

    assert len(resp.citations) == 1
    assert resp.passages[0].citation_id == resp.citations[0].id


def test_partial_extractor_and_chunker_are_network_free_modes():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(extractor=fakes.PartialExtractor(), chunker=fakes.PartialChunker()),
    )

    assert resp.passages
    assert 0 < resp.stats.urls_crawled_ok < resp.stats.urls_selected
    assert 0 < resp.stats.chunks_produced <= resp.stats.urls_crawled_ok


def test_fakes_cover_all_protocol_seams_for_down_empty_partial_modes():
    expected = {
        "planner": (fakes.DownPlanner, fakes.EmptyPlanner, fakes.PartialPlanner),
        "discovery": (fakes.DownDiscovery, fakes.EmptyDiscovery, fakes.PartialDiscovery),
        "selector": (fakes.DownSelector, fakes.EmptySelector, fakes.FakeSelector),
        "extractor": (fakes.DownExtractor, fakes.EmptyExtractor, fakes.PartialExtractor),
        "chunker": (fakes.DownChunker, fakes.EmptyChunker, fakes.PartialChunker),
        "reranker": (fakes.DownReranker, fakes.EmptyReranker, fakes.PartialReranker),
        "assembler": (fakes.DownAssembler, fakes.EmptyAssembler, fakes.PartialAssembler),
        "candidate_prefilter": (
            fakes.DownCandidatePrefilter,
            fakes.EmptyCandidatePrefilter,
            fakes.PartialCandidatePrefilter,
        ),
    }

    assert all(all(item is not None for item in variants) for variants in expected.values())


def test_no_discovery_returns_reason():
    deps = fakes.deps(discovery=fakes.EmptyDiscovery())
    resp = anyio.run(run_search, SearchRequest(query="x"), deps)
    assert resp.passages == [] and resp.stats.reason == "no_results_from_discovery"
    assert resp.stats.discovery_status == "ok"
    assert resp.stats.unresponsive_engines == []


def test_partial_engine_failure_returns_results_as_degraded():
    discovery = _OutcomeDiscovery(
        [DiscoveryResult("A", "https://a.test/article", "snip", "bing", 1.0)],
        [("mojeek", "access denied")],
    )

    resp = anyio.run(run_search, SearchRequest(query="x"), fakes.deps(discovery=discovery))

    assert resp.passages
    assert resp.stats.reason is None
    assert resp.stats.discovery_status == "degraded"
    assert [item.model_dump() for item in resp.stats.unresponsive_engines] == [
        {"engine": "mojeek", "reason": "access denied"}
    ]


class _TwoQueryPlanner:
    async def plan(self, query: str) -> list[str]:
        return ["working query", "failed query"]


class _PartiallyUnavailableDiscovery:
    async def search(self, subquery: str, freshness: str | None = None):
        if subquery == "failed query":
            raise DiscoveryUnavailable("transport timeout")
        return DiscoveryOutcome(
            [
                DiscoveryResult(
                    "A",
                    "https://a.test/article",
                    "snip",
                    "engine-a",
                    0.9,
                    contributions=(
                        DiscoveryContribution(subquery, "engine-a", 1, 0.9),
                        DiscoveryContribution(subquery, "engine-b", 3, 0.9),
                    ),
                )
            ],
            [],
        )


def test_partial_subquery_failure_keeps_results_and_reports_attempts_and_engines():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(planner=_TwoQueryPlanner(), discovery=_PartiallyUnavailableDiscovery()),
    )

    assert resp.passages
    assert resp.stats.discovery_status == "degraded"
    assert [(item.query, item.status, item.result_count) for item in resp.stats.subquery_diagnostics] == [
        ("working query", "ok", 1),
        ("failed query", "failed", 0),
    ]
    assert [(item.engine, item.contribution_count) for item in resp.stats.engine_contributions] == [
        ("engine-a", 1),
        ("engine-b", 1),
    ]
    diagnostic = next(item for item in resp.stats.url_diagnostics if item.selected)
    assert diagnostic.engines == ["engine-a", "engine-b"]
    assert diagnostic.positions == [1, 3]
    assert diagnostic.contributing_subqueries == ["working query"]
    assert diagnostic.contribution_count == 2
    assert diagnostic.independent_subquery_count == 1
    assert diagnostic.best_upstream_score == diagnostic.discovery_score == 0.9


def test_engine_failure_without_results_is_provider_unavailable():
    discovery = _OutcomeDiscovery([], [("qwant", "Suspended: access denied")])

    resp = anyio.run(run_search, SearchRequest(query="x"), fakes.deps(discovery=discovery))

    assert resp.passages == []
    assert resp.stats.reason == "search_provider_unavailable"
    assert resp.stats.discovery_status == "unavailable"
    assert [item.engine for item in resp.stats.unresponsive_engines] == ["qwant"]


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
    assert resp.stats.reason == "no_urls_after_selection"
    assert resp.stats.urls_discovered == 2
    assert resp.stats.urls_selected == 0


def test_domains_allowlist_limits_selection():
    resp = anyio.run(run_search, SearchRequest(query="x", domains=["b.test"]), fakes.deps())
    assert resp.citations
    assert all("b.test" in citation.url for citation in resp.citations)


def test_operator_allowlist_only_intersects_request_domains():
    deny = anyio.run(
        run_search,
        SearchRequest(query="x", domains=["a.test"]),
        fakes.deps(domain_allowlist={"b.test"}, allowlist_only=True),
    )
    allow = anyio.run(
        run_search,
        SearchRequest(query="x", domains=["b.test"]),
        fakes.deps(domain_allowlist={"b.test"}, allowlist_only=True),
    )

    assert deny.passages == []
    assert deny.stats.reason == "no_urls_after_selection"
    assert allow.citations
    assert all("b.test" in citation.url for citation in allow.citations)


def test_operator_domain_policy_applies_to_direct_crawl_targets():
    settings = SimpleNamespace(
        domain_blocklist={"blocked.test"},
        domain_allowlist={"allowed.test"},
        allowlist_only=True,
    )

    assert pipeline._operator_domain_allows("https://allowed.test/page", settings) is True
    assert pipeline._operator_domain_allows("https://sub.allowed.test/page", settings) is True
    assert pipeline._operator_domain_allows("https://blocked.test/page", settings) is False
    assert pipeline._operator_domain_allows("https://outside.test/page", settings) is False


def test_request_domain_filters_are_normalized_in_selector_only():
    allow = anyio.run(run_search, SearchRequest(query="x", domains=["B.TEST"]), fakes.deps())
    deny = anyio.run(run_search, SearchRequest(query="x", exclude_domains=["B.TEST"]), fakes.deps())

    assert allow.citations
    assert all("b.test" in citation.url for citation in allow.citations)
    assert deny.citations
    assert all("b.test" not in citation.url for citation in deny.citations)


def test_freshness_passed_to_discovery():
    discovery = fakes.FakeDiscovery()
    anyio.run(run_search, SearchRequest(query="x", freshness="week"), fakes.deps(discovery=discovery))
    assert discovery.freshness_seen == ["week"]


def test_custom_planner_called_when_decompose_true():
    planner = fakes.FakePlanner()
    anyio.run(run_search, SearchRequest(query="x", decompose=True), fakes.deps(planner=planner))
    assert planner.called is True


def test_planner_output_is_capped_before_discovery():
    discovery = _CountingDiscovery()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(planner=_ManyPlanner(), discovery=discovery),
    )

    assert discovery.queries == ["q0", "q1", "q2"]
    assert resp.stats.sub_queries == ["q0", "q1", "q2"]


def test_valid_non_ascii_subquery_is_not_truncated_by_utf8_bytes():
    discovery = _CountingDiscovery()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(planner=_LongUnicodePlanner(), discovery=discovery),
    )

    assert discovery.queries == ["界" * 500]
    assert resp.stats.sub_queries == ["界" * 500]


def test_partial_crawl_and_dedup_counts_are_reported():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=3),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test", "https://b.test", "https://c.test"]),
            extractor=_SinglePageExtractor(),
            chunker=_FallbackChunker(),
        ),
    )

    assert resp.stats.urls_selected == 3
    assert resp.stats.urls_crawled_ok == 1
    assert resp.stats.urls_crawled_failed == 2
    assert resp.stats.pages_after_dedup == 1
    assert resp.stats.pages_deduped == 0
    assert resp.stats.chunk_strategy == "cluster-semantic-greedy-token"
    assert resp.stats.embedding_degraded is True


def test_selected_url_diagnostics_are_reported():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=1),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test", "https://b.test"]),
        ),
    )

    assert resp.stats.url_diagnostics
    assert any(item.selected for item in resp.stats.url_diagnostics)
    assert any(item.filtered_reason == "rank_cap" for item in resp.stats.url_diagnostics)


def test_url_diagnostic_budget_prioritizes_selected_and_reports_omissions():
    urls = [f"https://blocked.test/{index}" for index in range(60)] + ["https://answer.test/article"]
    resp = anyio.run(
        run_search,
        SearchRequest(query="answer", max_urls=1),
        fakes.deps(
            discovery=_StaticDiscovery(urls),
            blocklist={"blocked.test"},
        ),
    )

    assert len(resp.stats.url_diagnostics) == 50
    assert resp.stats.url_diagnostics[0].selected is True
    assert resp.stats.url_diagnostics[0].url == "https://answer.test/article"
    assert [(item.reason, item.count) for item in resp.stats.url_diagnostics_omitted] == [
        ("blocked_domain", 11)
    ]


def test_url_diagnostic_byte_budget_is_independent_of_item_cap():
    long_urls = [
        f"https://blocked.test/{index}/" + ("x" * 1800)
        for index in range(45)
    ]
    resp = anyio.run(
        run_search,
        SearchRequest(query="answer", max_urls=1),
        fakes.deps(
            discovery=_StaticDiscovery(long_urls + ["https://answer.test/article"]),
            blocklist={"blocked.test"},
        ),
    )

    diagnostics_bytes = len(
        json.dumps(
            [item.model_dump(mode="json") for item in resp.stats.url_diagnostics],
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert resp.stats.url_diagnostics[0].selected is True
    assert len(resp.stats.url_diagnostics) < 46
    assert diagnostics_bytes <= MAX_URL_DIAGNOSTICS_BYTES
    assert sum(item.count for item in resp.stats.url_diagnostics_omitted) > 0


def test_selector_without_diagnostics_uses_the_same_byte_envelope(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_URL_DIAGNOSTICS_BYTES", 4096)
    results = [
        DiscoveryResult(
            "t" * 700,
            f"https://example{index}.test/" + ("x" * 3000),
            "snippet",
            "engine",
            1.0 - (index * 0.01),
        )
        for index in range(20)
    ]
    resp = anyio.run(
        run_search,
        SearchRequest(query="answer", max_urls=20),
        fakes.deps(
            discovery=_OutcomeDiscovery(results, []),
            selector=fakes.FakeSelector(),
        ),
    )

    diagnostics_bytes = len(
        json.dumps(
            [item.model_dump(mode="json") for item in resp.stats.url_diagnostics],
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert diagnostics_bytes <= 4096
    assert sum(item.count for item in resp.stats.url_diagnostics_omitted) > 0


def test_candidate_prefilter_limits_chunks_sent_to_reranker_and_reports_stats():
    resp = anyio.run(
        run_search,
        SearchRequest(query="Where was Oppenheimer born?", max_urls=2),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article", "https://b.test/article"]),
            chunker=_ManyChunker(),
            candidate_prefilter=CandidatePrefilterImpl(max_candidates=5),
        ),
    )

    assert resp.stats.chunks_produced == 20
    assert resp.stats.chunks_sent_to_reranker == 5
    assert resp.stats.chunks_prefiltered == 15
    assert resp.stats.prefilter_strategy == "lexical-source-preserving@1"
    assert any("New York City" in passage.text for passage in resp.passages)


def test_partial_reranker_batch_failure_reports_stats_and_keeps_results():
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 2:
            import httpx

            return httpx.Response(500, json={})
        import httpx

        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "score": 0.9},
                    {"index": 1, "score": 0.8},
                ]
            },
        )

    import httpx

    reranker = RerankerClient(
        "http://reranker:80",
        "m",
        path="/rerank",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        batch_size=2,
        timeout_s=30.0,
    )

    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=1),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article"]),
            chunker=_ManyChunker(),
            candidate_prefilter=CandidatePrefilterImpl(max_candidates=4),
            reranker=reranker,
        ),
    )

    assert resp.passages
    assert resp.stats.reranked is True
    assert resp.stats.reranker_batches == 2
    assert resp.stats.reranker_batches_failed == 1
    assert resp.stats.reranker_floor_filled is True
    assert resp.stats.chunks_reranked == 2


def test_relevance_floor_returns_fewer_passages_instead_of_padding_weak_chunks():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_passages=3),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article"]),
            chunker=_FixedChunker(),
            reranker=_MixedScoreReranker(),
        ),
    )

    assert [passage.text for passage in resp.passages] == ["strong answer"]
    assert resp.stats.passages_dropped_below_threshold == 2
    assert resp.stats.relevance_threshold_policy == "score>0.0"
    assert resp.stats.reason is None


def test_relevance_floor_returns_empty_when_all_reranked_chunks_are_below_floor():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_passages=3),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article"]),
            chunker=_FixedChunker(),
            reranker=_AllNegativeReranker(),
        ),
    )

    assert resp.passages == []
    assert resp.citations == []
    assert resp.stats.reranked is True
    assert resp.stats.passages_dropped_below_threshold == 3
    assert resp.stats.relevance_threshold_policy == "score>0.0"
    assert resp.stats.reason == "no_chunks_after_rerank"


def test_relevance_floor_is_not_applied_to_reranker_degraded_fallback():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_passages=3),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article"]),
            chunker=_FixedChunker(),
            reranker=fakes.DownReranker(),
        ),
    )

    assert len(resp.passages) == 3
    assert resp.stats.reranked is False
    assert resp.stats.passages_dropped_below_threshold == 0
    assert resp.stats.relevance_threshold_policy is None
    assert all(
        passage.score_components[-1].strategy == "position-fallback@1"
        for passage in resp.passages
    )
    assert all(passage.score_components[-1].score == passage.score for passage in resp.passages)


def test_page_dedup_count_is_reported():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=2),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test", "https://b.test"]),
            extractor=_DuplicateBodyExtractor(),
        ),
    )

    assert resp.stats.urls_crawled_ok == 2
    assert resp.stats.pages_after_dedup == 1
    assert resp.stats.pages_deduped == 1


def test_empty_reranker_result_returns_reason():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(reranker=fakes.EmptyReranker()),
    )

    assert resp.passages == []
    assert resp.stats.reason == "no_chunks_after_rerank"


def test_raw_markdown_attached_for_returned_citations_only():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", domains=["a.test"], include_raw_markdown=True),
        fakes.deps(),
    )
    assert resp.raw_markdown
    assert {r.citation_id for r in resp.raw_markdown} == {c.id for c in resp.citations}


class _UrlVariantChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return [
            Chunk(
                text="chunk text",
                token_count=2,
                source_url=f"{page.url}/canonical-different-path",
                title=page.title,
                position=0,
                source_id=page.source_id,
            )
            for page in pages
        ]


def test_raw_markdown_uses_source_id_not_url_matching():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", domains=["a.test"], include_raw_markdown=True),
        fakes.deps(chunker=_UrlVariantChunker()),
    )

    assert resp.citations[0].url.endswith("/canonical-different-path")
    assert [item.model_dump() for item in resp.raw_markdown] == [
        {
            "citation_id": resp.citations[0].id,
            "markdown": "Markdown for https://a.test/article",
            "cleaned_markdown": "Markdown for https://a.test/article",
            "document_id": None,
        }
    ]


def test_chunking_receives_cleaned_markdown_but_raw_markdown_returns_original():
    chunker = _RecordingChunker()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x", include_raw_markdown=True),
        fakes.deps(
            discovery=_StaticDiscovery(["https://a.test/article"]),
            extractor=_ChromeExtractor(),
            chunker=chunker,
        ),
    )

    assert chunker.texts == ["# Article\nThis is the article body about the query."]
    assert resp.raw_markdown[0].markdown == (
        "Navigation Menu\nSearch\n# Article\n"
        "This is the article body about the query."
    )
    assert resp.stats.pages_cleaned == 1
    assert resp.stats.markdown_blocks_dropped == 2


class _QualityChunks:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        page = pages[0]
        return [
            Chunk("Home\nMenu\nSearch\nLogin", 4, page.url, page.title, 0, page.source_id),
            Chunk("The answer is 42.", 4, page.url, page.title, 1, page.source_id),
        ]


def test_pipeline_quality_gate_reports_rule_counts_and_keeps_concise_facts():
    resp = anyio.run(
        run_search,
        SearchRequest(query="answer"),
        fakes.deps(chunker=_QualityChunks(), evidence_quality_enabled=True),
    )

    assert [item.text for item in resp.passages] == ["The answer is 42."]
    assert resp.stats.evidence_quality_strategy == "evidence-quality@1"
    assert resp.stats.evidence_quality_chunks_dropped == 1
    assert [(item.reason, item.count) for item in resp.stats.evidence_quality_drops] == [
        ("navigation_boilerplate", 1)
    ]


class _OnlyNoiseChunks:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        page = pages[0]
        return [Chunk("Home\nMenu\nSearch\nLogin", 4, page.url, page.title, 0, page.source_id)]


def test_quality_gate_has_a_stage_specific_empty_reason():
    resp = anyio.run(
        run_search,
        SearchRequest(query="answer"),
        fakes.deps(chunker=_OnlyNoiseChunks(), evidence_quality_enabled=True),
    )

    assert resp.passages == []
    assert resp.stats.reason == "no_evidence_after_quality_gate"


class _OversizedEvidenceChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        page = pages[0]
        text = "x" * (MAX_EVIDENCE_ITEM_BYTES + 1)
        return [Chunk(text, 1, page.url, page.title, 0, page.source_id)]


def test_evidence_byte_budget_truncates_top_ranked_oversized_chunk():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(chunker=_OversizedEvidenceChunker()),
    )

    assert len(resp.passages) == 1
    assert len(resp.passages[0].model_dump_json().encode("utf-8")) <= MAX_EVIDENCE_ITEM_BYTES
    assert resp.stats.evidence_items_omitted == 0
    response_bytes = len(
        json.dumps(
            [item.model_dump(mode="json") for item in resp.passages],
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert response_bytes <= MAX_EVIDENCE_BYTES


def test_evidence_byte_truncation_preserves_exact_identity():
    document = "evidence " * 10_000
    identity = build_document_identity("https://example.test/article", document)
    original_evidence_id = evidence_id_for(
        identity.final_url,
        identity.cleaned_markdown_sha256,
        0,
        len(document),
    )
    chunk = Chunk(
        document,
        10_000,
        identity.final_url,
        "Example",
        0,
        start_index=0,
        end_index=len(document),
        verbatim=True,
        document_id=identity.document_id,
        evidence_id=original_evidence_id,
        final_url=identity.final_url,
        cleaned_markdown_sha256=identity.cleaned_markdown_sha256,
    )

    bounded = pipeline._bound_evidence([ScoredChunk(chunk, 1.0)], SearchStats())

    assert len(bounded) == 1
    fitted = bounded[0].chunk
    assert fitted.text == document[fitted.start_index:fitted.end_index]
    assert fitted.evidence_id != original_evidence_id
    assert fitted.verbatim is True


def test_aggregate_evidence_envelope_counts_serialized_list_bytes():
    scored = [
        ScoredChunk(
            Chunk(
                "x" * 40_000,
                1,
                f"https://example{index}.test/article",
                "Example",
                index,
            ),
            1.0 - (index * 0.01),
        )
        for index in range(10)
    ]
    stats = SearchStats()

    bounded = pipeline._bound_evidence(scored, stats)
    serialized_bytes = len(
        json.dumps(
            [
                pipeline._wire_scored_passage(item).model_dump(mode="json")
                for item in bounded
            ],
            separators=(",", ":"),
        ).encode("utf-8")
    )

    assert serialized_bytes <= MAX_EVIDENCE_BYTES
    assert stats.evidence_items_omitted > 0


class _OversizedRawExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        text = "raw" * (MAX_RAW_MARKDOWN_ITEM_BYTES // 3 + 1)
        return [Page(urls[0], "oversized", text)]


class _SmallChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        page = pages[0]
        return [Chunk("useful", 1, page.url, page.title, 0, page.source_id)]


def test_raw_markdown_has_an_independent_item_and_byte_budget():
    resp = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=1, include_raw_markdown=True),
        fakes.deps(extractor=_OversizedRawExtractor(), chunker=_SmallChunker()),
    )

    assert resp.passages
    assert resp.raw_markdown == []
    assert resp.stats.raw_markdown_omitted == 1


def test_raw_markdown_array_framing_counts_at_exact_boundary(monkeypatch):
    page = Page("https://a.test", "A", "raw", source_id=1)
    citation = SimpleNamespace(id=1, source_id=1, document_id=None)
    item = RawMarkdown(
        citation_id=1,
        markdown="raw",
        cleaned_markdown="raw",
        document_id=None,
    )
    actual_array_bytes = len(
        json.dumps([item.model_dump(mode="json")], separators=(",", ":")).encode("utf-8")
    )
    monkeypatch.setattr(pipeline, "MAX_RAW_MARKDOWN_BYTES", actual_array_bytes - 1)
    stats = SearchStats()

    raw = pipeline._build_raw_markdown(
        SearchRequest(query="x", include_raw_markdown=True),
        [page],
        [citation],
        stats,
    )

    assert raw == []
    assert stats.raw_markdown_omitted == 1


class _InterleavingDiscovery:
    async def search(self, subquery: str, freshness: str | None = None):
        return DiscoveryOutcome(
            [DiscoveryResult(subquery, f"https://{subquery}.test/article", "snip", "engine", 1.0)],
            [],
        )


class _InterleavingExtractor:
    def __init__(self):
        self.entered = 0
        self.both_entered = asyncio.Event()

    async def extract(self, urls: list[str]) -> list[Page]:
        self.entered += 1
        if self.entered == 2:
            self.both_entered.set()
        await self.both_entered.wait()
        return [Page(urls[0], urls[0], f"evidence from {urls[0]}")]


class _InterleavingReranker:
    def __init__(self):
        self.entered = 0
        self.both_entered = asyncio.Event()

    async def rerank(self, query: str, chunks: list[Chunk]):
        self.entered += 1
        if self.entered == 2:
            self.both_entered.set()
        await self.both_entered.wait()
        batches = 1 if query == "alpha" else 2
        return RerankOutcome(
            [ScoredChunk(chunk, 0.9) for chunk in chunks],
            RerankerTelemetry(
                batches=batches,
                batches_failed=batches - 1,
                floor_filled=query == "beta",
                scored_count=len(chunks),
            ),
            "interleaved@1",
        )


def test_concurrent_searches_keep_fetch_and_reranker_stats_request_owned():
    async def scenario():
        deps = fakes.deps(
            discovery=_InterleavingDiscovery(),
            extractor=_InterleavingExtractor(),
            reranker=_InterleavingReranker(),
        )
        return await asyncio.gather(
            run_search(SearchRequest(query="alpha"), deps),
            run_search(SearchRequest(query="beta"), deps),
        )

    alpha, beta = anyio.run(scenario)

    assert alpha.passages[0].text.endswith("alpha.test/article")
    assert beta.passages[0].text.endswith("beta.test/article")
    assert (alpha.stats.reranker_batches, alpha.stats.reranker_batches_failed) == (1, 0)
    assert (beta.stats.reranker_batches, beta.stats.reranker_batches_failed) == (2, 1)
    assert alpha.stats.reranker_floor_filled is False
    assert beta.stats.reranker_floor_filled is True


def test_malformed_subquery_response_is_isolated_from_healthy_sibling():
    async def scenario():
        def handler(request):
            if request.url.params["q"] == "failed query":
                return httpx.Response(200, text="<html>rate limited</html>")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "A",
                            "url": "https://a.test/article",
                            "engine": "engine-a",
                            "score": 1.0,
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        discovery = SearxngDiscovery("http://searxng:8080", client=client)
        try:
            return await run_search(
                SearchRequest(query="x"),
                fakes.deps(planner=_TwoQueryPlanner(), discovery=discovery),
            )
        finally:
            await client.aclose()

    resp = anyio.run(scenario)

    assert resp.passages
    assert resp.stats.discovery_status == "degraded"
    assert [
        (item.query, item.status, item.failure_reason)
        for item in resp.stats.subquery_diagnostics
    ] == [
        ("working query", "ok", None),
        ("failed query", "failed", "malformed_response"),
    ]


@pytest.mark.parametrize(
    "unsafe_url,resolved",
    [
        ("http://169.254.169.254/latest/meta-data", {}),
        ("http://127.0.0.1/admin", {}),
        ("http://10.1.2.3/admin", {}),
        ("http://[::1]/admin", {}),
        ("http://[fd00:ec2::254]/latest/meta-data", {}),
        ("http://chunker:8000/admin", {"chunker": ["172.18.0.2"]}),
    ],
)
def test_unsafe_seed_urls_drop_before_selection_and_extraction(monkeypatch, unsafe_url, resolved):
    _mock_resolver(monkeypatch, {"safe.example": ["93.184.216.34"], **resolved})
    extractor = _RecordingExtractor()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(
            discovery=_StaticDiscovery([unsafe_url]),
            extractor=extractor,
            url_safety=_url_safety(),
        ),
    )

    assert resp.stats.urls_discovered == 1
    assert resp.stats.urls_selected == 0
    assert extractor.attempted_urls == []


def test_safe_public_seed_url_still_selects_and_crawls(monkeypatch):
    _mock_resolver(monkeypatch)
    extractor = _RecordingExtractor()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(
            discovery=_StaticDiscovery(["https://safe.example/article"]),
            extractor=extractor,
            url_safety=_url_safety(),
        ),
    )

    assert resp.stats.urls_selected == 1
    assert extractor.attempted_urls == ["https://safe.example/article"]
    assert resp.citations


@pytest.mark.parametrize(
    "candidate",
    [
        "file:///etc/passwd",
        "gopher://safe.example/1",
        "data:text/plain,hello",
        "ftp://safe.example/file",
        "safe.example/schemeless",
    ],
)
def test_unsafe_schemes_and_schemeless_candidates_drop_before_extraction(monkeypatch, candidate):
    _mock_resolver(monkeypatch)
    extractor = _RecordingExtractor()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(
            discovery=_StaticDiscovery([candidate]),
            extractor=extractor,
            url_safety=_url_safety(),
        ),
    )

    assert resp.stats.urls_selected == 0
    assert extractor.attempted_urls == []


@pytest.mark.parametrize(
    "candidate",
    [
        "http://2130706433/admin",
        "http://0x7f000001/admin",
        "http://0177.0.0.1/admin",
        "http://127.1/admin",
    ],
)
def test_encoded_loopback_hosts_drop_before_extraction(monkeypatch, candidate):
    _mock_resolver(monkeypatch)
    extractor = _RecordingExtractor()

    resp = anyio.run(
        run_search,
        SearchRequest(query="x"),
        fakes.deps(
            discovery=_StaticDiscovery([candidate]),
            extractor=extractor,
            url_safety=_url_safety(),
        ),
    )

    assert resp.stats.urls_selected == 0
    assert extractor.attempted_urls == []


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


def test_search_exposes_bounded_per_url_fetch_outcomes():
    class MixedOutcomeExtractor:
        supported_capabilities = frozenset({"markdown", "javascript", "links", "metadata"})

        async def extract(self, _urls):
            raise AssertionError("search must consume typed fetch outcomes")

        async def fetch(self, urls, capabilities, include_raw_html):
            assert capabilities == frozenset({"markdown", "javascript", "links", "metadata"})
            assert include_raw_html is False
            return [
                FetchStageOutcome(
                    requested_url=urls[0],
                    final_url=urls[0],
                    code=FetchOutcomeCode.CONTENT,
                    retrieval_method="crawl4ai_browser",
                    elapsed_ms=7,
                    status_code=200,
                    content_type="text/html",
                    title="A",
                    page=Page(urls[0], "A", "Useful evidence."),
                ),
                FetchStageOutcome(
                    requested_url=urls[1],
                    final_url=None,
                    code=FetchOutcomeCode.UPSTREAM_TIMEOUT,
                    retrieval_method="crawl4ai_browser",
                    elapsed_ms=30,
                ),
            ]

    response = anyio.run(
        run_search,
        SearchRequest(query="x", max_urls=2),
        fakes.deps(
            discovery=_StaticDiscovery(
                ["https://a.test/article", "https://b.test/article"]
            ),
            extractor=MixedOutcomeExtractor(),
        ),
    )

    assert response.passages
    assert response.stats.urls_crawled_ok == 1
    assert response.stats.urls_crawled_failed == 1
    assert [item.outcome for item in response.stats.fetch_outcomes] == [
        "content",
        "upstream_timeout",
    ]
    assert response.stats.fetch_outcome_counts[0].outcome == "content"
    assert response.stats.fetch_outcome_counts[0].count == 1
    assert response.stats.fetch_outcome_counts[1].outcome == "upstream_timeout"
    assert response.stats.fetch_outcomes[1].retryable is True
