"""Internal pipeline data types."""
from dataclasses import dataclass


@dataclass
class DiscoveryResult:
    title: str
    url: str
    snippet: str
    engine: str
    score: float


@dataclass(frozen=True)
class DiscoveryEngineFailure:
    engine: str
    reason: str


@dataclass
class DiscoveryOutcome:
    results: list[DiscoveryResult]
    unresponsive_engines: list[DiscoveryEngineFailure]


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    source_id: int | None = None
    original_markdown: str | None = None
    html: str | None = None


@dataclass
class CleanedPage:
    page: Page
    chars_before: int
    chars_after: int
    blocks_dropped: int
    cleaner_version: str


@dataclass
class Chunk:
    text: str
    token_count: int
    source_url: str
    title: str
    position: int
    source_id: int | None = None
    chunk_strategy: str | None = None
    embedding_degraded: bool = False


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


@dataclass
class PrefilteredChunks:
    chunks: list[Chunk]
    chunks_prefiltered: int
    chunks_sent_to_reranker: int
    prefilter_strategy: str


@dataclass
class AssembledPassage:
    text: str
    score: float
    token_count: int
    citation_id: int


@dataclass
class AssembledCitation:
    id: int
    url: str
    title: str
    source_id: int | None = None
