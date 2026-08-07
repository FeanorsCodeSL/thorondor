"""Search pipeline orchestration."""
import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from importlib import import_module
from importlib.metadata import version
from inspect import isawaitable

from .assembly import ResultAssemblerImpl, truncate_to_char_count
from .clients.chunker_client import ChunkerUnavailable
from .clients.reranker_client import RerankerUnavailable
from .clients.searxng_client import DiscoveryUnavailable
from .content_dedup import content_dedup
from .crawl_job_store import SqliteCrawlJobStore
from .crawl_jobs import CrawlJobManager, disabled_crawl_jobs
from .evidence_metadata import extract_document_metadata
from .evidence_quality import filter_evidence_quality
from .interfaces import (
    CandidatePrefilter,
    ContentExtractor,
    MarkdownCleaner,
    PageCacheRepository,
    QueryPlanner,
    Reranker,
    ResultAssembler,
    SearchDiscovery,
    SelectionPolicy,
    SemanticChunker,
)
from .merge import merge_dedup
from .models import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_JOB_FAILURE_SUMMARIES,
    MAX_JOB_FAILURE_SUMMARY_CHARS,
    MAX_PASSAGES,
    MAX_QUERY_CHARS,
    MAX_RAW_MARKDOWN_BYTES,
    MAX_RAW_MARKDOWN_ITEM_BYTES,
    MAX_RAW_MARKDOWN_ITEMS,
    MAX_SELECTED_URLS,
    MAX_URL_DIAGNOSTICS,
    MAX_URL_DIAGNOSTICS_BYTES,
    Citation,
    DiagnosticOmission,
    EngineContribution,
    EvidenceQualityDrop,
    FetchDiagnostic,
    FetchOutcomeCount,
    Passage,
    RawMarkdown,
    SearchRequest,
    SearchResponse,
    SearchStats,
    SubqueryDiagnostic,
    UnresponsiveEngine,
    UrlDiagnostic,
)
from .normalize import canonical_host, host_for
from .observability import query_hash
from .outcome_codes import FetchOutcomeCode
from .page_cache import DisabledPageCache, PageRefreshCoordinator, SqlitePageCache
from .politeness import HostPoliteness
from .resource_policy import ResourcePolicy, RouteDeadlineExceeded, RuntimeAdmission
from .robots_policy import RobotsCache
from .selection import SelectionDecision
from .types import (
    DiscoveryOutcome,
    DiscoveryResult,
    DocumentMetadata,
    FetchStageOutcome,
    Page,
    RerankerTelemetry,
    RerankOutcome,
    ScoreComponent,
    ScoredChunk,
)
from .url_safety import (
    filter_safe_discovery_results_async,
    is_safe_crawl_url_async,
)

logger = logging.getLogger(__name__)

SEARCH_FETCH_CAPABILITIES = frozenset({"markdown", "javascript", "links", "metadata"})
RETRYABLE_FETCH_OUTCOMES = {
    FetchOutcomeCode.CAPACITY_UNAVAILABLE,
    FetchOutcomeCode.UPSTREAM_TIMEOUT,
    FetchOutcomeCode.DEADLINE_CANCELLED,
    FetchOutcomeCode.UPSTREAM_FAILURE,
    FetchOutcomeCode.RATE_LIMITED,
}


class SearchDependencyUnavailable(Exception):
    def __init__(self, dependency: str, reason: str):
        super().__init__(reason)
        self.dependency = dependency
        self.reason = reason


@dataclass(frozen=True)
class _DiscoveryAttempt:
    query: str
    outcome: DiscoveryOutcome | None
    error: str | None
    elapsed_ms: int


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
    url_safety: Callable[[list[DiscoveryResult]], list[DiscoveryResult] | Awaitable[list[DiscoveryResult]]]
    relevance_score_floor: float | None
    evidence_quality_enabled: bool
    domain_allowlist: set[str] | None
    allowlist_only: bool
    resource_policy: ResourcePolicy
    admission: RuntimeAdmission
    crawl_url_safety: Callable[[str], bool | Awaitable[bool]]
    crawler_robots_user_agent: str
    robots_cache: RobotsCache
    site_politeness: HostPoliteness
    site_max_cooldown_s: int
    max_robots_bytes: int
    max_sitemap_bytes: int
    max_sitemap_entries: int
    max_sitemap_documents: int
    crawl_respect_robots_txt: bool
    page_cache: PageCacheRepository
    page_refresh: PageRefreshCoordinator
    page_cache_ttl_s: int
    page_cache_stale_s: int
    page_cache_retention_s: int
    page_cache_raw_html_enabled: bool
    page_diff_max_input_lines: int
    page_diff_max_operations: int
    page_diff_max_output_lines: int
    crawl_jobs: CrawlJobManager

    async def start(self) -> None:
        await self.page_cache.start()
        await self.crawl_jobs.start()

    async def aclose(self) -> None:
        await self.crawl_jobs.aclose()
        await self.page_refresh.aclose()
        await self.page_cache.aclose()
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
            "sub_queries_failed": sum(
                item.status == "failed" for item in stats.subquery_diagnostics
            ),
            "urls_discovered": stats.urls_discovered,
            "urls_selected": stats.urls_selected,
            "urls_crawled_ok": stats.urls_crawled_ok,
            "urls_crawled_failed": stats.urls_crawled_failed,
            "reranked": stats.reranked,
            "evidence_quality_chunks_dropped": stats.evidence_quality_chunks_dropped,
            "evidence_items_omitted": stats.evidence_items_omitted,
            "raw_markdown_omitted": stats.raw_markdown_omitted,
            "reason": reason if reason is not None else stats.reason,
            "elapsed_ms": stats.elapsed_ms,
        },
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _request_limits(req: SearchRequest, deps: PipelineDeps) -> tuple[int, int, int | None]:
    profile_defaults = deps.profile_defaults.get(req.search_profile or "", {})
    token_budget = req.token_budget or profile_defaults.get("token_budget", deps.default_token_budget)
    max_urls = min(
        req.max_urls or profile_defaults.get("max_urls", deps.default_max_urls),
        deps.resource_policy.max_internal_fanout,
    )
    max_passages = req.max_passages if req.max_passages is not None else profile_defaults.get("max_passages")
    return token_budget, max_urls, max_passages


