import anyio
import pytest

from orchestrator import fakes
from orchestrator.clients.chunker_client import ChunkerUnavailable
from orchestrator.clients.searxng_client import DiscoveryUnavailable
from orchestrator.models import SearchRequest
from orchestrator.pipeline import SearchDependencyUnavailable, run_search
from orchestrator.types import Chunk, DiscoveryResult, Page
from orchestrator.url_safety import filter_safe_discovery_results


class _StaticDiscovery:
    def __init__(self, urls: list[str]):
        self.urls = urls

    async def search(self, subquery: str, freshness: str | None = None):
        return [
            DiscoveryResult(f"T{index}", url, "snip", "fake", 1.0 - (index * 0.01))
            for index, url in enumerate(self.urls)
        ]


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
        return [DiscoveryResult(subquery, f"https://{subquery}.test/article", "snip", "fake", 1.0)]


class _ManyPlanner:
    async def plan(self, query: str) -> list[str]:
        return [f"q{index}" for index in range(50)]


class _SinglePageExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(urls[0], "one", "unique markdown")] if urls else []


class _DuplicateBodyExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(url, url, "same markdown body") for url in urls]


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


def _mock_resolver(monkeypatch, mapping: dict[str, list[str]] | None = None):
    from orchestrator import url_safety

    addresses = mapping or {"safe.example": ["93.184.216.34"]}

    def resolve(host: str):
        if host not in addresses:
            raise OSError(host)
        return [url_safety.ipaddress.ip_address(address) for address in addresses[host]]

    monkeypatch.setattr(url_safety, "resolve_host_ips", resolve)


def test_happy_path_returns_cited_budgeted_passages():
    deps = fakes.deps()
    resp = anyio.run(run_search, SearchRequest(query="eu ai act 2025"), deps)
    assert resp.passages and resp.citations
    assert resp.stats.reranked is True
    assert sum(p.token_count for p in resp.passages) <= 4000
    assert all(p.citation_id in {c.id for c in resp.citations} for p in resp.passages)
    assert {p.provenance for p in resp.passages} == {"external_web"}
    assert {p.trust for p in resp.passages} == {"untrusted"}


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
    }

    assert all(all(item is not None for item in variants) for variants in expected.values())


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
        }
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
            url_safety=filter_safe_discovery_results,
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
            url_safety=filter_safe_discovery_results,
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
            url_safety=filter_safe_discovery_results,
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
            url_safety=filter_safe_discovery_results,
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
