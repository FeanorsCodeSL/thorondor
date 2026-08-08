"""Protocols for orchestrator pipeline stages."""
from typing import Protocol

from .clients.structured_extractor import StructuredModelResult
from .page_cache import CacheRepositoryStats, PageCacheRecord, StoredTargetSnapshot
from .types import (
    AssembledCitation,
    AssembledPassage,
    Chunk,
    CleanedPage,
    DiscoveryOutcome,
    DiscoveryResult,
    FetchStageOutcome,
    Page,
    PrefilteredChunks,
    RerankOutcome,
    ScoredChunk,
)


class QueryPlanner(Protocol):
    async def plan(self, query: str) -> list[str]: ...


class SearchDiscovery(Protocol):
    async def search(self, subquery: str, freshness: str | None = None) -> DiscoveryOutcome: ...


class SelectionPolicy(Protocol):
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
        query: str | None = None,
    ) -> list[DiscoveryResult]: ...


class ContentExtractor(Protocol):
    async def extract(self, urls: list[str]) -> list[Page]: ...

    async def fetch(
        self,
        urls: list[str],
        capabilities: frozenset[str],
        include_raw_html: bool,
    ) -> list[FetchStageOutcome]: ...


class StructuredDataExtractor(Protocol):
    async def extract(
        self,
        markdown: str,
        schema: dict[str, object],
    ) -> StructuredModelResult: ...


class PageCacheRepository(Protocol):
    enabled: bool

    async def get(self, cache_key: str) -> PageCacheRecord | None: ...

    async def put(self, record: PageCacheRecord) -> None: ...

    async def put_with_target(
        self,
        record: PageCacheRecord,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None: ...

    async def get_target(
        self, cache_key: str, locator_hash: str
    ) -> StoredTargetSnapshot | None: ...

    async def put_target(
        self,
        cache_key: str,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None: ...

    async def delete(self, cache_key: str) -> None: ...

    async def clear_url(self, url_identity: str) -> int: ...

    async def cleanup(self, now: float | None = None) -> int: ...

    async def stats(self) -> CacheRepositoryStats: ...

    async def aclose(self) -> None: ...

    async def start(self) -> None: ...


class MarkdownCleaner(Protocol):
    def clean(self, page: Page) -> CleanedPage: ...


class SemanticChunker(Protocol):
    async def chunk(self, pages: list[Page]) -> list[Chunk]: ...


class CandidatePrefilter(Protocol):
    def filter(self, query: str, chunks: list[Chunk]) -> PrefilteredChunks: ...


class Reranker(Protocol):
    async def rerank(self, query: str, chunks: list[Chunk]) -> RerankOutcome: ...


class ResultAssembler(Protocol):
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]: ...
