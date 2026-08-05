"""Pydantic wire models for the Thorondor orchestrator."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class Passage(BaseModel):
    text: str
    score: float
    token_count: int
    citation_id: int
    provenance: Literal["external_web"] = "external_web"
    trust: Literal["untrusted"] = "untrusted"


class Citation(BaseModel):
    id: int
    url: str
    title: str
    published: str | None = None


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