async def _planned_subqueries(req: SearchRequest, deps: PipelineDeps, stats: SearchStats) -> list[str]:
    try:
        async with asyncio.timeout(deps.resource_policy.discovery_stage_deadline_s):
            planned = await deps.planner.plan(req.query) if req.decompose else [req.query]
    except TimeoutError:
        planned = [req.query]
    limit = min(deps.max_subqueries, deps.resource_policy.max_internal_fanout)
    subqueries = [
        str(query)[:MAX_QUERY_CHARS]
        for query in (planned or [req.query])[:limit]
    ]
    stats.sub_queries = subqueries
    return subqueries


async def _discover_results(
    req: SearchRequest,
    deps: PipelineDeps,
    stats: SearchStats,
    subqueries: list[str],
    started: float,
) -> list[DiscoveryResult]:
    async def attempt(query: str) -> _DiscoveryAttempt:
        attempt_started = time.perf_counter()
        try:
            async with asyncio.timeout(deps.resource_policy.discovery_stage_deadline_s):
                outcome = await deps.discovery.search(query, req.freshness)
            return _DiscoveryAttempt(query, outcome, None, _elapsed_ms(attempt_started))
        except TimeoutError:
            return _DiscoveryAttempt(
                query,
                None,
                "timeout",
                _elapsed_ms(attempt_started),
            )
        except DiscoveryUnavailable as exc:
            failure_reason = (
                exc.reason
                if exc.reason
                in {
                    "timeout",
                    "transport_error",
                    "upstream_status_error",
                    "malformed_response",
                }
                else "unavailable"
            )
            return _DiscoveryAttempt(
                query,
                None,
                failure_reason,
                _elapsed_ms(attempt_started),
            )

    attempts = await asyncio.gather(*(attempt(query) for query in subqueries))
    stats.subquery_diagnostics = [
        SubqueryDiagnostic(
            query=item.query,
            status="ok" if item.outcome is not None else "failed",
            elapsed_ms=item.elapsed_ms,
            result_count=len(item.outcome.results) if item.outcome is not None else 0,
            failure_reason=item.error,
        )
        for item in attempts
    ]
    successful_attempts = [item for item in attempts if item.outcome is not None]
    if not successful_attempts:
        stats.elapsed_ms = _elapsed_ms(started)
        _log_search_summary(req.query, stats, "searxng_unavailable")
        raise SearchDependencyUnavailable("searxng", "searxng_unavailable")

    outcomes = [item.outcome for item in successful_attempts if item.outcome is not None]
    failed_attempts = len(attempts) - len(successful_attempts)

    failures = sorted(
        {
            (failure.engine, failure.reason)
            for outcome in outcomes
            for failure in outcome.unresponsive_engines
        }
    )
    stats.unresponsive_engines = [
        UnresponsiveEngine(
            engine=_truncate_utf8(engine, 128),
            reason=_truncate_utf8(reason, 256),
        )
        for engine, reason in failures[:32]
    ]
    if failures or failed_attempts:
        stats.discovery_status = "degraded"

    engine_counts: dict[str, int] = {}
    for outcome in outcomes:
        for result in outcome.results:
            engines = [item.engine for item in result.contributions if item.engine]
            if not engines and result.engine:
                engines = [result.engine]
            for engine in engines:
                engine_counts[engine] = engine_counts.get(engine, 0) + 1
    stats.engine_contributions = [
        EngineContribution(
            engine=_truncate_utf8(engine, 128),
            contribution_count=count,
        )
        for engine, count in sorted(
            engine_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:32]
    ]

    result_sets = [
        item.outcome.results
        for item in successful_attempts
        if item.outcome is not None
    ]
    merged = merge_dedup(
        result_sets,
        [item.query for item in successful_attempts],
    )
    if not merged and (failures or failed_attempts):
        stats.discovery_status = "unavailable"
    stats.urls_discovered = len(merged)
    return merged


