"""Pydantic wire models for the Thorondor orchestrator."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_QUERY_CHARS = 500
MAX_TOKEN_BUDGET = 16_000
MAX_SELECTED_URLS = 20
MAX_PASSAGES = 50

SearchProfile = Literal["quick", "research", "deep"]
ReasonCode = Literal[
    "search_provider_unavailable",
    "no_results_from_discovery",
    "no_urls_after_selection",
    "all_crawls_failed",
    "no_chunks_after_dedup",
    "no_chunks_after_rerank",
]
DiscoveryStatus = Literal["ok", "degraded", "unavailable"]
MetadataField = Literal[
    "title",
    "description",
    "published_at",
    "modified_at",
    "author",
    "language",
    "declared_canonical_url",
]
MetadataSource = Literal[
    "html_title",
    "html_canonical",
    "html_lang",
    "json_ld",
    "html_meta",
    "page_metadata",
    "discovery",
    "sitemap",
    "fetch_title",
]
MetadataConfidence = Literal["high", "medium", "low"]


class Passage(BaseModel):
    text: str
    score: float
    token_count: int
    citation_id: int
    start_index: int | None = Field(default=None, ge=0)
    end_index: int | None = Field(default=None, gt=0)
    verbatim: bool = False
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    section_heading: str | None = None
    provenance: Literal["external_web"] = "external_web"
    trust: Literal["untrusted"] = "untrusted"

    @model_validator(mode="after")
    def validate_evidence(self) -> "Passage":
        if self.verbatim and (
            self.start_index is None
            or self.end_index is None
            or self.end_index <= self.start_index
        ):
            raise ValueError("verbatim passages require a valid end-exclusive span")
        if self.evidence_id is not None and (
            not self.verbatim or self.document_id is None
        ):
            raise ValueError("evidence_id requires verbatim text and document_id")
        if self.verbatim and (self.document_id is None or self.evidence_id is None):
            raise ValueError("verbatim passages require document_id and evidence_id")
        return self


class EvidenceSpan(BaseModel):
    start_index: int | None = Field(default=None, ge=0)
    end_index: int | None = Field(default=None, gt=0)
    verbatim: bool = False
    evidence_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    section_heading: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "EvidenceSpan":
        if self.verbatim and (
            self.start_index is None
            or self.end_index is None
            or self.end_index <= self.start_index
        ):
            raise ValueError("verbatim evidence requires a valid end-exclusive span")
        if self.evidence_id is not None and not self.verbatim:
            raise ValueError("evidence_id requires verbatim evidence")
        if self.verbatim and self.evidence_id is None:
            raise ValueError("verbatim evidence requires evidence_id")
        return self


class MetadataValue(BaseModel):
    value: str = Field(max_length=8192)
    source: MetadataSource
    confidence: MetadataConfidence


class MetadataConflict(BaseModel):
    field: MetadataField
    candidates: list[MetadataValue] = Field(max_length=8)


class CitationMetadata(BaseModel):
    title: MetadataValue | None = None
    description: MetadataValue | None = None
    published_at: MetadataValue | None = None
    modified_at: MetadataValue | None = None
    author: MetadataValue | None = None
    language: MetadataValue | None = None
    declared_canonical_url: MetadataValue | None = None
    conflicts: list[MetadataConflict] = Field(default_factory=list, max_length=7)


class Citation(BaseModel):
    id: int
    url: str
    title: str
    published: str | None = None
    modified_at: str | None = None
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    metadata: CitationMetadata | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "Citation":
        if any(span.verbatim for span in self.evidence_spans) and self.document_id is None:
            raise ValueError("verbatim citation spans require document_id")
        return self


class UrlDiagnostic(BaseModel):
    url: str
    title: str
    engine: str
    discovery_score: float
    selected: bool
    selection_reason: str | None = None
    filtered_reason: str | None = None


class RawMarkdown(BaseModel):
    citation_id: int
    markdown: str
    cleaned_markdown: str | None = None
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class UnresponsiveEngine(BaseModel):
    engine: str
    reason: str


class SearchStats(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    sub_queries: list[str] = Field(default_factory=list)
    discovery_status: DiscoveryStatus = "ok"
    unresponsive_engines: list[UnresponsiveEngine] = Field(default_factory=list)
    urls_discovered: int = 0
    urls_selected: int = 0
    url_diagnostics: list[UrlDiagnostic] = Field(default_factory=list)
    urls_crawled_ok: int = 0
    urls_crawled_failed: int = 0
    pages_after_dedup: int = 0
    pages_deduped: int = 0
    pages_cleaned: int = 0
    markdown_chars_before: int = 0
    markdown_chars_after: int = 0
    markdown_blocks_dropped: int = 0
    markdown_cleaner_version: str | None = None
    chunks_produced: int = 0
    chunks_prefiltered: int = 0
    chunks_sent_to_reranker: int = 0
    prefilter_strategy: str | None = None
    reranker_batches: int = 0
    reranker_batches_failed: int = 0
    reranker_floor_filled: bool = False
    chunks_reranked: int = 0
    passages_dropped_below_threshold: int = 0
    relevance_threshold_policy: str | None = None
    chunk_strategy: str | None = None
    embedding_degraded: bool = False
    reranked: bool = False
    tokens_returned: int = 0
    elapsed_ms: int = 0
    reason: ReasonCode | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    search_profile: SearchProfile | None = None
    token_budget: int | None = Field(default=None, ge=1, le=MAX_TOKEN_BUDGET)
    max_urls: int | None = Field(default=None, ge=1, le=MAX_SELECTED_URLS)
    max_passages: int | None = Field(default=None, ge=1, le=MAX_PASSAGES)
    decompose: bool = True
    freshness: Literal["day", "week", "month", "year"] | None = None
    domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    include_raw_markdown: bool = False


class SearchResponse(BaseModel):
    query: str
    passages: list[Passage]
    citations: list[Citation]
    stats: SearchStats
    raw_markdown: list[RawMarkdown] | None = None
    schema_version: Literal["thorondor.search.v1"] = "thorondor.search.v1"
