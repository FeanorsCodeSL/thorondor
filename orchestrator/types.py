"""Internal pipeline data types."""
from dataclasses import dataclass


@dataclass
class DiscoveryResult:
    title: str
    url: str
    snippet: str
    engine: str
    score: float


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    source_id: int | None = None


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