def _selection_filters(req: SearchRequest, deps: PipelineDeps) -> tuple[set[str], set[str] | None]:
    blocklist = set(deps.blocklist)
    if req.exclude_domains:
        blocklist.update(req.exclude_domains)
    allowlist = set(req.domains) if req.domains else None
    if deps.allowlist_only:
        operator_allowlist = set(deps.domain_allowlist or set())
        allowlist = operator_allowlist if allowlist is None else allowlist & operator_allowlist
    return blocklist, allowlist


def _operator_domain_allows(url: str, settings) -> bool:
    host = host_for(url)
    if not host:
        return False
    blocked = {canonical_host(value) for value in settings.domain_blocklist}
    if any(host == value or host.endswith(f".{value}") for value in blocked if value):
        return False
    if not settings.allowlist_only:
        return True
    allowed = {canonical_host(value) for value in settings.domain_allowlist}
    return any(host == value or host.endswith(f".{value}") for value in allowed if value)


async def _safe_candidates(deps: PipelineDeps, candidates: list[DiscoveryResult]) -> list[DiscoveryResult]:
    try:
        filtered = deps.url_safety(candidates)
        if isawaitable(filtered):
            filtered = await filtered
    except Exception:
        return []
    return filtered if isinstance(filtered, list) else []


def _select_results(
    req: SearchRequest,
    deps: PipelineDeps,
    candidates: list[DiscoveryResult],
    max_urls: int,
    stats: SearchStats,
) -> list[DiscoveryResult]:
    blocklist, allowlist = _selection_filters(req, deps)
    select_with_diagnostics = getattr(deps.selector, "select_with_diagnostics", None)
    if select_with_diagnostics is None:
        selected = deps.selector.select(candidates, max_urls, blocklist, allowlist, req.query)
        decisions = [
            SelectionDecision(
                result,
                selected=True,
                selection_reason="selector",
            )
            for result in selected
        ]
        _set_bounded_url_diagnostics(stats, decisions)
        return selected

    selected, selection_diagnostics = select_with_diagnostics(
        candidates,
        max_urls,
        blocklist,
        allowlist,
        req.query,
    )
    _set_bounded_url_diagnostics(stats, selection_diagnostics)
    return selected


def _ordered_unique(values) -> list:
    return list(dict.fromkeys(values))


def _wire_url_diagnostic(
    result: DiscoveryResult,
    selected: bool,
    selection_reason: str | None,
    filtered_reason: str | None,
    lexical_score: float,
) -> UrlDiagnostic:
    contributions = result.contributions
    engines = _ordered_unique(item.engine for item in contributions if item.engine)
    if result.engine and result.engine not in engines:
        engines.insert(0, result.engine)
    positions = _ordered_unique(
        item.position for item in contributions if item.position is not None
    )
    subqueries = _ordered_unique(
        item.subquery
        for item in contributions
        if item.subquery and not item.subquery.startswith("result_set:")
    )
    return UrlDiagnostic(
        url=_truncate_utf8(result.url, 2048),
        title=_truncate_utf8(result.title, 512),
        engine=_truncate_utf8(result.engine, 128),
        engines=[_truncate_utf8(item, 128) for item in engines[:16]],
        positions=positions[:16],
        contributing_subqueries=[_truncate_utf8(item, 500) for item in subqueries[:8]],
        contribution_count=max(1, len(contributions)),
        independent_subquery_count=len(subqueries),
        best_upstream_score=result.score,
        discovery_score=result.score,
        lexical_selection_score=lexical_score,
        selected=selected,
        selection_reason=selection_reason,
        filtered_reason=filtered_reason,
    )


def _set_bounded_url_diagnostics(stats: SearchStats, decisions: list) -> None:
    ordered = [item for item in decisions if item.selected]
    ordered.extend(item for item in decisions if not item.selected)
    kept: list[UrlDiagnostic] = []
    omitted: dict[str, int] = {}
    used_bytes = 2
    for decision in ordered:
        diagnostic = _wire_url_diagnostic(
            decision.result,
            decision.selected,
            decision.selection_reason,
            decision.filtered_reason,
            decision.lexical_score,
        )
        item_bytes = len(diagnostic.model_dump_json().encode("utf-8"))
        additional_bytes = item_bytes + (1 if kept else 0)
        if (
            len(kept) < MAX_URL_DIAGNOSTICS
            and used_bytes + additional_bytes <= MAX_URL_DIAGNOSTICS_BYTES
        ):
            kept.append(diagnostic)
            used_bytes += additional_bytes
            continue
        reason = decision.filtered_reason or "selected_over_budget"
        omitted[reason] = omitted.get(reason, 0) + 1
    stats.url_diagnostics = kept
    stats.url_diagnostics_omitted = [
        DiagnosticOmission(reason=reason, count=count)
        for reason, count in sorted(omitted.items())[:16]
    ]


