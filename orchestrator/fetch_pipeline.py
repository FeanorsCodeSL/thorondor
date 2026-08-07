import asyncio
import time
from collections import Counter
from dataclasses import replace
from inspect import isawaitable
from urllib.parse import urlsplit, urlunsplit

from .diffing import bounded_diff
from .models import (
    FetchCacheInfo,
    FetchOutcomeCount,
    FetchRequest,
    FetchResponse,
    FetchResult,
    FetchStats,
    PageChange,
    TargetWatchResult,
)
from .outcome_codes import FetchOutcomeCode
from .page_cache import (
    PageCacheRecord,
    StoredTargetSnapshot,
    build_cache_key,
    cache_bypass_reason,
    has_url_credentials,
    source_hash,
    watch_fingerprint,
)
from .resource_policy import RouteDeadlineExceeded
from .robots_policy import RobotsSnapshot, robots_body
from .target_watch import evaluate_watch, resolve_target
from .types import FetchStageOutcome

RETRYABLE_FETCH_OUTCOMES = {
    FetchOutcomeCode.UPSTREAM_TIMEOUT,
    FetchOutcomeCode.DEADLINE_CANCELLED,
    FetchOutcomeCode.UPSTREAM_FAILURE,
    FetchOutcomeCode.RATE_LIMITED,
    FetchOutcomeCode.CAPACITY_UNAVAILABLE,
}
REMOVED_STATUSES = {404, 410}


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


def _origin(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        if port is not None and not (
            (parsed.scheme == "http" and port == 80)
            or (parsed.scheme == "https" and port == 443)
        ):
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, host, "", "", ""))
    except (TypeError, ValueError, UnicodeError):
        return None


async def _cache_policy_allows(deps, url: str) -> bool:
    if not deps.crawl_respect_robots_txt:
        return True
    origin = _origin(url)
    if origin is None:
        return False
    now = time.time()
    snapshot = deps.robots_cache.get(origin, now=now)
    if snapshot is None:
        async with deps.robots_cache.lock_for(origin):
            now = time.time()
            snapshot = deps.robots_cache.get(origin, now=now)
            if snapshot is None:
                robots_url = f"{origin}/robots.txt"
                if not await _is_safe(deps, robots_url):
                    return False
                fetched = await deps.extractor.fetch(
                    [robots_url],
                    frozenset({"markdown"}),
                    False,
                )
                outcome = fetched[0] if fetched else None
                status_code = outcome.status_code if outcome is not None else None
                if outcome is not None and outcome.code == FetchOutcomeCode.CONTENT:
                    status_code = status_code or 200
                body = ""
                if outcome is not None and outcome.page is not None:
                    body = robots_body(
                        outcome.page.markdown,
                        outcome.page.raw_html or outcome.page.html,
                        outcome.content_type or outcome.page.content_type,
                    )
                snapshot = RobotsSnapshot.from_http(
                    origin=origin,
                    user_agent=deps.crawler_robots_user_agent,
                    status_code=status_code,
                    body=body,
                    fetched_at=now,
                    max_bytes=deps.max_robots_bytes,
                )
                deps.robots_cache.put(snapshot)
    return snapshot.allows(url)


