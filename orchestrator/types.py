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


@dataclass
class Chunk:
    text: str
    token_count: int
    source_url: str
    title: str
    position: int


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