def _set_fetch_diagnostics(
    stats: SearchStats,
    outcomes: list[FetchStageOutcome],
) -> None:
    stats.fetch_outcomes = [
        FetchDiagnostic(
            requested_url=item.requested_url,
            final_url=item.final_url,
            outcome=item.code.value,
            retryable=item.code in RETRYABLE_FETCH_OUTCOMES,
            status_code=item.status_code,
            content_type=item.content_type,
            title=item.title,
            retrieval_method=item.retrieval_method or None,
            elapsed_ms=item.elapsed_ms,
        )
        for item in outcomes[:MAX_SELECTED_URLS]
    ]
    counts = Counter(item.code.value for item in outcomes)
    stats.fetch_outcome_counts = [
        FetchOutcomeCount(outcome=outcome, count=count)
        for outcome, count in sorted(counts.items())
    ]


async def _fetch_pages(
    deps: PipelineDeps,
    urls: list[str],
    stats: SearchStats,
) -> list[Page]:
    fetch = getattr(deps.extractor, "fetch", None)
    if fetch is None:
        pages = await deps.extractor.extract(urls)
        page_by_url = {page.requested_url or page.url: page for page in pages}
        outcomes = [
            FetchStageOutcome(
                requested_url=url,
                final_url=(page.final_url or page.url) if page is not None else None,
                code=(
                    FetchOutcomeCode.CONTENT
                    if page is not None
                    else FetchOutcomeCode.UPSTREAM_FAILURE
                ),
                retrieval_method="legacy_extractor",
                elapsed_ms=0,
                status_code=page.status_code if page is not None else None,
                content_type=page.content_type if page is not None else None,
                title=page.title if page is not None else None,
                page=page,
            )
            for url in urls
            for page in [page_by_url.get(url)]
        ]
    else:
        try:
            async with asyncio.timeout(deps.resource_policy.crawl_stage_deadline_s):
                outcomes = await fetch(urls, SEARCH_FETCH_CAPABILITIES, False)
        except TimeoutError:
            outcomes = [
                FetchStageOutcome(
                    requested_url=url,
                    final_url=None,
                    code=FetchOutcomeCode.DEADLINE_CANCELLED,
                    retrieval_method="crawl4ai_browser",
                    elapsed_ms=int(deps.resource_policy.crawl_stage_deadline_s * 1000),
                )
                for url in urls
            ]
        pages = [
            item.page
            for item in outcomes
            if item.code == FetchOutcomeCode.CONTENT and item.page is not None
        ]
    _set_fetch_diagnostics(stats, outcomes)
    stats.urls_crawled_ok = sum(
        item.code == FetchOutcomeCode.CONTENT for item in outcomes
    )
    stats.urls_crawled_failed = max(0, len(urls) - stats.urls_crawled_ok)
    return pages


def _clean_pages(deps: PipelineDeps, pages: list[Page], stats: SearchStats) -> list[Page]:
    cleaned_pages = [deps.markdown_cleaner.clean(page) for page in pages]
    if not cleaned_pages:
        return pages
    stats.pages_cleaned = len(cleaned_pages)
    stats.markdown_chars_before = sum(item.chars_before for item in cleaned_pages)
    stats.markdown_chars_after = sum(item.chars_after for item in cleaned_pages)
    stats.markdown_blocks_dropped = sum(item.blocks_dropped for item in cleaned_pages)
    stats.markdown_cleaner_version = cleaned_pages[0].cleaner_version
    return [
        replace(item.page, evidence_metadata=extract_document_metadata(item.page))
        for item in cleaned_pages
    ]


def _attach_discovery_metadata(
    pages: list[Page],
    selected: list[DiscoveryResult],
) -> list[Page]:
    published_by_url = {
        result.url: result.published_at
        for result in selected
        if result.published_at is not None
    }
    return [
        replace(
            page,
            discovery_published_at=published_by_url.get(page.requested_url or page.url),
        )
        for page in pages
    ]


def _wire_metadata(metadata: DocumentMetadata | None) -> dict | None:
    if metadata is None:
        return None
    selected = {candidate.field: candidate for candidate in metadata.selected}
    return {
        **{
            field: {
                "value": candidate.value,
                "source": candidate.source,
                "confidence": candidate.confidence,
            }
            for field, candidate in selected.items()
        },
        "conflicts": [
            {
                "field": conflict.field,
                "candidates": [
                    {
                        "value": candidate.value,
                        "source": candidate.source,
                        "confidence": candidate.confidence,
                    }
                    for candidate in conflict.candidates
                ],
            }
            for conflict in metadata.conflicts
        ],
    }


def _dedupe_pages(pages: list[Page], stats: SearchStats) -> list[Page]:
    pages_before_dedup = len(pages)
    deduped = [
        replace(page, source_id=index + 1)
        for index, page in enumerate(content_dedup(pages))
    ]
    stats.pages_after_dedup = len(deduped)
    stats.pages_deduped = max(0, pages_before_dedup - len(deduped))
    return deduped


