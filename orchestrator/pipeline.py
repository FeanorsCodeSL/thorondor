"""Search pipeline orchestration."""
from dataclasses import dataclass, replace
import asyncio
from collections.abc import Callable
import time

from .assembly import ResultAssemblerImpl
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .content_dedup import content_dedup
from .interfaces import (
    ContentExtractor,
    QueryPlanner,
    ResultAssembler,
    Reranker,
    SearchDiscovery,
    SelectionPolicy,
    SemanticChunker,
)
from .merge import merge_dedup
from .models import Citation, Passage, RawMarkdown, SearchRequest, SearchResponse, SearchStats
from .types import DiscoveryResult, Page, ScoredChunk
from .url_safety import filter_safe_discovery_results

MAX_SUBQUERIES = 3


class SearchDependencyUnavailable(Exception):
    def __init__(self, dependency: str, reason: str):
        super().__init__(reason)
        self.dependency = dependency
        self.reason = reason


@dataclass
class PipelineDeps:
    planner: QueryPlanner
    discovery: SearchDiscovery
    selector: SelectionPolicy
    extractor: ContentExtractor
    chunker: SemanticChunker
    reranker: Reranker
    assembler: ResultAssembler
    default_token_budget: int
    default_max_urls: int
    blocklist: set[str]
    domain_allowlist: set[str] | None = None
    allowlist_only: bool = False
    url_safety: Callable[[list[DiscoveryResult]], list[DiscoveryResult]] = filter_safe_discovery_results

    async def aclose(self) -> None:
        for component in (self.planner, self.discovery, self.extractor, self.chunker, self.reranker):
            close = getattr(component, "aclose", None)
            if close is not None:
                await close()


def _empty_response(query: str, stats: SearchStats, reason: str) -> SearchResponse:
    stats.reason = reason
    return SearchResponse(query=query, passages=[], citations=[], stats=stats)


async def run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse:
    started = time.perf_counter()
    token_budget = req.token_budget or deps.default_token_budget
    max_urls = req.max_urls or deps.default_max_urls
    stats = SearchStats()

    planned = await deps.planner.plan(req.query) if req.decompose else [req.query]
    subqueries = (planned or [req.query])[:MAX_SUBQUERIES]
    stats.sub_queries = subqueries

    try:
        result_sets = await asyncio.gather(
            *(deps.discovery.search(query, req.freshness) for query in subqueries)
        )
    except DiscoveryUnavailable as exc:
        raise SearchDependencyUnavailable("searxng", "searxng_unavailable") from exc

    merged = merge_dedup(result_sets)
    stats.urls_discovered = len(merged)
    if not merged:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_results_from_discovery")

    safe_candidates = deps.url_safety(merged)

    blocklist = set(deps.blocklist)
    if req.exclude_domains:
        blocklist.update(req.exclude_domains)
    allowlist = set(req.domains) if req.domains else None
    if deps.allowlist_only:
        operator_allowlist = set(deps.domain_allowlist or set())
        allowlist = operator_allowlist if allowlist is None else allowlist & operator_allowlist
    selected = deps.selector.select(safe_candidates, max_urls, blocklist, allowlist)
    stats.urls_selected = len(selected)
    if not selected:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_urls_after_selection")

    pages = await deps.extractor.extract([result.url for result in selected])
    stats.urls_crawled_ok = len(pages)
    stats.urls_crawled_failed = max(0, stats.urls_selected - stats.urls_crawled_ok)
    if not pages:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "all_crawls_failed")

    pages_before_dedup = len(pages)
    pages = [
        replace(page, source_id=index + 1)
        for index, page in enumerate(content_dedup(pages))
    ]
    stats.pages_after_dedup = len(pages)
    stats.pages_deduped = max(0, pages_before_dedup - len(pages))
    try:
        chunks = await deps.chunker.chunk(pages)
    except ChunkerUnavailable as exc:
        raise SearchDependencyUnavailable("chunker", "chunker_unavailable") from exc
    stats.chunks_produced = len(chunks)
    if chunks:
        stats.chunk_strategy = chunks[0].chunk_strategy
        stats.embedding_degraded = any(chunk.embedding_degraded for chunk in chunks)
    if not chunks:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_chunks_after_dedup")

    try:
        scored = await deps.reranker.rerank(req.query, chunks)
        stats.reranked = True
        stats.chunks_reranked = len(scored)
    except RerankerUnavailable:
        scored = [ScoredChunk(chunk, 1.0 / (index + 1)) for index, chunk in enumerate(chunks)]
        stats.reranked = False
        stats.chunks_reranked = 0
    if not scored:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_chunks_after_rerank")

    assembled_passages, assembled_citations = deps.assembler.assemble(scored, token_budget, req.max_passages)
    passages = [
        Passage(
            text=passage.text,
            score=passage.score,
            token_count=passage.token_count,
            citation_id=passage.citation_id,
        )
        for passage in assembled_passages
    ]
    citations = [
        Citation(id=citation.id, url=citation.url, title=citation.title)
        for citation in assembled_citations
    ]
    stats.tokens_returned = sum(p.token_count for p in passages)
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)

    raw_markdown = None
    if req.include_raw_markdown:
        by_source_id: dict[int, Page] = {
            page.source_id: page
            for page in pages
            if page.source_id is not None
        }
        raw_markdown = [
            RawMarkdown(citation_id=citation.id, markdown=by_source_id[citation.source_id].markdown)
            for citation in assembled_citations
            if citation.source_id in by_source_id
        ]

    return SearchResponse(
        query=req.query,
        passages=passages,
        citations=citations,
        stats=stats,
        raw_markdown=raw_markdown,
    )


def build_deps_from_settings(settings) -> PipelineDeps:
    from .clients.chunker_client import ChunkerClient
    from .clients.crawl4ai_client import Crawl4aiExtractor
    from .clients.planner import IdentityPlanner, LlmPlanner
    from .clients.reranker_client import RerankerClient
    from .clients.searxng_client import SearxngDiscovery
    from .selection import SelectionPolicyImpl

    planner = (
        LlmPlanner(settings.llm_endpoint, settings.llm_model, api_key=settings.llm_api_key)
        if settings.llm_endpoint and settings.llm_model
        else IdentityPlanner()
    )
    return PipelineDeps(
        planner=planner,
        discovery=SearxngDiscovery(settings.searxng_url, api_key=settings.searxng_api_key),
        selector=SelectionPolicyImpl(),
        extractor=Crawl4aiExtractor(
            settings.crawl4ai_url,
            settings.crawl_concurrency,
            settings.crawl_timeout_s,
            respect_robots_txt=settings.crawl_respect_robots_txt,
            per_host_concurrency=settings.crawl_per_host_concurrency,
            api_key=settings.crawl4ai_api_key,
        ),
        chunker=ChunkerClient(settings.chunker_url, api_key=settings.chunker_api_key),
        reranker=RerankerClient(
            settings.reranker_endpoint,
            settings.reranker_model,
            settings.reranker_path,
            api_key=settings.reranker_api_key,
        ),
        assembler=ResultAssemblerImpl(),
        default_token_budget=settings.default_token_budget,
        default_max_urls=settings.max_urls,
        blocklist=settings.domain_blocklist,
        domain_allowlist=settings.domain_allowlist,
        allowlist_only=settings.allowlist_only,
        url_safety=filter_safe_discovery_results,
    )
