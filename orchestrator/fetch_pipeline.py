import asyncio
import time
from collections import Counter
from inspect import isawaitable

from .models import (
    FetchOutcomeCount,
    FetchRequest,
    FetchResponse,
    FetchResult,
    FetchStats,
)
from .outcome_codes import FetchOutcomeCode
from .resource_policy import RouteDeadlineExceeded
from .types import FetchStageOutcome

RETRYABLE_FETCH_OUTCOMES = {
    FetchOutcomeCode.UPSTREAM_TIMEOUT,
    FetchOutcomeCode.DEADLINE_CANCELLED,
    FetchOutcomeCode.UPSTREAM_FAILURE,
    FetchOutcomeCode.RATE_LIMITED,
    FetchOutcomeCode.CAPACITY_UNAVAILABLE,
}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _is_safe(deps, url: str) -> bool:
    try:
        result = deps.crawl_url_safety(url)
        if isawaitable(result):
            result = await result
        return result is True
    except Exception:
        return False


def _wire_result(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
) -> FetchResult:
    page = outcome.page
    code = outcome.code
    markdown = None
    raw_html = None
    if code == FetchOutcomeCode.CONTENT and page is not None:
        try:
            cleaned = cleaner.clean(page).page
            markdown = cleaned.markdown.strip() or page.markdown.strip()
            if not markdown:
                code = FetchOutcomeCode.EXTRACTION_EMPTY
            elif "raw_html" in capabilities:
                raw_html = page.html
        except Exception:
            code = FetchOutcomeCode.LOCAL_PROCESSING_FAILURE
    headers = {}
    if outcome.content_type:
        headers["content-type"] = outcome.content_type
    if outcome.etag:
        headers["etag"] = outcome.etag
    if outcome.last_modified:
        headers["last-modified"] = outcome.last_modified
    return FetchResult(
        requested_url=outcome.requested_url,
        final_url=outcome.final_url,
        outcome=code.value,
        retryable=code in RETRYABLE_FETCH_OUTCOMES,
        status_code=outcome.status_code,
        content_type=outcome.content_type,
        title=outcome.title,
        retrieval_method=outcome.retrieval_method or None,
        elapsed_ms=outcome.elapsed_ms,
        capabilities=sorted(capabilities),
        markdown=markdown if "markdown" in capabilities else None,
        raw_html=raw_html,
        links=outcome.links if "links" in capabilities else {},
        metadata=outcome.metadata if "metadata" in capabilities else {},
        response_headers=headers,
    )


async def _run_fetch(req: FetchRequest, deps) -> FetchResponse:
    started = time.perf_counter()
    capabilities = frozenset(req.capabilities)
    supported = frozenset(getattr(deps.extractor, "supported_capabilities", ()))
    unsupported = capabilities - supported
    outcomes: list[FetchStageOutcome] = []
    safe_urls = []
    if unsupported:
        outcomes = [
            FetchStageOutcome(
                requested_url=url,
                final_url=None,
                code=FetchOutcomeCode.UNSUPPORTED_CAPABILITY,
                retrieval_method="",
                elapsed_ms=0,
            )
            for url in req.urls
        ]
    else:
        for url in req.urls:
            if await _is_safe(deps, url):
                safe_urls.append(url)
            else:
                outcomes.append(
                    FetchStageOutcome(
                        requested_url=url,
                        final_url=None,
                        code=FetchOutcomeCode.UNSAFE_TARGET,
                        retrieval_method="",
                        elapsed_ms=0,
                    )
                )
        if safe_urls:
            fetched = await deps.extractor.fetch(
                safe_urls,
                capabilities,
                "raw_html" in capabilities,
            )
            by_url = {item.requested_url: item for item in fetched}
            outcomes.extend(
                by_url.get(
                    url,
                    FetchStageOutcome(
                        requested_url=url,
                        final_url=None,
                        code=FetchOutcomeCode.UPSTREAM_FAILURE,
                        retrieval_method="crawl4ai_browser",
                        elapsed_ms=0,
                    ),
                )
                for url in safe_urls
            )
    order = {url: index for index, url in enumerate(req.urls)}
    outcomes.sort(key=lambda item: order[item.requested_url])
    results = [
        _wire_result(outcome, capabilities, deps.markdown_cleaner)
        for outcome in outcomes
    ]
    counts = Counter(item.outcome for item in results)
    succeeded = counts.get(FetchOutcomeCode.CONTENT.value, 0)
    return FetchResponse(
        results=results,
        stats=FetchStats(
            requested=len(req.urls),
            succeeded=succeeded,
            failed=len(req.urls) - succeeded,
            outcomes=[
                FetchOutcomeCount(outcome=outcome, count=count)
                for outcome, count in sorted(counts.items())
            ],
            elapsed_ms=_elapsed_ms(started),
        ),
    )


async def run_fetch(req: FetchRequest, deps) -> FetchResponse:
    async with deps.admission.fetch_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.fetch_route_deadline_s):
                return await _run_fetch(req, deps)
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("fetch") from exc