async def _chunk_pages(
    req: SearchRequest,
    deps: PipelineDeps,
    pages: list[Page],
    stats: SearchStats,
    started: float,
) -> list:
    try:
        async with asyncio.timeout(deps.resource_policy.chunk_stage_deadline_s):
            chunks = await deps.chunker.chunk(pages)
    except TimeoutError as exc:
        stats.elapsed_ms = _elapsed_ms(started)
        _log_search_summary(req.query, stats, "chunker_timeout")
        raise SearchDependencyUnavailable("chunker", "chunker_timeout") from exc
    except ChunkerUnavailable as exc:
        stats.elapsed_ms = _elapsed_ms(started)
        _log_search_summary(req.query, stats, "chunker_unavailable")
        raise SearchDependencyUnavailable("chunker", "chunker_unavailable") from exc
    stats.chunks_produced = len(chunks)
    if chunks:
        stats.chunk_strategy = chunks[0].chunk_strategy
        stats.embedding_degraded = any(chunk.embedding_degraded for chunk in chunks)
    return chunks


def _prefilter_chunks(req: SearchRequest, deps: PipelineDeps, chunks: list, stats: SearchStats) -> list:
    prefiltered = deps.candidate_prefilter.filter(req.query, chunks)
    stats.chunks_prefiltered = prefiltered.chunks_prefiltered
    stats.chunks_sent_to_reranker = prefiltered.chunks_sent_to_reranker
    stats.prefilter_strategy = prefiltered.prefilter_strategy
    return prefiltered.chunks


async def _rerank_chunks(req: SearchRequest, deps: PipelineDeps, chunks: list, stats: SearchStats) -> list[ScoredChunk]:
    try:
        async with asyncio.timeout(deps.resource_policy.rerank_stage_deadline_s):
            result = await deps.reranker.rerank(req.query, chunks)
        if isinstance(result, RerankOutcome):
            outcome = result
        else:
            scored_result = list(result)
            outcome = RerankOutcome(
                scored_result,
                RerankerTelemetry(
                    batches=1 if chunks else 0,
                    scored_count=len(scored_result),
                ),
                "reranker@1",
            )
        scored = [
            item
            if item.score_components
            else replace(
                item,
                score_components=(
                    ScoreComponent("reranker", item.score, outcome.strategy),
                ),
            )
            for item in outcome.scored
        ]
        stats.reranked = True
        stats.reranker_batches = outcome.telemetry.batches
        stats.reranker_batches_failed = outcome.telemetry.batches_failed
        stats.reranker_floor_filled = outcome.telemetry.floor_filled
        stats.chunks_reranked = outcome.telemetry.scored_count
        return scored
    except (RerankerUnavailable, TimeoutError) as exc:
        telemetry = (
            exc.telemetry
            if isinstance(exc, RerankerUnavailable)
            else RerankerTelemetry()
        )
        stats.reranked = False
        stats.reranker_batches = telemetry.batches
        stats.reranker_batches_failed = telemetry.batches_failed
        stats.reranker_floor_filled = False
        stats.chunks_reranked = 0
        return [
            ScoredChunk(
                chunk,
                1.0 / (index + 1),
                (
                    ScoreComponent(
                        "position_fallback",
                        1.0 / (index + 1),
                        "position-fallback@1",
                    ),
                ),
            )
            for index, chunk in enumerate(chunks)
        ]


def _apply_relevance_floor(
    scored: list[ScoredChunk],
    reranked: bool,
    relevance_score_floor: float | None,
) -> tuple[list[ScoredChunk], int, str | None]:
    if not reranked or relevance_score_floor is None:
        return scored, 0, None
    above_floor = [item for item in scored if item.score > relevance_score_floor]
    return (
        above_floor,
        len(scored) - len(above_floor),
        f"score>{relevance_score_floor}",
    )


def _wire_scored_passage(item: ScoredChunk) -> Passage:
    return Passage(
        text=item.chunk.text,
        score=item.score,
        token_count=item.chunk.token_count,
        citation_id=MAX_PASSAGES,
        start_index=item.chunk.start_index,
        end_index=item.chunk.end_index,
        verbatim=item.chunk.verbatim,
        document_id=item.chunk.document_id,
        evidence_id=item.chunk.evidence_id,
        section_heading=item.chunk.section_heading,
        score_components=[
            {
                "name": component.name,
                "score": component.score,
                "strategy": component.strategy,
            }
            for component in item.score_components
        ],
    )


