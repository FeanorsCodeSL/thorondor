"""Search pipeline orchestration."""
from dataclasses import dataclass, replace
import asyncio
from collections.abc import Callable
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
import logging
import time

from .assembly import ResultAssemblerImpl
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .content_dedup import content_dedup
from .interfaces import (
    ContentExtractor,
    MarkdownCleaner,
    CandidatePrefilter,
    QueryPlanner,
    ResultAssembler,
    Reranker,
    SearchDiscovery,
    SelectionPolicy,
    SemanticChunker,
)
from .merge import merge_dedup
from .models import Citation, Passage, RawMarkdown, SearchRequest, SearchResponse, SearchStats, UrlDiagnostic
from .observability import query_hash
from .types import DiscoveryResult, Page, ScoredChunk
from .url_safety import filter_safe_discovery_results, is_safe_crawl_url

logger = logging.getLogger(__name__)


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
    markdown_cleaner: MarkdownCleaner
    chunker: SemanticChunker
    candidate_prefilter: CandidatePrefilter
    reranker: Reranker
    assembler: ResultAssembler
    default_token_budget: int
    default_max_urls: int
    max_subqueries: int
    profile_defaults: dict[str, dict[str, int]]
    blocklist: set[str]
    url_safety: Callable[[list[DiscoveryResult]], list[DiscoveryResult]]
    relevance_score_floor: float | None
    domain_allowlist: set[str] | None
    allowlist_only: bool

    async def aclose(self) -> None:
        for component in (self.planner, self.discovery, self.extractor, self.chunker, self.reranker):
            close = getattr(component, "aclose", None)
            if close is not None:
                await close()


def _empty_response(query: str, stats: SearchStats, reason: str) -> SearchResponse:
    stats.reason = reason
    response = SearchResponse(query=query, passages=[], citations=[], stats=stats)
    _log_search_summary(query, stats)
    return response


def _log_search_summary(query: str, stats: SearchStats, reason: str | None = None) -> None:
    logger.info(
        "search_completed",
        extra={
            "event": "search_completed",
            "query_hash": query_hash(query),
            "sub_query_count": len(stats.sub_queries),
            "urls_discovered": stats.urls_discovered,
            "urls_selected": stats.urls_selected,
            "urls_crawled_ok": stats.urls_crawled_ok,
            "urls_crawled_failed": stats.urls_crawled_failed,
            "reranked": stats.reranked,
            "reason": reason if reason is not None else stats.reason,
            "elapsed_ms": stats.elapsed_ms,
        },
    )


def _apply_relevance_floor(
    scored: list[ScoredChunk],
    reranked: bool,
    relevance_score_floor: float | None,
) -> tuple[list[ScoredChunk], int, str | None]:
    if not reranked or relevance_score_floor is None:
        return scored, 0, None
    above_floor = [item for item in scored if item.score > relevance_score_floor]
    if not above_floor:
        return scored, 0, f"score>{relevance_score_floor}:kept_all_no_positive_scores"
    return (
        above_floor,
        len(scored) - len(above_floor),
        f"score>{relevance_score_floor}",
    )


async def run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse:
    started = time.perf_counter()
    profile_defaults = deps.profile_defaults.get(req.search_profile or "", {})
    token_budget = req.token_budget or profile_defaults.get("token_budget", deps.default_token_budget)
    max_urls = req.max_urls or profile_defaults.get("max_urls", deps.default_max_urls)
    max_passages = req.max_passages if req.max_passages is not None else profile_defaults.get("max_passages")
    stats = SearchStats()

    planned = await deps.planner.plan(req.query) if req.decompose else [req.query]
    subqueries = (planned or [req.query])[:deps.max_subqueries]
    stats.sub_queries = subqueries

    try:
        result_sets = await asyncio.gather(
            *(deps.discovery.search(query, req.freshness) for query in subqueries)
        )
    except DiscoveryUnavailable as exc:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        _log_search_summary(req.query, stats, "searxng_unavailable")
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
    select_with_diagnostics = getattr(deps.selector, "select_with_diagnostics", None)
    if select_with_diagnostics is not None:
        selected, selection_diagnostics = select_with_diagnostics(
            safe_candidates,
            max_urls,
            blocklist,
            allowlist,
            req.query,
        )
        stats.url_diagnostics = [
            UrlDiagnostic(
                url=decision.result.url,
                title=decision.result.title,
                engine=decision.result.engine,
                discovery_score=decision.result.score,
                selected=decision.selected,
                selection_reason=decision.selection_reason,
                filtered_reason=decision.filtered_reason,
            )
            for decision in selection_diagnostics
        ]
    else:
        selected = deps.selector.select(safe_candidates, max_urls, blocklist, allowlist, req.query)
        stats.url_diagnostics = [
            UrlDiagnostic(
                url=result.url,
                title=result.title,
                engine=result.engine,
                discovery_score=result.score,
                selected=True,
                selection_reason="selector",
            )
            for result in selected
        ]
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

    cleaned_pages = [deps.markdown_cleaner.clean(page) for page in pages]
    if cleaned_pages:
        stats.pages_cleaned = len(cleaned_pages)
        stats.markdown_chars_before = sum(item.chars_before for item in cleaned_pages)
        stats.markdown_chars_after = sum(item.chars_after for item in cleaned_pages)
        stats.markdown_blocks_dropped = sum(item.blocks_dropped for item in cleaned_pages)
        stats.markdown_cleaner_version = cleaned_pages[0].cleaner_version
        pages = [item.page for item in cleaned_pages]

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
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        _log_search_summary(req.query, stats, "chunker_unavailable")
        raise SearchDependencyUnavailable("chunker", "chunker_unavailable") from exc
    stats.chunks_produced = len(chunks)
    if chunks:
        stats.chunk_strategy = chunks[0].chunk_strategy
        stats.embedding_degraded = any(chunk.embedding_degraded for chunk in chunks)
    if not chunks:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_chunks_after_dedup")

    prefiltered = deps.candidate_prefilter.filter(req.query, chunks)
    chunks = prefiltered.chunks
    stats.chunks_prefiltered = prefiltered.chunks_prefiltered
    stats.chunks_sent_to_reranker = prefiltered.chunks_sent_to_reranker
    stats.prefilter_strategy = prefiltered.prefilter_strategy
    if not chunks:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_chunks_after_dedup")

    try:
        scored = await deps.reranker.rerank(req.query, chunks)
        stats.reranked = True
        stats.reranker_batches = getattr(deps.reranker, "last_batches", 1 if chunks else 0)
        stats.reranker_batches_failed = getattr(deps.reranker, "last_batches_failed", 0)
        stats.reranker_floor_filled = bool(getattr(deps.reranker, "last_floor_filled", False))
        stats.chunks_reranked = getattr(deps.reranker, "last_scored_count", len(scored))
    except RerankerUnavailable:
        scored = [ScoredChunk(chunk, 1.0 / (index + 1)) for index, chunk in enumerate(chunks)]
        stats.reranked = False
        stats.reranker_batches = getattr(deps.reranker, "last_batches", 0)
        stats.reranker_batches_failed = getattr(deps.reranker, "last_batches_failed", 0)
        stats.reranker_floor_filled = False
        stats.chunks_reranked = 0
    if not scored:
        stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _empty_response(req.query, stats, "no_chunks_after_rerank")

    scored, dropped_below_threshold, threshold_policy = _apply_relevance_floor(
        scored,
        stats.reranked,
        deps.relevance_score_floor,
    )
    stats.passages_dropped_below_threshold = dropped_below_threshold
    stats.relevance_threshold_policy = threshold_policy

    assembled_passages, assembled_citations = deps.assembler.assemble(scored, token_budget, max_passages)
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
            RawMarkdown(
                citation_id=citation.id,
                markdown=by_source_id[citation.source_id].original_markdown
                or by_source_id[citation.source_id].markdown,
            )
            for citation in assembled_citations
            if citation.source_id in by_source_id
        ]

    response = SearchResponse(
        query=req.query,
        passages=passages,
        citations=citations,
        stats=stats,
        raw_markdown=raw_markdown,
    )
    _log_search_summary(req.query, stats)
    return response


