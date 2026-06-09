"""Search pipeline orchestration."""
from dataclasses import dataclass
import asyncio
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
from .models import RawMarkdown, SearchRequest, SearchResponse, SearchStats
from .normalize import normalize_url
from .types import Page, ScoredChunk


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


def _empty_response(query: str, stats: SearchStats, reason: str) -> SearchResponse:
    stats.reason = reason
    return SearchResponse(query=query, passages=[], citations=[], stats=stats)


async def run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse:
    started = time.perf_counter()
    token_budget = req.token_budget or deps.default_token_budget
    max_urls = req.max_urls or deps.default_max_urls
    stats = SearchStats()

    subqueries = await deps.planner.plan(req.query) if req.decompose else [req.query]
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

    blocklist = set(deps.blocklist)
    if req.exclude_domains:
        blocklist.update(host.lower() for host in req.exclude_domains)
    allowlist = {host.lower() for host in req.domains} if req.domains else None
    selected = deps.selector.select(merged, max_urls, blocklist, allowlist)
    stats.urls_selected = len(selected)
    if not selected:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_results_from_discovery")

    pages = await deps.extractor.extract([result.url for result in selected])
    stats.urls_crawled_ok = len(pages)
    if not pages:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "all_crawls_failed")

    pages = content_dedup(pages)
    try:
        chunks = await deps.chunker.chunk(pages)
    except ChunkerUnavailable as exc:
        raise SearchDependencyUnavailable("chunker", "chunker_unavailable") from exc
    stats.chunks_produced = len(chunks)
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

    passages, citations = deps.assembler.assemble(scored, token_budget, req.max_passages)
    stats.tokens_returned = sum(p.token_count for p in passages)
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)

    raw_markdown = None
    if req.include_raw_markdown:
        by_url: dict[str, Page] = {normalize_url(page.url): page for page in pages}
        raw_markdown = [
            RawMarkdown(citation_id=citation.id, markdown=by_url[normalize_url(citation.url)].markdown)
            for citation in citations
            if normalize_url(citation.url) in by_url
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
        LlmPlanner(settings.llm_endpoint, settings.llm_model)
        if settings.llm_endpoint and settings.llm_model
        else IdentityPlanner()
    )
    return PipelineDeps(
        planner=planner,
        discovery=SearxngDiscovery(settings.searxng_url),
        selector=SelectionPolicyImpl(),
        extractor=Crawl4aiExtractor(settings.crawl4ai_url, settings.crawl_concurrency, settings.crawl_timeout_s),
        chunker=ChunkerClient(settings.chunker_url),
        reranker=RerankerClient(settings.reranker_endpoint, settings.reranker_model, settings.reranker_path),
        assembler=ResultAssemblerImpl(),
        default_token_budget=settings.default_token_budget,
        default_max_urls=settings.max_urls,
        blocklist=settings.domain_blocklist,
    )