def _fit_evidence_item(item: ScoredChunk) -> tuple[ScoredChunk | None, int]:
    original_bytes = len(_wire_scored_passage(item).model_dump_json().encode("utf-8"))
    if original_bytes <= MAX_EVIDENCE_ITEM_BYTES:
        return item, original_bytes
    lower = 1
    upper = len(item.chunk.text) - 1
    best_item = None
    best_bytes = 0
    while lower <= upper:
        midpoint = (lower + upper) // 2
        candidate = truncate_to_char_count(item, midpoint)
        if candidate is None:
            lower = midpoint + 1
            continue
        candidate_bytes = len(
            _wire_scored_passage(candidate).model_dump_json().encode("utf-8")
        )
        if candidate_bytes <= MAX_EVIDENCE_ITEM_BYTES:
            best_item = candidate
            best_bytes = candidate_bytes
            lower = midpoint + 1
        else:
            upper = midpoint - 1
    return best_item, best_bytes or original_bytes


def _bound_evidence(scored: list[ScoredChunk], stats: SearchStats) -> list[ScoredChunk]:
    bounded = []
    used_bytes = 2
    for item in sorted(
        scored,
        key=lambda value: (
            -value.score,
            value.chunk.source_url,
            value.chunk.position,
        ),
    ):
        fitted, item_bytes = _fit_evidence_item(item)
        additional_bytes = item_bytes + (1 if bounded else 0)
        if (
            fitted is None
            or len(bounded) >= MAX_PASSAGES
            or used_bytes + additional_bytes > MAX_EVIDENCE_BYTES
        ):
            stats.evidence_items_omitted += 1
            stats.evidence_bytes_omitted += item_bytes
            continue
        bounded.append(fitted)
        used_bytes += additional_bytes
    return bounded


def _build_raw_markdown(
    req: SearchRequest,
    pages: list[Page],
    assembled_citations: list,
    stats: SearchStats,
) -> list[RawMarkdown] | None:
    if not req.include_raw_markdown:
        return None
    by_source_id: dict[int, Page] = {
        page.source_id: page
        for page in pages
        if page.source_id is not None
    }
    raw_markdown = []
    used_bytes = 2
    for citation in assembled_citations:
        if citation.source_id not in by_source_id:
            continue
        page = by_source_id[citation.source_id]
        item = RawMarkdown(
            citation_id=citation.id,
            markdown=page.original_markdown or page.markdown,
            cleaned_markdown=page.markdown,
            document_id=citation.document_id,
        )
        item_bytes = len(item.model_dump_json().encode("utf-8"))
        additional_bytes = item_bytes + (1 if raw_markdown else 0)
        if (
            len(raw_markdown) >= MAX_RAW_MARKDOWN_ITEMS
            or item_bytes > MAX_RAW_MARKDOWN_ITEM_BYTES
            or used_bytes + additional_bytes > MAX_RAW_MARKDOWN_BYTES
        ):
            stats.raw_markdown_omitted += 1
            continue
        raw_markdown.append(item)
        used_bytes += additional_bytes
    return raw_markdown


