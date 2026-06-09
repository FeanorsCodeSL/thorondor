"""Deterministic fakes for orchestrator tests."""
from .assembly import ResultAssemblerImpl
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .pipeline import PipelineDeps
from .selection import SelectionPolicyImpl
from .types import Chunk, DiscoveryResult, Page, ScoredChunk


class FakePlanner:
    def __init__(self):
        self.called = False

    async def plan(self, query: str) -> list[str]:
        self.called = True
        return [query]


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


class DownDiscovery:
    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        raise DiscoveryUnavailable("down")


class FakeExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return [Page(url, url.split("//", 1)[-1], f"Markdown for {url}") for url in urls]


class EmptyExtractor:
    async def extract(self, urls: list[str]) -> list[Page]:
        return []


class FakeChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return [
            Chunk(page.markdown, len(page.markdown.split()), page.url, page.title, index)
            for page in pages
            for index in [0]
        ]


class EmptyChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        return []


class DownChunker:
    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        raise ChunkerUnavailable("down")


class FakeReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        return [ScoredChunk(chunk, 1.0 - (index * 0.1)) for index, chunk in enumerate(chunks)]


class DownReranker:
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        raise RerankerUnavailable("down")


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
    }
    values.update(overrides)
    return PipelineDeps(**values)
