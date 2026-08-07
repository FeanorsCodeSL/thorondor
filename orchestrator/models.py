"""Pydantic wire models for the Thorondor orchestrator."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from thorondor_contracts import (
    DEFAULT_FETCH_CAPABILITIES,
    FETCH_CAPABILITIES,
    MAX_FETCH_URL_BYTES,
    MAX_FETCH_URLS,
    MAX_TARGET_TEXT_CHARS,
    RESOURCE_POLICY_SUMMARY,
    FetchCapability,
    TargetWatch,
)

MAX_QUERY_CHARS = 500
MAX_TOKEN_BUDGET = 16_000
MAX_SELECTED_URLS = 20
MAX_PASSAGES = 50
MAX_SUBQUERY_COUNT = 8
MAX_URL_DIAGNOSTICS = 50
MAX_URL_DIAGNOSTICS_BYTES = 65_536
MAX_EVIDENCE_ITEM_BYTES = 65_536
MAX_EVIDENCE_BYTES = 262_144
MAX_RAW_MARKDOWN_ITEMS = 20
MAX_RAW_MARKDOWN_ITEM_BYTES = 262_144
MAX_RAW_MARKDOWN_BYTES = 524_288
MAX_SITE_URL_BYTES = 8192
MAX_SITE_DEPTH = 5
MAX_SITE_DISCOVERED_URLS = 500
MAX_SITE_PAGES = 20
MAX_SITE_PATTERNS = 32
MAX_SITE_PATTERN_CHARS = 256
MAX_SITE_EXTENSIONS = 16
DEFAULT_SITE_EXTENSIONS = ("", ".htm", ".html", ".pdf")
MAX_TARGET_ATTRIBUTES = 32
MAX_DIFF_OUTPUT_LINES = 24

SearchProfile = Literal["quick", "research", "deep"]
ReasonCode = Literal[
    "search_provider_unavailable",
    "no_results_from_discovery",
    "no_urls_after_selection",
    "all_crawls_failed",
    "no_chunks_after_dedup",
    "no_chunks_after_rerank",
    "no_evidence_after_quality_gate",
    "no_evidence_after_output_budget",
]
DiscoveryStatus = Literal["ok", "degraded", "unavailable"]
DiscoveryFailureReason = Literal[
    "timeout",
    "transport_error",
    "upstream_status_error",
    "malformed_response",
    "unavailable",
]
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
FetchOutcomeReason = Literal[
    "content",
    "empty_shell",
    "challenge",
    "robots_refused",
    "upstream_timeout",
    "deadline_cancelled",
    "unsafe_redirect",
    "unsupported_content",
    "unsupported_capability",
    "content_too_large",
    "extraction_empty",
    "malformed_upstream_response",
    "upstream_failure",
    "rate_limited",
    "capacity_unavailable",
    "unsafe_target",
    "local_processing_failure",
]


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
    score_components: list["ScoreComponent"] = Field(default_factory=list, max_length=4)

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


class ScoreComponent(BaseModel):
    name: str = Field(max_length=64)
    score: float
    strategy: str = Field(max_length=128)


class UrlDiagnostic(BaseModel):
    url: str
    title: str
    engine: str
    engines: list[str] = Field(default_factory=list, max_length=16)
    positions: list[int] = Field(default_factory=list, max_length=16)
    contributing_subqueries: list[str] = Field(default_factory=list, max_length=8)
    contribution_count: int = Field(default=1, ge=1)
    independent_subquery_count: int = Field(default=0, ge=0)
    best_upstream_score: float | None = None
    discovery_score: float
    lexical_selection_score: float = 0.0
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


class SubqueryDiagnostic(BaseModel):
    query: str = Field(max_length=MAX_QUERY_CHARS)
    status: Literal["ok", "failed"]
    elapsed_ms: int = Field(ge=0)
    result_count: int = Field(ge=0)
    failure_reason: DiscoveryFailureReason | None = None


class EngineContribution(BaseModel):
    engine: str = Field(max_length=128)
    contribution_count: int = Field(ge=1)


class DiagnosticOmission(BaseModel):
    reason: str = Field(max_length=64)
    count: int = Field(ge=1)


class EvidenceQualityDrop(BaseModel):
    reason: str = Field(max_length=64)
    count: int = Field(ge=1)


class FetchOutcomeCount(BaseModel):
    outcome: FetchOutcomeReason
    count: int = Field(ge=1)


class FetchDiagnostic(BaseModel):
    requested_url: str
    final_url: str | None = None
    outcome: FetchOutcomeReason
    retryable: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None = None
    title: str | None = None
    retrieval_method: str | None = None
    elapsed_ms: int = Field(ge=0)


class SearchStats(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    sub_queries: list[str] = Field(default_factory=list)
    subquery_diagnostics: list[SubqueryDiagnostic] = Field(
        default_factory=list,
        max_length=MAX_SUBQUERY_COUNT,
    )
    discovery_status: DiscoveryStatus = "ok"
    unresponsive_engines: list[UnresponsiveEngine] = Field(default_factory=list)
    engine_contributions: list[EngineContribution] = Field(default_factory=list, max_length=32)
    urls_discovered: int = 0
    urls_selected: int = 0
    url_diagnostics: list[UrlDiagnostic] = Field(default_factory=list)
    url_diagnostics_omitted: list[DiagnosticOmission] = Field(default_factory=list, max_length=16)
    urls_crawled_ok: int = 0
    urls_crawled_failed: int = 0
    fetch_outcomes: list[FetchDiagnostic] = Field(default_factory=list, max_length=MAX_SELECTED_URLS)
    fetch_outcome_counts: list[FetchOutcomeCount] = Field(default_factory=list, max_length=16)
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
    evidence_quality_strategy: str | None = None
    evidence_quality_chunks_dropped: int = 0
    evidence_quality_drops: list[EvidenceQualityDrop] = Field(default_factory=list, max_length=8)
    evidence_items_omitted: int = 0
    evidence_bytes_omitted: int = 0
    raw_markdown_omitted: int = 0
    chunk_strategy: str | None = None
    embedding_degraded: bool = False
    reranked: bool = False
    tokens_returned: int = 0
    elapsed_ms: int = 0
    reason: ReasonCode | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-resource-policy": RESOURCE_POLICY_SUMMARY})

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


class TargetWatchSnapshot(BaseModel):
    text: str = Field(max_length=MAX_TARGET_TEXT_CHARS)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=MAX_TARGET_ATTRIBUTES)


class TargetWatchResult(BaseModel):
    resolution: Literal["found", "missing", "ambiguous", "unsupported"]
    state: Literal["new", "same", "changed", "removed"] | None = None
    previous: TargetWatchSnapshot | None = None
    current: TargetWatchSnapshot | None = None
    condition_met: bool = False


class FetchCacheInfo(BaseModel):
    state: Literal["fresh", "stale", "revalidated", "bypass"]
    reason: str = Field(max_length=64)
    age_s: float | None = Field(default=None, ge=0)


class PageChange(BaseModel):
    state: Literal["new", "same", "changed", "removed"]
    previous_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    current_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    previous_status: int | None = Field(default=None, ge=100, le=599)
    current_status: int | None = Field(default=None, ge=100, le=599)


class PageDiff(BaseModel):
    truncated: bool
    previous_lines: int = Field(ge=0)
    current_lines: int = Field(ge=0)
    added_lines: list[str] = Field(default_factory=list, max_length=MAX_DIFF_OUTPUT_LINES)
    removed_lines: list[str] = Field(default_factory=list, max_length=MAX_DIFF_OUTPUT_LINES)
    changed_sections: list[str] = Field(default_factory=list, max_length=MAX_DIFF_OUTPUT_LINES)


class FetchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-resource-policy": RESOURCE_POLICY_SUMMARY})

    urls: list[Annotated[str, Field(min_length=1, max_length=MAX_FETCH_URL_BYTES)]] = Field(
        min_length=1,
        max_length=MAX_FETCH_URLS,
    )
    capabilities: list[FetchCapability] = Field(
        default_factory=lambda: list(DEFAULT_FETCH_CAPABILITIES),
        min_length=1,
        max_length=len(FETCH_CAPABILITIES),
    )
    force_refresh: bool = False
    stale_while_revalidate: bool = False
    watch: TargetWatch | None = None

    @model_validator(mode="after")
    def validate_unique_values(self) -> "FetchRequest":
        if len(self.urls) != len(set(self.urls)):
            raise ValueError("urls must not contain duplicates")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must not contain duplicates")
        return self


class FetchResult(BaseModel):
    requested_url: str
    final_url: str | None = None
    outcome: FetchOutcomeReason
    retryable: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None = None
    title: str | None = None
    retrieval_method: str | None = None
    elapsed_ms: int = Field(ge=0)
    capabilities: list[FetchCapability] = Field(default_factory=list)
    markdown: str | None = None
    raw_html: str | None = None
    links: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    response_headers: dict[
        Literal["content-type", "etag", "last-modified", "retry-after"],
        str,
    ] = Field(
        default_factory=dict
    )
    cache: FetchCacheInfo = Field(
        default_factory=lambda: FetchCacheInfo(state="bypass", reason="disabled")
    )
    change: PageChange | None = None
    diff: PageDiff | None = None
    watch: TargetWatchResult | None = None
    provenance: Literal["external_web"] = "external_web"
    trust: Literal["untrusted"] = "untrusted"


class FetchStats(BaseModel):
    requested: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    outcomes: list[FetchOutcomeCount]
    elapsed_ms: int = Field(ge=0)
    cache_fresh: int = Field(default=0, ge=0)
    cache_stale: int = Field(default=0, ge=0)
    cache_revalidated: int = Field(default=0, ge=0)
    cache_bypassed: int = Field(default=0, ge=0)


class FetchResponse(BaseModel):
    results: list[FetchResult]
    stats: FetchStats
    schema_version: Literal["thorondor.fetch.v1"] = "thorondor.fetch.v1"


SiteSource = Literal["seed", "sitemap", "link", "search"]
SiteState = Literal[
    "discovered",
    "admitted",
    "queued",
    "fetched",
    "filtered",
    "failed",
    "cancelled",
]
SiteOutcomeReason = Literal[
    "completed",
    "partial",
    "cancelled",
    "unsafe_seed",
    "unsafe_redirect",
    "robots_refused",
    "limit_reached",
    "upstream_timeout",
    "deadline_cancelled",
    "malformed_upstream_response",
    "upstream_failure",
    "rate_limited",
    "unsafe_target",
    "local_processing_failure",
    "no_admitted_urls",
]


class SiteRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-resource-policy": RESOURCE_POLICY_SUMMARY})

    url: str = Field(min_length=1, max_length=MAX_SITE_URL_BYTES)
    sitemap: Literal["include", "only", "skip"] = "include"
    max_depth: int = Field(default=2, ge=0, le=MAX_SITE_DEPTH)
    max_pages: int = Field(default=10, ge=1, le=MAX_SITE_PAGES)
    max_discovered_urls: int = Field(
        default=200,
        ge=1,
        le=MAX_SITE_DISCOVERED_URLS,
    )
    include_parent_paths: bool = False
    include_subdomains: bool = False
    include_paths: list[
        Annotated[str, Field(min_length=1, max_length=MAX_SITE_PATTERN_CHARS)]
    ] = Field(
        default_factory=list,
        max_length=MAX_SITE_PATTERNS,
    )
    exclude_paths: list[
        Annotated[str, Field(min_length=1, max_length=MAX_SITE_PATTERN_CHARS)]
    ] = Field(
        default_factory=list,
        max_length=MAX_SITE_PATTERNS,
    )
    query_parameters: Literal["preserve", "strip", "exclude"] = "preserve"
    allowed_file_extensions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SITE_EXTENSIONS),
        min_length=1,
        max_length=MAX_SITE_EXTENSIONS,
    )
    include_search: bool = False

    @model_validator(mode="after")
    def validate_site_policy(self) -> "SiteRequest":
        try:
            url_bytes = len(self.url.encode("utf-8"))
        except UnicodeError as exc:
            raise ValueError("url must be valid UTF-8") from exc
        if url_bytes > MAX_SITE_URL_BYTES:
            raise ValueError(f"url must not exceed {MAX_SITE_URL_BYTES} UTF-8 bytes")
        if len(self.include_paths) != len(set(self.include_paths)):
            raise ValueError("include_paths must not contain duplicates")
        if len(self.exclude_paths) != len(set(self.exclude_paths)):
            raise ValueError("exclude_paths must not contain duplicates")
        if any(not pattern.startswith("/") for pattern in self.include_paths + self.exclude_paths):
            raise ValueError("path patterns must start with /")
        normalized = [value.casefold() for value in self.allowed_file_extensions]
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_file_extensions must not contain duplicates")
        if any(
            value != "" and (not value.startswith(".") or len(value) > 16)
            for value in normalized
        ):
            raise ValueError("file extensions must be empty or start with .")
        return self


class MapRequest(SiteRequest):
    pass


class CrawlRequest(SiteRequest):
    capabilities: list[FetchCapability] = Field(
        default_factory=lambda: list(DEFAULT_FETCH_CAPABILITIES),
        min_length=1,
        max_length=len(FETCH_CAPABILITIES),
    )

    @model_validator(mode="after")
    def validate_crawl_capabilities(self) -> "CrawlRequest":
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must not contain duplicates")
        return self


class SiteUrlRecord(BaseModel):
    url: str
    depth: int = Field(ge=0)
    sources: list[SiteSource] = Field(min_length=1, max_length=4)
    states: list[SiteState] = Field(min_length=1, max_length=7)
    reason: str | None = Field(default=None, max_length=64)
    modified_at: str | None = Field(default=None, max_length=128)
    priority: float | None = Field(default=None, ge=0, le=1)
    provenance: Literal["external_web"] = "external_web"
    trust: Literal["untrusted"] = "untrusted"


class SiteSourceCount(BaseModel):
    source: SiteSource
    count: int = Field(ge=1)


class SiteStats(BaseModel):
    discovered: int = Field(ge=0)
    admitted: int = Field(ge=0)
    queued: int = Field(ge=0)
    fetched: int = Field(ge=0)
    filtered: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    omitted: int = Field(ge=0)
    discovery_limit_omitted: int = Field(default=0, ge=0)
    response_budget_omitted: int = Field(default=0, ge=0)
    results_omitted: int = Field(ge=0)
    pages_succeeded: int = Field(ge=0)
    pages_failed: int = Field(ge=0)
    sitemap_documents_attempted: int = Field(ge=0)
    sitemap_documents: int = Field(ge=0)
    sitemap_entries: int = Field(ge=0)
    sitemap_truncated: int = Field(ge=0)
    robots_documents_attempted: int = Field(default=0, ge=0)
    non_http_urls_skipped: int = Field(default=0, ge=0)
    robots_state: Literal["available", "unavailable", "unreachable"] | None = None
    robots_source: Literal["network", "cache"] | None = None
    source_counts: list[SiteSourceCount] = Field(default_factory=list, max_length=4)
    fetch_outcomes: list[FetchOutcomeCount] = Field(default_factory=list, max_length=17)
    elapsed_ms: int = Field(ge=0)


class MapResponse(BaseModel):
    requested_url: str
    effective_url: str | None = None
    requested_origin: str | None = None
    effective_origin: str | None = None
    outcome: SiteOutcomeReason
    urls: list[SiteUrlRecord]
    stats: SiteStats
    warnings: list[str] = Field(default_factory=list, max_length=32)
    schema_version: Literal["thorondor.map.v1"] = "thorondor.map.v1"


class CrawlResponse(BaseModel):
    requested_url: str
    effective_url: str | None = None
    requested_origin: str | None = None
    effective_origin: str | None = None
    outcome: SiteOutcomeReason
    urls: list[SiteUrlRecord]
    results: list[FetchResult]
    stats: SiteStats
    warnings: list[str] = Field(default_factory=list, max_length=32)
    schema_version: Literal["thorondor.crawl.v1"] = "thorondor.crawl.v1"