async def _run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse:
    started = time.perf_counter()
    token_budget, max_urls, max_passages = _request_limits(req, deps)
    stats = SearchStats()

    subqueries = await _planned_subqueries(req, deps, stats)
    merged = await _discover_results(req, deps, stats, subqueries, started)
    if not merged:
        stats.elapsed_ms = _elapsed_ms(started)
        reason = (
            "search_provider_unavailable"
            if stats.discovery_status == "unavailable"
            else "no_results_from_discovery"
        )
        return _empty_response(req.query, stats, reason)

    safe_candidates = await _safe_candidates(deps, merged)
    selected = _select_results(req, deps, safe_candidates, max_urls, stats)
    stats.urls_selected = len(selected)
    if not selected:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_urls_after_selection")

    pages = _attach_discovery_metadata(
        await _fetch_pages(deps, [result.url for result in selected], stats),
        selected,
    )
    if not pages:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "all_crawls_failed")

    pages = _dedupe_pages(_clean_pages(deps, pages, stats), stats)
    chunks = await _chunk_pages(req, deps, pages, stats, started)
    if not chunks:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_chunks_after_dedup")

    chunks = _prefilter_chunks(req, deps, chunks, stats)
    if not chunks:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_chunks_after_dedup")

    scored = await _rerank_chunks(req, deps, chunks, stats)
    if not scored:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_chunks_after_rerank")

    scored, dropped_below_threshold, threshold_policy = _apply_relevance_floor(
        scored,
        stats.reranked,
        deps.relevance_score_floor,
    )
    stats.passages_dropped_below_threshold = dropped_below_threshold
    stats.relevance_threshold_policy = threshold_policy
    if not scored:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_chunks_after_rerank")

    if deps.evidence_quality_enabled:
        quality_outcome = filter_evidence_quality(scored)
        stats.evidence_quality_strategy = quality_outcome.strategy
        stats.evidence_quality_chunks_dropped = sum(
            quality_outcome.dropped_by_rule.values()
        )
        stats.evidence_quality_drops = [
            EvidenceQualityDrop(reason=reason, count=count)
            for reason, count in sorted(quality_outcome.dropped_by_rule.items())
        ]
        scored = quality_outcome.scored
        if not scored:
            stats.elapsed_ms = _elapsed_ms(started)
            return _empty_response(req.query, stats, "no_evidence_after_quality_gate")

    scored = _bound_evidence(scored, stats)
    if not scored:
        stats.elapsed_ms = _elapsed_ms(started)
        return _empty_response(req.query, stats, "no_evidence_after_output_budget")

    assembled_passages, assembled_citations = deps.assembler.assemble(
        scored,
        token_budget,
        max_passages,
    )
    passages = [
        Passage(
            text=passage.text,
            score=passage.score,
            token_count=passage.token_count,
            citation_id=passage.citation_id,
            start_index=passage.start_index,
            end_index=passage.end_index,
            verbatim=passage.verbatim,
            document_id=passage.document_id,
            evidence_id=passage.evidence_id,
            section_heading=passage.section_heading,
            score_components=[
                {
                    "name": component.name,
                    "score": component.score,
                    "strategy": component.strategy,
                }
                for component in passage.score_components
            ],
        )
        for passage in assembled_passages
    ]
    citations = [
        Citation(
            id=citation.id,
            url=citation.url,
            title=citation.title,
            published=(
                citation.evidence_metadata.get("published_at").value
                if citation.evidence_metadata is not None
                and citation.evidence_metadata.get("published_at") is not None
                else None
            ),
            modified_at=(
                citation.evidence_metadata.get("modified_at").value
                if citation.evidence_metadata is not None
                and citation.evidence_metadata.get("modified_at") is not None
                else None
            ),
            document_id=citation.document_id,
            evidence_spans=[
                {
                    "start_index": span.start_index,
                    "end_index": span.end_index,
                    "verbatim": span.verbatim,
                    "evidence_id": span.evidence_id,
                    "section_heading": span.section_heading,
                }
                for span in citation.evidence_spans
            ],
            metadata=_wire_metadata(citation.evidence_metadata),
        )
        for citation in assembled_citations
    ]
    stats.tokens_returned = sum(p.token_count for p in passages)
    stats.elapsed_ms = _elapsed_ms(started)

    response = SearchResponse(
        query=req.query,
        passages=passages,
        citations=citations,
        stats=stats,
        raw_markdown=_build_raw_markdown(req, pages, assembled_citations, stats),
    )
    _log_search_summary(req.query, stats)
    return response


async def run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse:
    async with deps.admission.search_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.search_route_deadline_s):
                return await _run_search(req, deps)
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("search") from exc


