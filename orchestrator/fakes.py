"""Deterministic fakes for orchestrator tests."""
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .pipeline import PipelineDeps
from .assembly import ResultAssemblerImpl
from .selection import SelectionPolicyImpl
from .types import AssembledCitation, AssembledPassage, Chunk, DiscoveryResult, Page, ScoredChunk


class FakePlanner:
    def __init__(self):
        self.called = False

    async def plan(self, query: str) -> list[str]:
        self.called = True
        return [query]


class EmptyPlanner:
    async def plan(self, query: str) -> list[str]:
        return []


class PartialPlanner:
    async def plan(self, query: str) -> list[str]:
        return [query, f"{query} context"][:1]


class DownPlanner:
    async def plan(self, query: str) -> list[str]:
        raise RuntimeError("planner down")


class FakeDiscovery:
    def __init__(self):
        self.freshness_seen: list[str | None] = []

    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        self.freshness_seen.append(freshness)
        return [
            DiscoveryResult("A", "https://a.test/article", "snip", "fake", 0.9),
            DiscoveryResult("B", "https://b.test/article", "snip", "fake", 0.8),
        ]


class EmptyDiscovery:
    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        return []


class PartialDiscovery:
    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        return [DiscoveryResult("A", "https://a.test/article", "snip", "fake", 0.9)]


class DownDiscovery:
    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        raise DiscoveryUnavailable("down")


class FakeSelector:
    def __init__(self, subset: list[DiscoveryResult] | None = None):
        self.subset = subset

    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]:
        if self.subset is not None:
            return self.subset[:max_urls]
        return results[:max_urls]


class EmptySelector:
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]:
        return []


class DownSelector:
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]:
        raise RuntimeError("selector down")


class FakeExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in urls]


class EmptyExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return []


class PartialExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        kept = urls[: max(1, len(urls) - 1)]
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in kept]


class DownExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        raise RuntimeError("extractor down")


class FakeChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return [
            Chunk(page.markdown, len(page.markdown.split()), page.url, page.title, index, page.source_id)
            for page in pages
            for index in [0]
        ]


class EmptyChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return []


class PartialChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        kept = pages[: max(1, len(pages) - 1)]
        return [
            Chunk(page.markdown, len(page.markdown.split()), page.url, page.title, index, page.source_id)
            for page in kept
            for index in [0]
        ]


class DownChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        raise ChunkerUnavailable("down")


class FakeReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return [ScoredChunk(chunk, 1.0 - (index * 0.1)) for index, chunk in enumerate(chunks)]


class EmptyReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return []


class PartialReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return [ScoredChunk(chunk, 1.0 - (index * 0.1)) for index, chunk in enumerate(chunks[:1])]


class DownReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
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
                )
            )
            total_tokens += item.chunk.token_count

        return passages, citations


class EmptyAssembler:
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
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
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        raise RuntimeError("assembler down")


def deps(**overrides) -> PipelineDeps:
    values = {
        "planner": FakePlanner(),
        "discovery": FakeDiscovery(),
        "selector": SelectionPolicyImpl(),
        "extractor": FakeExtractor(),
        "chunker": FakeChunker(),
        "reranker": FakeReranker(),
        "assembler": ResultAssemblerImpl(),
        "default_token_budget": 4000,
        "default_max_urls": 6,
        "blocklist": set(),
        "domain_allowlist": set(),
        "allowlist_only": False,
        "url_safety": list,
    }
    values.update(overrides)
    return PipelineDeps(**values)
