"""Deterministic fakes for orchestrator tests."""
import asyncio

from .assembly import ResultAssemblerImpl
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .markdown_cleaner import MarkdownCleanerImpl
from .outcome_codes import FetchOutcomeCode
from .page_cache import DisabledPageCache, PageRefreshCoordinator
from .pipeline import PipelineDeps
from .politeness import HostPoliteness
from .prefilter import CandidatePrefilterImpl
from .resource_policy import ResourcePolicy, RuntimeAdmission
from .robots_policy import RobotsCache
from .selection import SelectionPolicyImpl
from .types import (
    AssembledCitation,
    AssembledPassage,
    Chunk,
    DiscoveryEngineFailure,
    DiscoveryOutcome,
    DiscoveryResult,
    FetchStageOutcome,
    Page,
    PrefilteredChunks,
    RerankerTelemetry,
    RerankOutcome,
    ScoreComponent,
    ScoredChunk,
)
from .url_identity import build_document_identity, evidence_id_for

FAKE_ARTICLE_URL = "https://a.test/article"


def _fake_extract(html: str, **_kwargs) -> str:
    return html


async def _async_boundary() -> None:
    await asyncio.sleep(0)


class FakePlanner:
    def __init__(self):
        self.called = False

    async def plan(self, query: str) -> list[str]:
        await _async_boundary()
        self.called = True
        return [query]


class EmptyPlanner:
    async def plan(self, _query: str) -> list[str]:
        await _async_boundary()
        return []


class PartialPlanner:
    async def plan(self, query: str) -> list[str]:
        await _async_boundary()
        return [query, f"{query} context"][:1]


class DownPlanner:
    async def plan(self, _query: str) -> list[str]:
        await _async_boundary()
        raise RuntimeError("planner down")


