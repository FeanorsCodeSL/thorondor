import anyio
import httpx

from orchestrator import fakes
from orchestrator.clients.reranker_client import RerankerClient
from orchestrator.models import SearchRequest
from orchestrator.pipeline import run_search
from orchestrator.prefilter import CandidatePrefilterImpl
from orchestrator.types import Chunk, DiscoveryOutcome, DiscoveryResult, Page


class _InvestigationDiscovery:
    async def search(self, subquery: str, freshness: str | None = None):
        return DiscoveryOutcome(
            [
                DiscoveryResult(
                    "Atomic",
                    "https://atomicarchive.com/oppenheimer",
                    "born New York",
                    "bing",
                    0.95,
                ),
                DiscoveryResult(
                    "NPS",
                    "https://nps.gov/oppenheimer",
                    "born New York City",
                    "mojeek",
                    0.9,
                ),
                DiscoveryResult(
                    "Britannica",
                    "https://britannica.com/oppenheimer",
                    "physicist biography",
                    "qwant",
                    0.88,
                ),
                DiscoveryResult(
                    "NYT",
                    "https://nytimes.com/oppenheimer",
                    "obituary born New York",
                    "startpage",
                    0.86,
                ),
                DiscoveryResult("Same A", "https://same.test/a", "duplicate domain", "bing", 0.84),
                DiscoveryResult("Same B", "https://same.test/b", "duplicate domain", "bing", 0.83),
                DiscoveryResult("Same C", "https://same.test/c", "duplicate domain", "bing", 0.82),
                DiscoveryResult("Low", "https://low.test/x", "low relevance", "yep", 0.2),
            ],
            [],
        )


class _BoilerplateExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [
            Page(
                url,
                url.rsplit("/", 1)[-1],
                "\n".join(
                    [
                        "Navigation Menu",
                        "Search",
                        f"# Article for {url}",
                        "J. Robert Oppenheimer was born in New York City on April 22, 1904.",
                        "This page also discusses education, physics, and the Manhattan Project.",
                        "Was this page helpful?",
                    ]
                ),
                html="\n".join(
                    [
                        f"# Article for {url}",
                        "J. Robert Oppenheimer was born in New York City on April 22, 1904.",
                        "This page also discusses education, physics, and the Manhattan Project.",
                    ]
                ),
            )
            for url in urls
        ]


class _ManyInvestigationChunks:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in pages:
            assert "Navigation Menu" not in page.markdown
            for position in range(10):
                text = (
                    "J. Robert Oppenheimer was born in New York City."
                    if position == 2
                    else f"background filler chunk {position} from {page.title}"
                )
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


def test_investigation_flow_broadens_cleans_prefilters_batches_and_shapes_results():
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(500, json={})
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "score": 4.0},
                    {"index": 1, "score": -0.2},
                    {"index": 2, "score": -0.3},
                    {"index": 3, "score": -0.4},
                    {"index": 4, "score": -0.5},
                ]
            },
        )

    reranker = RerankerClient(
        "http://reranker:80",
        "m",
        path="/rerank",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        batch_size=5,
        timeout_s=30.0,
    )

    resp = anyio.run(
        run_search,
        SearchRequest(
            query="Where was J. Robert Oppenheimer born?",
            search_profile="research",
            max_urls=6,
            max_passages=6,
        ),
        fakes.deps(
            discovery=_InvestigationDiscovery(),
            extractor=_BoilerplateExtractor(),
            chunker=_ManyInvestigationChunks(),
            candidate_prefilter=CandidatePrefilterImpl(max_candidates=10),
            reranker=reranker,
        ),
    )

    assert resp.stats.urls_selected == 6
    assert resp.stats.url_diagnostics
    assert resp.stats.pages_cleaned == 6
    assert resp.stats.markdown_blocks_dropped == 0
    assert resp.stats.chunks_produced == 60
    assert resp.stats.chunks_sent_to_reranker == 10
    assert resp.stats.chunks_prefiltered == 50
    assert resp.stats.reranked is True
    assert resp.stats.reranker_batches == 2
    assert resp.stats.reranker_batches_failed == 1
    assert resp.stats.reranker_floor_filled is True
    assert resp.stats.passages_dropped_below_threshold > 0
    assert resp.passages
    assert all("Navigation Menu" not in passage.text for passage in resp.passages)
    assert "New York City" in resp.passages[0].text
    assert resp.citations
