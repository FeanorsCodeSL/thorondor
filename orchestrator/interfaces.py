"""Protocols for orchestrator pipeline stages."""
from typing import Protocol

from .types import AssembledCitation, AssembledPassage, Chunk, DiscoveryResult, Page, ScoredChunk


class QueryPlanner(Protocol):
    async def plan(self, query: str) -> list[str]: ...


class SearchDiscovery(Protocol):
    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]: ...


class SelectionPolicy(Protocol):
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]: ...


class ContentExtractor(Protocol):
    async def extract(self, urls: list[str]) -> list[Page]: ...


class SemanticChunker(Protocol):
    async def chunk(self, pages: list[Page]) -> list[Chunk]: ...


class Reranker(Protocol):
    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]: ...


class ResultAssembler(Protocol):
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]: ...