class FakeDiscovery:
    def __init__(self):
        self.freshness_seen: list[str | None] = []

    async def search(self, _subquery: str, freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        self.freshness_seen.append(freshness)
        return DiscoveryOutcome(
            [
                DiscoveryResult("A", FAKE_ARTICLE_URL, "snip", "fake", 0.9),
                DiscoveryResult("B", "https://b.test/article", "snip", "fake", 0.8),
            ],
            [],
        )


class EmptyDiscovery:
    async def search(self, _subquery: str, _freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        return DiscoveryOutcome([], [])


class PartialDiscovery:
    async def search(self, _subquery: str, _freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        return DiscoveryOutcome(
            [DiscoveryResult("A", FAKE_ARTICLE_URL, "snip", "fake", 0.9)],
            [],
        )


class DegradedDiscovery:
    async def search(self, _subquery: str, _freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        return DiscoveryOutcome(
            [DiscoveryResult("A", FAKE_ARTICLE_URL, "snip", "bing", 0.9)],
            [DiscoveryEngineFailure("mojeek", "access denied")],
        )


class UnavailableDiscovery:
    async def search(self, _subquery: str, _freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        return DiscoveryOutcome(
            [],
            [DiscoveryEngineFailure("qwant", "Suspended: access denied")],
        )


class DownDiscovery:
    async def search(self, _subquery: str, _freshness: str | None = None) -> DiscoveryOutcome:
        await _async_boundary()
        raise DiscoveryUnavailable("down")


class FakeSelector:
    def __init__(self, subset: list[DiscoveryResult] | None = None):
        self.subset = subset

    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        _blocklist: set[str],
        _allowlist: set[str] | None = None,
        _query: str | None = None,
    ) -> list[DiscoveryResult]:
        if self.subset is not None:
            return self.subset[:max_urls]
        return results[:max_urls]


class EmptySelector:
    def select(
        self,
        _results: list[DiscoveryResult],
        _max_urls: int,
        _blocklist: set[str],
        _allowlist: set[str] | None = None,
        _query: str | None = None,
    ) -> list[DiscoveryResult]:
        return []


class DownSelector:
    def select(
        self,
        _results: list[DiscoveryResult],
        _max_urls: int,
        _blocklist: set[str],
        _allowlist: set[str] | None = None,
        _query: str | None = None,
    ) -> list[DiscoveryResult]:
        raise RuntimeError("selector down")


class FakeExtractor:
    supported_capabilities = frozenset(
        {"markdown", "javascript", "links", "metadata", "raw_html", "pdf", "document"}
    )

    async def extract(self, urls: list[str]) -> list[Page]:
        await _async_boundary()
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in urls]

    async def fetch(self, urls, _capabilities, _include_raw_html):
        pages = await self.extract(urls)
        return [
            FetchStageOutcome(
                requested_url=page.url,
                final_url=page.url,
                code=FetchOutcomeCode.CONTENT,
                retrieval_method="fake_browser",
                elapsed_ms=0,
                status_code=200,
                content_type="text/html",
                title=page.title,
                links=page.links,
                metadata=page.metadata,
                page=page,
            )
            for page in pages
        ]


class EmptyExtractor:
    async def extract(self, _urls: list[str]) -> list[Page]:
        await _async_boundary()
        return []


class PartialExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        await _async_boundary()
        kept = urls[: max(1, len(urls) - 1)]
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in kept]


class DownExtractor:
    async def extract(self, _urls: list[str]) -> list[Page]:
        await _async_boundary()
        raise RuntimeError("extractor down")


def _chunk_page(page: Page, position: int) -> Chunk:
    identity = build_document_identity(page.final_url or page.url, page.markdown)
    return Chunk(
        text=page.markdown,
        token_count=len(page.markdown.split()),
        source_url=identity.final_url,
        title=page.title,
        position=position,
        source_id=page.source_id,
        start_index=0,
        end_index=len(page.markdown),
        verbatim=True,
        document_id=identity.document_id,
        evidence_id=evidence_id_for(
            identity.final_url,
            identity.cleaned_markdown_sha256,
            0,
            len(page.markdown),
        ),
        final_url=identity.final_url,
        cleaned_markdown_sha256=identity.cleaned_markdown_sha256,
        evidence_metadata=page.evidence_metadata,
    )


class FakeChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        await _async_boundary()
        return [
            _chunk_page(page, index)
            for page in pages
            for index in [0]
        ]


class EmptyChunker:
    async def chunk(self, _pages: list[Page]) -> list[Chunk]:
        await _async_boundary()
        return []


class PartialChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        await _async_boundary()
        kept = pages[: max(1, len(pages) - 1)]
        return [
            _chunk_page(page, index)
            for page in kept
            for index in [0]
        ]


class DownChunker:
    async def chunk(self, _pages: list[Page]) -> list[Chunk]:
        await _async_boundary()
        raise ChunkerUnavailable("down")


class FakeCandidatePrefilter:
    def filter(self, _query: str, chunks: list[Chunk]) -> PrefilteredChunks:
        return PrefilteredChunks(chunks, 0, len(chunks), "fake-prefilter")


class EmptyCandidatePrefilter:
    def filter(self, _query: str, chunks: list[Chunk]) -> PrefilteredChunks:
        return PrefilteredChunks([], len(chunks), 0, "fake-prefilter")


class PartialCandidatePrefilter:
    def filter(self, _query: str, chunks: list[Chunk]) -> PrefilteredChunks:
        kept = chunks[:1]
        return PrefilteredChunks(kept, len(chunks) - len(kept), len(kept), "fake-prefilter")


class DownCandidatePrefilter:
    def filter(self, _query: str, _chunks: list[Chunk]) -> PrefilteredChunks:
        raise RuntimeError("prefilter down")


class FakeReranker:
    async def rerank(self, _query: str, chunks: list[Chunk]) -> RerankOutcome:
        await _async_boundary()
        scored = [
            ScoredChunk(
                chunk,
                1.0 - (index * 0.1),
                (ScoreComponent("reranker", 1.0 - (index * 0.1), "fake-reranker@1"),),
            )
            for index, chunk in enumerate(chunks)
        ]
        return RerankOutcome(
            scored,
            RerankerTelemetry(
                batches=1 if chunks else 0,
                scored_count=len(scored),
            ),
            "fake-reranker@1",
        )


class EmptyReranker:
    async def rerank(self, _query: str, _chunks: list[Chunk]) -> RerankOutcome:
        await _async_boundary()
        return RerankOutcome([], RerankerTelemetry(), "fake-reranker@1")


class PartialReranker:
    async def rerank(self, _query: str, chunks: list[Chunk]) -> RerankOutcome:
        await _async_boundary()
        scored = [
            ScoredChunk(
                chunk,
                1.0,
                (ScoreComponent("reranker", 1.0, "fake-reranker@1"),),
            )
            for chunk in chunks[:1]
        ]
        return RerankOutcome(
            scored,
            RerankerTelemetry(batches=1 if chunks else 0, scored_count=len(scored)),
            "fake-reranker@1",
        )


class DownReranker:
    async def rerank(self, _query: str, _chunks: list[Chunk]) -> list[ScoredChunk]:
        await _async_boundary()
        raise RerankerUnavailable("down")


class FakeAssembler:
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        passages: list[AssembledPassage] = []
        citations: list[AssembledCitation] = []
        total_tokens = 0

        for item in scored:
            if max_passages is not None and len(passages) >= max_passages:
                break
            if total_tokens + item.chunk.token_count > token_budget:
                continue
            citation_id = len(citations) + 1
            citations.append(
                AssembledCitation(
                    id=citation_id,
                    url=item.chunk.source_url,
                    title=item.chunk.title,
                    source_id=item.chunk.source_id,
                )
            )
            passages.append(
                AssembledPassage(
                    text=item.chunk.text,
                    score=item.score,
                    token_count=item.chunk.token_count,
                    citation_id=citation_id,
                    score_components=item.score_components,
                )
            )
            total_tokens += item.chunk.token_count

        return passages, citations


class EmptyAssembler:
    def assemble(
        self,
        _scored: list[ScoredChunk],
        _token_budget: int,
        _max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        return [], []


class PartialAssembler:
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        return FakeAssembler().assemble(scored[:1], token_budget, max_passages)


class DownAssembler:
    def assemble(
        self,
        _scored: list[ScoredChunk],
        _token_budget: int,
        _max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        raise RuntimeError("assembler down")


def deps(**overrides) -> PipelineDeps:
    resource_policy = overrides.pop("resource_policy", ResourcePolicy())
    values = {
        "planner": FakePlanner(),
        "discovery": FakeDiscovery(),
        "selector": SelectionPolicyImpl(),
        "extractor": FakeExtractor(),
        "markdown_cleaner": MarkdownCleanerImpl(
            extractor=_fake_extract,
            extractor_name="fake-extractor",
            extractor_version="test",
            favor_recall=True,
            include_comments=False,
            include_tables=True,
            deduplicate=True,
        ),
        "chunker": FakeChunker(),
        "candidate_prefilter": CandidatePrefilterImpl(),
        "reranker": FakeReranker(),
        "assembler": ResultAssemblerImpl(),
        "default_token_budget": 4000,
        "default_max_urls": 6,
        "max_subqueries": 3,
        "profile_defaults": {
            "quick": {"token_budget": 2000, "max_urls": 5, "max_passages": 5},
            "research": {"token_budget": 8000, "max_urls": 12, "max_passages": 20},
            "deep": {"token_budget": 16000, "max_urls": 20, "max_passages": 40},
        },
        "blocklist": set(),
        "relevance_score_floor": 0.0,
        "evidence_quality_enabled": False,
        "domain_allowlist": set(),
        "allowlist_only": False,
        "url_safety": list,
        "crawl_url_safety": lambda _url: True,
        "resource_policy": resource_policy,
        "admission": RuntimeAdmission(resource_policy),
        "crawler_robots_user_agent": "ThorondorBot",
        "robots_cache": RobotsCache(86400),
        "site_politeness": HostPoliteness(
            default_delay_s=0,
            max_jitter_s=0,
            max_cooldown_s=60,
        ),
        "site_max_cooldown_s": 60,
        "max_robots_bytes": 262144,
        "max_sitemap_bytes": 262144,
        "max_sitemap_entries": 500,
        "max_sitemap_documents": 16,
        "crawl_respect_robots_txt": True,
        "page_cache": DisabledPageCache(),
        "page_refresh": PageRefreshCoordinator(),
        "page_cache_ttl_s": 300,
        "page_cache_stale_s": 900,
        "page_cache_retention_s": 604800,
        "page_cache_raw_html_enabled": False,
        "page_diff_max_input_lines": 2000,
        "page_diff_max_operations": 1_000_000,
        "page_diff_max_output_lines": 24,
    }
    values.update(overrides)
    return PipelineDeps(**values)