def build_deps_from_settings(settings) -> PipelineDeps:
    from .clients.chunker_client import ChunkerClient
    from .clients.crawl4ai_client import Crawl4aiExtractor
    from .clients.planner import IdentityPlanner, LlmPlanner
    from .clients.reranker_client import RerankerClient
    from .clients.searxng_client import SearxngDiscovery
    from .markdown_cleaner import MarkdownCleanerImpl
    from .prefilter import CandidatePrefilterImpl
    from .selection import SelectionPolicyImpl
    from .site_pipeline import run_crawl_job

    if settings.markdown_extractor.lower() != "trafilatura":
        raise RuntimeError("MARKDOWN_EXTRACTOR must be trafilatura")
    try:
        trafilatura = import_module("trafilatura")
        extractor_version = version("trafilatura")
    except ImportError as exc:
        raise RuntimeError("MARKDOWN_EXTRACTOR=trafilatura requires the trafilatura package") from exc

    planner = (
        LlmPlanner(
            settings.llm_endpoint,
            settings.llm_model,
            api_key=settings.llm_api_key,
            timeout_s=settings.discovery_timeout_s,
        )
        if settings.llm_endpoint and settings.llm_model
        else IdentityPlanner()
    )
    async def url_safety(results: list[DiscoveryResult]) -> list[DiscoveryResult]:
        return await filter_safe_discovery_results_async(
            results,
            settings.url_safety_policy,
            settings.crawl_concurrency,
        )

    async def crawl_url_safety(url: str) -> bool:
        if not _operator_domain_allows(url, settings):
            return False
        return await is_safe_crawl_url_async(url, settings.url_safety_policy)

    resource_policy = ResourcePolicy(
        max_request_body_bytes=settings.max_request_body_bytes,
        max_response_body_bytes=settings.max_response_body_bytes,
        search_route_deadline_s=settings.search_route_deadline_s,
        fetch_route_deadline_s=settings.fetch_route_deadline_s,
        map_route_deadline_s=settings.map_route_deadline_s,
        site_crawl_route_deadline_s=settings.site_crawl_route_deadline_s,
        discovery_stage_deadline_s=settings.discovery_timeout_s,
        crawl_stage_deadline_s=settings.crawl_timeout_s,
        chunk_stage_deadline_s=settings.chunk_timeout_s,
        rerank_stage_deadline_s=settings.reranker_timeout_s,
        max_inflight_searches=settings.max_inflight_searches,
        max_inflight_fetches=settings.max_inflight_fetches,
        max_inflight_maps=settings.max_inflight_maps,
        max_inflight_crawls=settings.max_inflight_crawls,
        admission_wait_s=settings.admission_wait_s,
        admission_retry_after_s=settings.admission_retry_after_s,
        max_internal_fanout=settings.max_internal_fanout,
        max_content_bytes=settings.max_content_bytes,
        chunk_concurrency=settings.chunk_concurrency,
    )
    page_cache = (
        SqlitePageCache(settings.page_cache_path)
        if settings.page_cache_enabled
        else DisabledPageCache()
    )
    runtime_deps = None

    async def crawl_job_runner(request, on_result, on_failure, cancel_requested):
        return await run_crawl_job(
            request,
            runtime_deps,
            on_result,
            on_failure,
            cancel_requested,
            settings.crawl_job_attempt_deadline_s,
        )

    crawl_jobs = (
        CrawlJobManager(
            enabled=True,
            store=SqliteCrawlJobStore(
                settings.crawl_job_path,
                retention_s=settings.crawl_job_retention_s,
                tombstone_s=settings.crawl_job_expired_tombstone_s,
                max_failure_summaries=MAX_JOB_FAILURE_SUMMARIES,
                max_failure_summary_chars=MAX_JOB_FAILURE_SUMMARY_CHARS,
                max_result_item_bytes=settings.crawl_job_result_page_max_bytes,
                max_records=settings.crawl_job_max_records,
            ),
            runner=crawl_job_runner,
            synchronous_max_pages=settings.crawl_sync_max_pages,
            max_attempts=settings.crawl_job_max_attempts,
            retry_base_s=settings.crawl_job_retry_base_s,
            max_page_items=settings.crawl_job_result_page_max_items,
            max_page_bytes=settings.crawl_job_result_page_max_bytes,
            raw_html_enabled=settings.crawl_job_raw_html_enabled,
            max_inflight_requests=settings.crawl_job_max_inflight_requests,
            admission_wait_s=settings.admission_wait_s,
            admission_retry_after_s=settings.admission_retry_after_s,
            poll_interval_s=60,
        )
        if settings.crawl_jobs_enabled
        else disabled_crawl_jobs()
    )
    runtime_deps = PipelineDeps(
        planner=planner,
        discovery=SearxngDiscovery(
            settings.searxng_url,
            api_key=settings.searxng_api_key,
            timeout_s=settings.discovery_timeout_s,
        ),
        selector=SelectionPolicyImpl(),
        extractor=Crawl4aiExtractor(
            settings.crawl4ai_url,
            settings.crawl_concurrency,
            settings.crawl_timeout_s,
            respect_robots_txt=settings.crawl_respect_robots_txt,
            per_host_concurrency=settings.crawl_per_host_concurrency,
            crawler_user_agent=settings.crawler_user_agent,
            url_safety=crawl_url_safety,
            api_key=settings.crawl4ai_api_key,
            max_content_bytes=settings.max_content_bytes,
            max_response_body_bytes=settings.max_response_body_bytes,
            admission_wait_s=settings.admission_wait_s,
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
        chunker=ChunkerClient(
            settings.chunker_url,
            api_key=settings.chunker_api_key,
            concurrency=settings.chunk_concurrency,
            timeout_s=settings.chunk_timeout_s,
        ),
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
        evidence_quality_enabled=settings.evidence_quality_enabled,
        domain_allowlist=settings.domain_allowlist,
        allowlist_only=settings.allowlist_only,
        url_safety=url_safety,
        crawl_url_safety=crawl_url_safety,
        resource_policy=resource_policy,
        admission=RuntimeAdmission(resource_policy),
        crawler_robots_user_agent=settings.crawler_robots_user_agent,
        robots_cache=RobotsCache(settings.robots_cache_ttl_s),
        site_politeness=HostPoliteness(
            default_delay_s=settings.site_default_delay_s,
            max_jitter_s=settings.site_max_jitter_s,
            max_cooldown_s=settings.site_max_cooldown_s,
        ),
        site_max_cooldown_s=settings.site_max_cooldown_s,
        max_robots_bytes=settings.max_robots_bytes,
        max_sitemap_bytes=settings.max_sitemap_bytes,
        max_sitemap_entries=settings.max_sitemap_entries,
        max_sitemap_documents=settings.max_sitemap_documents,
        crawl_respect_robots_txt=settings.crawl_respect_robots_txt,
        page_cache=page_cache,
        page_refresh=PageRefreshCoordinator(),
        page_cache_ttl_s=settings.page_cache_ttl_s,
        page_cache_stale_s=settings.page_cache_stale_s,
        page_cache_retention_s=settings.page_cache_retention_s,
        page_cache_raw_html_enabled=settings.page_cache_raw_html_enabled,
        page_diff_max_input_lines=settings.page_diff_max_input_lines,
        page_diff_max_operations=settings.page_diff_max_operations,
        page_diff_max_output_lines=settings.page_diff_max_output_lines,
        crawl_jobs=crawl_jobs,
    )
    return runtime_deps
