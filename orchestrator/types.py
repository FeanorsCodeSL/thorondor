"""Internal pipeline data types."""
from dataclasses import dataclass, field
from typing import TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class DiscoveryContribution:
    subquery: str
    engine: str
    position: int | None
    score: float


@dataclass
class DiscoveryResult:
    title: str
    url: str
    snippet: str
    engine: str
    score: float
    published_at: str | None = None
    contributions: tuple[DiscoveryContribution, ...] = ()


@dataclass(frozen=True)
class DiscoveryEngineFailure:
    engine: str
    reason: str


@dataclass
class DiscoveryOutcome:
    results: list[DiscoveryResult]
    unresponsive_engines: list[DiscoveryEngineFailure]


@dataclass(frozen=True)
class MetadataCandidate:
    field: str
    value: str
    source: str
    confidence: str


@dataclass(frozen=True)
class MetadataConflict:
    field: str
    candidates: tuple[MetadataCandidate, ...]


@dataclass(frozen=True)
class DocumentMetadata:
    selected: tuple[MetadataCandidate, ...] = ()
    conflicts: tuple[MetadataConflict, ...] = ()

    def get(self, field_name: str) -> MetadataCandidate | None:
        return next(
            (candidate for candidate in self.selected if candidate.field == field_name),
            None,
        )


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    source_id: int | None = None
    original_markdown: str | None = None
    html: str | None = None
    requested_url: str | None = None
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    links: dict[str, JsonValue] = field(default_factory=dict)
    discovery_published_at: str | None = None
    evidence_metadata: DocumentMetadata | None = None


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
    start_index: int | None = None
    end_index: int | None = None
    verbatim: bool = False
    document_id: str | None = None
    evidence_id: str | None = None
    final_url: str | None = None
    cleaned_markdown_sha256: str | None = None
    section_heading: str | None = None
    evidence_metadata: DocumentMetadata | None = None


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    score: float
    strategy: str


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    score_components: tuple[ScoreComponent, ...] = ()


@dataclass(frozen=True)
class RerankerTelemetry:
    batches: int = 0
    batches_failed: int = 0
    floor_filled: bool = False
    scored_count: int = 0


@dataclass
class RerankOutcome:
    scored: list[ScoredChunk]
    telemetry: RerankerTelemetry
    strategy: str

    def __getitem__(self, index):
        return self.scored[index]

    def __iter__(self):
        return iter(self.scored)

    def __len__(self) -> int:
        return len(self.scored)


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
    start_index: int | None = None
    end_index: int | None = None
    verbatim: bool = False
    document_id: str | None = None
    evidence_id: str | None = None
    section_heading: str | None = None
    score_components: tuple[ScoreComponent, ...] = ()


@dataclass(frozen=True)
class EvidenceSpan:
    start_index: int | None
    end_index: int | None
    verbatim: bool
    evidence_id: str | None
    section_heading: str | None = None


@dataclass
class AssembledCitation:
    id: int
    url: str
    title: str
    source_id: int | None = None
    document_id: str | None = None
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    evidence_metadata: DocumentMetadata | None = None