def _materialize_fetch_result(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
) -> tuple[FetchResult, str | None, str | None]:
    page = outcome.page
    code = outcome.code
    markdown = None
    raw_html = None
    source_html = (page.raw_html or page.html) if page is not None else None
    if code == FetchOutcomeCode.CONTENT and page is not None:
        try:
            cleaned = cleaner.clean(page).page
            markdown = cleaned.markdown.strip() or page.markdown.strip()
            if not markdown:
                code = FetchOutcomeCode.EXTRACTION_EMPTY
            elif "raw_html" in capabilities:
                raw_html = page.raw_html or page.html
        except Exception:
            code = FetchOutcomeCode.LOCAL_PROCESSING_FAILURE
    headers = {}
    if outcome.content_type:
        headers["content-type"] = outcome.content_type
    if outcome.etag:
        headers["etag"] = outcome.etag
    if outcome.last_modified:
        headers["last-modified"] = outcome.last_modified
    if outcome.retry_after:
        headers["retry-after"] = outcome.retry_after
    result = FetchResult(
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
    return result, markdown, source_html


def wire_fetch_result(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
) -> FetchResult:
    return _materialize_fetch_result(outcome, capabilities, cleaner)[0]


def _record_to_result(
    record: PageCacheRecord,
    state: str,
    reason: str,
    now: float,
    requested_url: str,
) -> FetchResult:
    return FetchResult(
        requested_url=requested_url,
        final_url=record.final_url,
        outcome=record.outcome,
        retryable=False,
        status_code=record.status_code,
        content_type=record.content_type,
        title=record.title,
        retrieval_method=record.retrieval_method,
        elapsed_ms=0,
        capabilities=list(record.capabilities),
        markdown=record.markdown if "markdown" in record.capabilities else None,
        raw_html=record.raw_html if "raw_html" in record.capabilities else None,
        links=record.links if "links" in record.capabilities else {},
        metadata=record.metadata if "metadata" in record.capabilities else {},
        response_headers=record.response_headers,
        cache=FetchCacheInfo(
            state=state,
            reason=reason,
            age_s=max(0.0, now - record.fetched_at),
        ),
        change=PageChange(
            state="removed" if record.status_code in REMOVED_STATUSES else "same",
            previous_sha256=record.source_hash,
            current_sha256=record.source_hash,
            previous_status=record.status_code,
            current_status=record.status_code,
        ),
    )


def _page_change(
    previous: PageCacheRecord | None,
    current_hash: str | None,
    current_status: int | None,
) -> PageChange:
    if current_status in REMOVED_STATUSES:
        state = "removed"
    elif previous is None:
        state = "new"
    elif previous.source_hash == current_hash and previous.status_code == current_status:
        state = "same"
    else:
        state = "changed"
    return PageChange(
        state=state,
        previous_sha256=previous.source_hash if previous else None,
        current_sha256=current_hash,
        previous_status=previous.status_code if previous else None,
        current_status=current_status,
    )


def _cacheable(result: FetchResult) -> bool:
    return result.outcome == FetchOutcomeCode.CONTENT.value or _is_tombstone(result)


def _is_tombstone(result: FetchResult) -> bool:
    return (
        result.outcome == FetchOutcomeCode.UPSTREAM_FAILURE.value
        and result.status_code in REMOVED_STATUSES
    )


def _build_record(
    result: FetchResult,
    markdown: str | None,
    cache_key: str,
    url_identity: str,
    cleaner_version: str,
    now: float,
    deps,
) -> PageCacheRecord:
    return PageCacheRecord(
        cache_key=cache_key,
        url_identity=url_identity,
        requested_url=result.requested_url,
        final_url=result.final_url,
        outcome=result.outcome,
        status_code=result.status_code,
        content_type=result.content_type,
        title=result.title,
        retrieval_method=result.retrieval_method,
        capabilities=tuple(result.capabilities),
        markdown=markdown,
        raw_html=result.raw_html if deps.page_cache_raw_html_enabled else None,
        links=result.links,
        metadata=result.metadata,
        response_headers=result.response_headers,
        source_hash=source_hash(markdown),
        cleaner_version=cleaner_version,
        fetched_at=now,
        expires_at=now + deps.page_cache_ttl_s,
        retained_until=now + deps.page_cache_retention_s,
    )


def _project_snapshot(
    snapshot: StoredTargetSnapshot,
    request: FetchRequest,
    now: float,
) -> StoredTargetSnapshot:
    if request.watch is None:
        return replace(snapshot, updated_at=now)
    attributes = {
        state.attribute.casefold(): snapshot.attributes.get(state.attribute.casefold(), "")
        for state in (request.watch.expected, request.watch.desired)
        if state is not None and state.attribute is not None
    }
    return StoredTargetSnapshot(
        resolution=snapshot.resolution,
        text=snapshot.text,
        attributes=attributes,
        updated_at=now,
    )


def _watch_key(request: FetchRequest) -> str:
    return watch_fingerprint(request.watch.model_dump(exclude_none=True))


def _target_baseline(snapshot: StoredTargetSnapshot | None) -> StoredTargetSnapshot | None:
    if snapshot is None:
        return None
    if snapshot.resolution == "found":
        return snapshot
    if snapshot.baseline_updated_at is None:
        return None
    return StoredTargetSnapshot(
        resolution="found",
        text=snapshot.baseline_text,
        attributes=snapshot.baseline_attributes or {},
        updated_at=snapshot.baseline_updated_at,
    )


def _stored_target_snapshot(
    current: StoredTargetSnapshot,
    previous: StoredTargetSnapshot | None,
) -> StoredTargetSnapshot:
    baseline = current if current.resolution == "found" else previous
    return replace(
        current,
        baseline_text=baseline.text if baseline is not None else None,
        baseline_attributes=baseline.attributes if baseline is not None else None,
        baseline_updated_at=baseline.updated_at if baseline is not None else None,
    )


async def _cached_watch_result(
    request: FetchRequest,
    deps,
    cache_key: str,
) -> TargetWatchResult | None:
    if request.watch is None:
        return None
    watch_key = _watch_key(request)
    snapshot = await deps.page_cache.get_target(cache_key, watch_key)
    if snapshot is None:
        return None
    previous = snapshot if snapshot.resolution == "found" else _target_baseline(snapshot)
    return evaluate_watch(request.watch, previous, snapshot)


async def _fetch_outcome(url: str, capabilities: frozenset[str], deps) -> FetchStageOutcome:
    fetched = await deps.extractor.fetch(
        [url],
        capabilities,
        "raw_html" in capabilities,
    )
    if fetched:
        return fetched[0]
    return FetchStageOutcome(
        requested_url=url,
        final_url=None,
        code=FetchOutcomeCode.UPSTREAM_FAILURE,
        retrieval_method="crawl4ai_browser",
        elapsed_ms=0,
    )


async def _conditionally_revalidate(
    url: str,
    capabilities: frozenset[str],
    previous: PageCacheRecord,
    deps,
) -> FetchStageOutcome | None:
    revalidate = getattr(deps.extractor, "revalidate", None)
    if revalidate is None or "javascript" in capabilities:
        return None
    etag = previous.response_headers.get("etag")
    last_modified = previous.response_headers.get("last-modified")
    if not etag and not last_modified:
        return None
    return await revalidate(
        url,
        capabilities,
        etag,
        last_modified,
        "raw_html" in capabilities,
    )


async def _refresh_one(
    request: FetchRequest,
    url: str,
    capabilities: frozenset[str],
    deps,
    cache_key: str,
    url_identity: str,
    baseline: PageCacheRecord | None,
) -> FetchResult:
    lock = deps.page_refresh.lock_for(cache_key)
    async with lock:
        now = time.time()
        latest = await deps.page_cache.get(cache_key)
        if latest is not None and (
            baseline is None or latest.fetched_at > baseline.fetched_at
        ):
            result = _record_to_result(
                latest,
                "revalidated",
                "coalesced",
                now,
                url,
            )
            result.change = _page_change(baseline, latest.source_hash, latest.status_code)
            result.diff = bounded_diff(
                baseline.markdown if baseline else None,
                latest.markdown,
                deps.page_diff_max_input_lines,
                deps.page_diff_max_operations,
                deps.page_diff_max_output_lines,
            ) if result.change.state != "same" else None
            result.watch = await _cached_watch_result(request, deps, cache_key)
            return result
        previous = latest or baseline
        outcome = None
        if previous is not None:
            outcome = await _conditionally_revalidate(url, capabilities, previous, deps)
        if outcome is not None and outcome.status_code == 304:
            refreshed = replace(
                previous,
                fetched_at=now,
                expires_at=now + deps.page_cache_ttl_s,
            )
            await deps.page_cache.put(refreshed)
            result = _record_to_result(
                refreshed,
                "revalidated",
                "not_modified",
                now,
                url,
            )
            result.watch = await _cached_watch_result(request, deps, cache_key)
            return result
        if outcome is None:
            outcome = await _fetch_outcome(url, capabilities, deps)
        result, markdown, source_html = _materialize_fetch_result(
            outcome,
            capabilities,
            deps.markdown_cleaner,
        )
        result.cache = FetchCacheInfo(
            state="revalidated",
            reason="miss" if previous is None else "full_refetch",
        )
        if _cacheable(result):
            current_hash = source_hash(markdown)
            result.change = _page_change(previous, current_hash, result.status_code)
            if result.change.state != "same":
                result.diff = bounded_diff(
                    previous.markdown if previous else None,
                    markdown,
                    deps.page_diff_max_input_lines,
                    deps.page_diff_max_operations,
                    deps.page_diff_max_output_lines,
                )
        record = None
        if _cacheable(result):
            record = _build_record(
                result,
                markdown,
                cache_key,
                url_identity,
                getattr(deps.markdown_cleaner, "cleaner_version", "unknown"),
                now,
                deps,
            )
        if request.watch is not None:
            if result.outcome == FetchOutcomeCode.CONTENT.value:
                current = _project_snapshot(
                    resolve_target(source_html, request.watch.target),
                    request,
                    now,
                )
            elif _is_tombstone(result):
                current = StoredTargetSnapshot("missing", None, {}, now)
            else:
                current = StoredTargetSnapshot("unsupported", None, {}, now)
            watch_key = _watch_key(request)
            stored_previous = await deps.page_cache.get_target(cache_key, watch_key)
            previous_target = _target_baseline(stored_previous)
            result.watch = evaluate_watch(request.watch, previous_target, current)
            if record is not None:
                await deps.page_cache.put_with_target(
                    record,
                    watch_key,
                    _stored_target_snapshot(current, previous_target),
                )
        elif record is not None:
            await deps.page_cache.put(record)
        return result


async def _cached_fetch_one(
    request: FetchRequest,
    url: str,
    capabilities: frozenset[str],
    deps,
) -> FetchResult:
    if has_url_credentials(url):
        results = await _run_uncached(
            request,
            [url],
            capabilities,
            deps,
            "url_credentials",
        )
        return results[0]
    if not await _cache_policy_allows(deps, url):
        results = await _run_uncached(
            request,
            [url],
            capabilities,
            deps,
            "robots_policy",
        )
        return results[0]
    cleaner_version = getattr(deps.markdown_cleaner, "cleaner_version", "unknown")
    cache_key, url_identity = build_cache_key(
        url,
        capabilities,
        "browser" if "javascript" in capabilities else "http",
        cleaner_version,
    )
    now = time.time()
    record = await deps.page_cache.get(cache_key)
    if (
        record is not None
        and record.final_url is not None
        and record.final_url != url
        and (
            has_url_credentials(record.final_url)
            or not await _is_safe(deps, record.final_url)
            or not await _cache_policy_allows(deps, record.final_url)
        )
    ):
        await deps.page_cache.delete(cache_key)
        result = await _refresh_one(
            request,
            url,
            capabilities,
            deps,
            cache_key,
            url_identity,
            None,
        )
        result.cache.reason = "cached_final_url_policy"
        return result
    cached_watch = await _cached_watch_result(request, deps, cache_key)
    watch_needs_fetch = request.watch is not None and cached_watch is None
    if (
        record is not None
        and now < record.expires_at
        and not request.force_refresh
        and not watch_needs_fetch
    ):
        result = _record_to_result(record, "fresh", "ttl", now, url)
        result.watch = cached_watch
        return result
    stale_until = record.expires_at + deps.page_cache_stale_s if record else 0
    if (
        record is not None
        and now < stale_until
        and request.stale_while_revalidate
        and not request.force_refresh
        and request.watch is None
    ):
        deps.page_refresh.start_background(
            cache_key,
            lambda: _background_refresh(
                request,
                url,
                capabilities,
                deps,
                cache_key,
                url_identity,
                record,
            ),
        )
        return _record_to_result(record, "stale", "refresh_started", now, url)
    return await _refresh_one(
        request,
        url,
        capabilities,
        deps,
        cache_key,
        url_identity,
        record,
    )


async def _background_refresh(
    request: FetchRequest,
    url: str,
    capabilities: frozenset[str],
    deps,
    cache_key: str,
    url_identity: str,
    baseline: PageCacheRecord,
) -> None:
    async with deps.admission.fetch_slot():
        async with asyncio.timeout(deps.resource_policy.fetch_route_deadline_s):
            await _refresh_one(
                request,
                url,
                capabilities,
                deps,
                cache_key,
                url_identity,
                baseline,
            )


async def _run_uncached(
    request: FetchRequest,
    safe_urls: list[str],
    capabilities: frozenset[str],
    deps,
    reason: str,
) -> list[FetchStageOutcome | FetchResult]:
    if not safe_urls:
        return []
    fetched = await deps.extractor.fetch(
        safe_urls,
        capabilities,
        "raw_html" in capabilities,
    )
    by_url = {item.requested_url: item for item in fetched}
    results: list[FetchResult] = []
    for url in safe_urls:
        outcome = by_url.get(
            url,
            FetchStageOutcome(
                requested_url=url,
                final_url=None,
                code=FetchOutcomeCode.UPSTREAM_FAILURE,
                retrieval_method="crawl4ai_browser",
                elapsed_ms=0,
            ),
        )
        result, _markdown, _source_html = _materialize_fetch_result(
            outcome, capabilities, deps.markdown_cleaner
        )
        result.cache = FetchCacheInfo(state="bypass", reason=reason)
        if request.watch is not None:
            result.watch = TargetWatchResult(
                resolution="unsupported",
                state=None,
                condition_met=False,
            )
        results.append(result)
    return results


async def _run_fetch(req: FetchRequest, deps) -> FetchResponse:
    started = time.perf_counter()
    capabilities = frozenset(req.capabilities)
    supported = frozenset(getattr(deps.extractor, "supported_capabilities", ()))
    unsupported = capabilities - supported
    results: list[FetchResult] = []
    safe_urls: list[str] = []
    if unsupported:
        results = [
            wire_fetch_result(
                FetchStageOutcome(
                    requested_url=url,
                    final_url=None,
                    code=FetchOutcomeCode.UNSUPPORTED_CAPABILITY,
                    retrieval_method="",
                    elapsed_ms=0,
                ),
                capabilities,
                deps.markdown_cleaner,
            )
            for url in req.urls
        ]
    else:
        for url in req.urls:
            if await _is_safe(deps, url):
                safe_urls.append(url)
            else:
                results.append(
                    wire_fetch_result(
                        FetchStageOutcome(
                            requested_url=url,
                            final_url=None,
                            code=FetchOutcomeCode.UNSAFE_TARGET,
                            retrieval_method="",
                            elapsed_ms=0,
                        ),
                        capabilities,
                        deps.markdown_cleaner,
                    )
                )
        reason = cache_bypass_reason(
            deps.page_cache.enabled,
            capabilities,
            deps.page_cache_raw_html_enabled,
        )
        if safe_urls and reason is not None:
            results.extend(await _run_uncached(req, safe_urls, capabilities, deps, reason))
        elif safe_urls:
            cached = await asyncio.gather(
                *(
                    _cached_fetch_one(req, url, capabilities, deps)
                    for url in safe_urls
                )
            )
            results.extend(cached)
    order = {url: index for index, url in enumerate(req.urls)}
    results.sort(key=lambda item: order[item.requested_url])
    counts = Counter(item.outcome for item in results)
    cache_counts = Counter(item.cache.state for item in results)
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
            cache_fresh=cache_counts["fresh"],
            cache_stale=cache_counts["stale"],
            cache_revalidated=cache_counts["revalidated"],
            cache_bypassed=cache_counts["bypass"],
        ),
    )


async def run_fetch(req: FetchRequest, deps) -> FetchResponse:
    async with deps.admission.fetch_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.fetch_route_deadline_s):
                return await _run_fetch(req, deps)
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("fetch") from exc
