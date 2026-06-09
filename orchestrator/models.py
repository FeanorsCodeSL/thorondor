"""Pydantic wire models for the Thorondor orchestrator."""
from typing import Literal

from pydantic import BaseModel, Field


class Passage(BaseModel):
    text: str
    score: float
    token_count: int
    citation_id: int


class Citation(BaseModel):
    id: int
    url: str
    title: str
    published: str | None = None


class RawMarkdown(BaseModel):
    citation_id: int
    markdown: str


class SearchStats(BaseModel):
    sub_queries: list[str] = Field(default_factory=list)
    urls_discovered: int = 0
    urls_selected: int = 0
    urls_crawled_ok: int = 0
    chunks_produced: int = 0
    chunks_reranked: int = 0
    reranked: bool = False
    tokens_returned: int = 0
    elapsed_ms: int = 0
    reason: str | None = None


class SearchRequest(BaseModel):
    query: str
    token_budget: int | None = None
    max_urls: int | None = None
    max_passages: int | None = None
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