def build_deps_from_settings(settings) -> PipelineDeps:
    from .clients.chunker_client import ChunkerClient
    from .clients.crawl4ai_client import Crawl4aiExtractor
    from .clients.planner import IdentityPlanner, LlmPlanner
    from .clients.reranker_client import RerankerClient
    from .markdown_cleaner import MarkdownCleanerImpl
    from .prefilter import CandidatePrefilterImpl
    from .clients.searxng_client import SearxngDiscovery
    from .selection import SelectionPolicyImpl

    if settings.markdown_extractor.lower() != "trafilatura":
        raise RuntimeError("MARKDOWN_EXTRACTOR must be trafilatura")
    try:
        trafilatura = import_module("trafilatura")
        extractor_version = version("trafilatura")
    except (ImportError, PackageNotFoundError) as exc:
        raise RuntimeError("MARKDOWN_EXTRACTOR=trafilatura requires the trafilatura package") from exc

    planner = (
        LlmPlanner(settings.llm_endpoint, settings.llm_model, api_key=settings.llm_api_key)
        if settings.llm_endpoint and settings.llm_model
        else IdentityPlanner()
    )
    url_safety = lambda results: filter_safe_discovery_results(results, settings.url_safety_policy)
    crawl_url_safety = lambda url: is_safe_crawl_url(url, settings.url_safety_policy)

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
            validate_redirects=settings.crawl_validate_redirects,
            max_preflight_redirects=settings.crawl_max_preflight_redirects,
            url_safety=crawl_url_safety,
            api_key=settings.crawl4ai_api_key,
        ),
        markdown_cleaner=MarkdownCleanerImpl(
            extractor=trafilatura.extract,
            extractor_name=settings.markdown_extractor,
            extractor_version=extractor_version,
            favor_recall=settings.markdown_extractor_favor_recall,
            include_comments=settings.markdown_extractor_include_comments,
            include_tables=settings.markdown_extractor_include_tables,
            deduplicate=settings.markdown_extractor_deduplicate,
        ),
        chunker=ChunkerClient(settings.chunker_url, api_key=settings.chunker_api_key),
        candidate_prefilter=CandidatePrefilterImpl(),
        reranker=RerankerClient(
            settings.reranker_endpoint,
            settings.reranker_model,
            settings.reranker_path,
            api_key=settings.reranker_api_key,
            batch_size=settings.reranker_batch_size,
            timeout_s=settings.reranker_timeout_s,
        ),
        assembler=ResultAssemblerImpl(),
        default_token_budget=settings.default_token_budget,
        default_max_urls=settings.max_urls,
        max_subqueries=settings.max_subqueries,
        profile_defaults={
            name: {
                "token_budget": defaults.token_budget,
                "max_urls": defaults.max_urls,
                "max_passages": defaults.max_passages,
            }
            for name, defaults in settings.search_profiles.items()
        },
        blocklist=settings.domain_blocklist,
        relevance_score_floor=settings.relevance_score_floor,
        domain_allowlist=settings.domain_allowlist,
        allowlist_only=settings.allowlist_only,
        url_safety=url_safety,
    )
