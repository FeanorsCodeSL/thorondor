import asyncio
import math
import time
from collections import Counter
from dataclasses import replace
from inspect import isawaitable
from typing import cast
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
    StructuredExtraction,
    StructuredFormat,
    StructuredMarkdownReference,
    StructuredModelUsage,
    StructuredSchemaField,
    StructuredValidationFailure,
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
from .schema_contract import (
    MAX_EXTRACTION_FIELDS,
    MAX_EXTRACTION_PATH_CHARS,
    MAX_EXTRACTION_VALIDATION_FAILURES,
    scalar_fields,
    validate_extracted_value,
)
from .structured_extraction import extract_structured
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
FETCH_RESPONSE_ENVELOPE_RESERVE_BYTES = 65_536


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
    except (TypeError, ValueError):
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
                snapshot = await _fetch_robots_snapshot(deps, origin, now)
    if snapshot is None:
        return False
    return snapshot.allows(url)


async def _fetch_robots_snapshot(deps, origin: str, now: float) -> RobotsSnapshot | None:
    robots_url = f"{origin}/robots.txt"
    if not await _is_safe(deps, robots_url):
        return None
    fetched = await deps.extractor.fetch(
        [robots_url],
        frozenset({"markdown"}),
        False,
    )
    outcome = fetched[0] if fetched else None
    status_code = outcome.status_code if outcome is not None else None
    if outcome is not None and outcome.code == FetchOutcomeCode.CONTENT:
        status_code = status_code or 200
    body = _robots_outcome_body(outcome)
    snapshot = RobotsSnapshot.from_http(
        origin=origin,
        user_agent=deps.crawler_robots_user_agent,
        status_code=status_code,
        body=body,
        fetched_at=now,
        max_bytes=deps.max_robots_bytes,
    )
    deps.robots_cache.put(snapshot)
    return snapshot


def _robots_outcome_body(outcome: FetchStageOutcome | None) -> str:
    if outcome is None or outcome.page is None:
        return ""
    return robots_body(
        outcome.page.markdown,
        outcome.page.raw_html or outcome.page.html,
        outcome.content_type or outcome.page.content_type,
    )


def _materialize_fetch_result(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
    structured_formats: tuple[StructuredFormat, ...] = (),
) -> tuple[FetchResult, str | None, str | None]:
    page = outcome.page
    code = outcome.code
    final_url = outcome.final_url or (page.final_url if page is not None else None)
    markdown = None
    raw_html = None
    source_html = (page.raw_html or page.html) if page is not None else None
    code, markdown, raw_html, structured = _content_payload(
        outcome,
        page,
        code,
        final_url,
        source_html,
        capabilities,
        cleaner,
        structured_formats,
    )
    headers = _response_headers(outcome)
    result = FetchResult(
        requested_url=outcome.requested_url,
        final_url=final_url,
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
        structured=structured,
        response_headers=headers,
    )
    return result, markdown, source_html


def _unsupported_structured(
    structured_formats: tuple[StructuredFormat, ...],
) -> list[StructuredExtraction]:
    return [
        StructuredExtraction(format=format_name, status="unsupported")
        for format_name in structured_formats
    ]


def _content_payload(
    outcome: FetchStageOutcome,
    page,
    code: FetchOutcomeCode,
    final_url: str | None,
    source_html: str | None,
    capabilities: frozenset[str],
    cleaner,
    structured_formats: tuple[StructuredFormat, ...],
) -> tuple[FetchOutcomeCode, str | None, str | None, list[StructuredExtraction]]:
    structured = _unsupported_structured(structured_formats)
    if code != FetchOutcomeCode.CONTENT or page is None:
        return code, None, None, structured
    try:
        cleaned = cleaner.clean(page).page
        markdown = cleaned.markdown.strip() or page.markdown.strip()
        if not markdown:
            return FetchOutcomeCode.EXTRACTION_EMPTY, None, None, structured
        raw_html = page.raw_html or page.html if "raw_html" in capabilities else None
        if structured_formats and source_html:
            structured = extract_structured(
                source_html,
                final_url or outcome.requested_url,
                markdown,
                structured_formats,
            )
        return code, markdown, raw_html, structured
    except Exception:
        return FetchOutcomeCode.LOCAL_PROCESSING_FAILURE, None, None, structured


def _response_headers(outcome: FetchStageOutcome) -> dict[str, str]:
    headers = {}
    if outcome.content_type:
        headers["content-type"] = outcome.content_type
    if outcome.etag:
        headers["etag"] = outcome.etag
    if outcome.last_modified:
        headers["last-modified"] = outcome.last_modified
    if outcome.retry_after:
        headers["retry-after"] = outcome.retry_after
    return headers


def wire_fetch_result(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
    structured_formats: tuple[StructuredFormat, ...] = (),
) -> FetchResult:
    return _materialize_fetch_result(
        outcome,
        capabilities,
        cleaner,
        structured_formats,
    )[0]


def _normalized_evidence_text(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def _evidence_supports_value(value: object, excerpt: str) -> bool:
    if value is None or not isinstance(value, (str, int, float, bool)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    normalized_excerpt = _normalized_evidence_text(excerpt)
    if isinstance(value, bool):
        candidates = ("true" if value else "false",)
    elif isinstance(value, float) and value.is_integer():
        candidates = (str(value), str(int(value)))
    else:
        candidates = (str(value),)
    return any(
        normalized and normalized in normalized_excerpt
        for normalized in (_normalized_evidence_text(candidate) for candidate in candidates)
    )


def _replace_schema_extraction(
    result: FetchResult, extraction: StructuredExtraction
) -> FetchResult:
    result.structured = [
        extraction if item.format == "json_schema" else item
        for item in result.structured
    ]
    return result


def _model_usage(model_result) -> StructuredModelUsage:
    return StructuredModelUsage(
        prompt_bytes=model_result.prompt_bytes,
        output_bytes=model_result.output_bytes,
        input_tokens=model_result.input_tokens,
        output_tokens=model_result.output_tokens,
    )


def _model_failure_extraction(
    document_id: str,
    reason: str,
    usage: StructuredModelUsage,
) -> StructuredExtraction:
    unavailable = reason == "model_unavailable"
    return StructuredExtraction(
        format="json_schema",
        status="unsupported" if unavailable else "model_failed",
        document_id=None if unavailable else document_id,
        validation_failures=[StructuredValidationFailure(path="", code=reason)],
        model_usage=usage,
    )


def _schema_field(
    path: str,
    value: object,
    evidence: dict[str, str],
    markdown: str,
    identity,
) -> tuple[StructuredSchemaField | None, StructuredValidationFailure | None]:
    if len(path) > MAX_EXTRACTION_PATH_CHARS:
        return None, StructuredValidationFailure(
            path=path[:MAX_EXTRACTION_PATH_CHARS],
            code="field_limit_exceeded",
        )
    if value is None or not isinstance(value, (str, int, float, bool)):
        return None, None
    if isinstance(value, float) and not math.isfinite(value):
        return None, None
    excerpt = evidence.get(path)
    if not excerpt:
        return None, StructuredValidationFailure(path=path, code="evidence_missing")
    start = markdown.find(excerpt)
    if start < 0:
        return None, StructuredValidationFailure(path=path, code="evidence_not_found")
    if not _evidence_supports_value(value, excerpt):
        return None, StructuredValidationFailure(path=path, code="evidence_value_mismatch")
    from .url_identity import evidence_id_for

    end = start + len(excerpt)
    source = StructuredMarkdownReference(
        document_id=identity.document_id,
        cleaned_markdown_sha256=identity.cleaned_markdown_sha256,
        final_url=str(identity.final_url),
        start_index=start,
        end_index=end,
        evidence_id=evidence_id_for(
            identity.final_url,
            identity.cleaned_markdown_sha256,
            start,
            end,
        ),
    )
    return StructuredSchemaField(path=path, value=value, source=source), None


def _validated_schema_extraction(
    extraction_schema: dict[str, object],
    model_result,
    markdown: str,
    identity,
    usage: StructuredModelUsage,
) -> StructuredExtraction:
    data = model_result.data or {}
    failures = [
        StructuredValidationFailure(path=path[:MAX_EXTRACTION_PATH_CHARS], code=code)
        for path, code in validate_extracted_value(extraction_schema, data)
    ]
    fields = []
    scalar_values = scalar_fields(data)
    if len(scalar_values) > MAX_EXTRACTION_FIELDS:
        failures.insert(
            0,
            StructuredValidationFailure(path="", code="field_limit_exceeded")
        )
    for path, value in scalar_values[:MAX_EXTRACTION_FIELDS]:
        field, failure = _schema_field(
            path,
            value,
            model_result.evidence,
            markdown,
            identity,
        )
        if failure is not None:
            failures.append(failure)
        if field is not None:
            fields.append(field)
    validation_failed = bool(failures)
    return StructuredExtraction(
        format="json_schema",
        status="validation_failed" if validation_failed else "ok",
        document_id=identity.document_id,
        data=None if validation_failed else data,
        fields=[] if validation_failed else fields,
        validation_failures=failures[:MAX_EXTRACTION_VALIDATION_FAILURES],
        model_usage=usage,
    )


async def apply_schema_extraction(
    result: FetchResult,
    markdown: str | None,
    extraction_schema: dict[str, object] | None,
    deps,
) -> FetchResult:
    if extraction_schema is None or not any(
        item.format == "json_schema" for item in result.structured
    ):
        return result
    if (
        result.outcome != FetchOutcomeCode.CONTENT.value
        or not markdown
    ):
        return result
    from .url_identity import build_document_identity

    final_url = result.final_url or result.requested_url
    identity = build_document_identity(final_url, markdown)
    try:
        model_result = await deps.structured_extractor.extract(markdown, extraction_schema)
    except Exception:
        return _replace_schema_extraction(
            result,
            _model_failure_extraction(
                identity.document_id,
                "model_error",
                StructuredModelUsage(),
            ),
        )
    usage = _model_usage(model_result)
    if model_result.reason is not None:
        extraction = _model_failure_extraction(
            identity.document_id,
            model_result.reason,
            usage,
        )
    else:
        extraction = _validated_schema_extraction(
            extraction_schema,
            model_result,
            markdown,
            identity,
            usage,
        )
    return _replace_schema_extraction(result, extraction)


def _trim_structured_extraction(
    extraction: StructuredExtraction,
) -> StructuredExtraction | None:
    values = extraction.model_dump()
    if extraction.links:
        values["links"].pop()
        values["omitted_items"] += 1
        values["status"] = "truncated"
    elif extraction.tables:
        table = values["tables"][-1]
        if len(table["rows"]) > 1:
            row = table["rows"].pop()
            values["omitted_items"] += max(1, len(row))
        else:
            table = values["tables"].pop()
            values["omitted_items"] += max(
                1,
                sum(len(row) for row in table["rows"]),
            )
        values["status"] = "truncated"
    elif extraction.documents:
        values["documents"].pop()
        values["omitted_items"] += 1
        values["status"] = "truncated"
    elif extraction.format == "json_schema" and (
        extraction.data is not None or extraction.fields
    ):
        return StructuredExtraction(
            format="json_schema",
            status="validation_failed",
            document_id=extraction.document_id,
            validation_failures=[
                StructuredValidationFailure(path="", code="response_budget_exceeded")
            ],
            model_usage=extraction.model_usage,
            omitted_items=max(1, len(extraction.fields)),
        )
    else:
        return None
    return StructuredExtraction.model_validate(values)


def _bound_fetch_result(result: FetchResult, max_bytes: int) -> None:
    while len(result.model_dump_json().encode("utf-8")) > max_bytes:
        candidates = [
            (len(item.model_dump_json().encode("utf-8")), index)
            for index, item in enumerate(result.structured)
            if item.links
            or item.tables
            or item.documents
            or (item.format == "json_schema" and (item.data is not None or item.fields))
        ]
        if not candidates:
            return
        _size, index = max(candidates)
        replacement = _trim_structured_extraction(result.structured[index])
        if replacement is None:
            return
        result.structured[index] = replacement


def _bound_fetch_results(results: list[FetchResult], max_response_bytes: int) -> None:
    available = max_response_bytes - FETCH_RESPONSE_ENVELOPE_RESERVE_BYTES
    if not results or available <= 0:
        return
    result_budget = available // len(results)
    for result in results:
        _bound_fetch_result(result, result_budget)


async def wire_fetch_result_with_schema(
    outcome: FetchStageOutcome,
    capabilities: frozenset[str],
    cleaner,
    structured_formats: tuple[StructuredFormat, ...],
    extraction_schema: dict[str, object] | None,
    deps,
) -> FetchResult:
    result, markdown, _source_html = _materialize_fetch_result(
        outcome,
        capabilities,
        cleaner,
        structured_formats,
    )
    return await apply_schema_extraction(
        result,
        markdown,
        extraction_schema,
        deps,
    )


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
        return cast(StoredTargetSnapshot, replace(snapshot, updated_at=now))
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
    return cast(
        StoredTargetSnapshot,
        replace(
            current,
            baseline_text=baseline.text if baseline is not None else None,
            baseline_attributes=baseline.attributes if baseline is not None else None,
            baseline_updated_at=baseline.updated_at if baseline is not None else None,
        ),
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


def _apply_change(
    result: FetchResult,
    previous: PageCacheRecord | None,
    current_hash: str,
    current_markdown: str | None,
    deps,
) -> None:
    result.change = _page_change(previous, current_hash, result.status_code)
    if result.change.state == "same":
        return
    result.diff = bounded_diff(
        previous.markdown if previous is not None else None,
        current_markdown,
        deps.page_diff_max_input_lines,
        deps.page_diff_max_operations,
        deps.page_diff_max_output_lines,
    )


async def _coalesced_result(
    request: FetchRequest,
    deps,
    latest: PageCacheRecord,
    baseline: PageCacheRecord | None,
    now: float,
    url: str,
    cache_key: str,
) -> FetchResult:
    result = _record_to_result(
        latest,
        "revalidated",
        "coalesced",
        now,
        url,
    )
    _apply_change(result, baseline, latest.source_hash, latest.markdown, deps)
    result.watch = await _cached_watch_result(request, deps, cache_key)
    return result


async def _not_modified_result(
    request: FetchRequest,
    deps,
    previous: PageCacheRecord,
    now: float,
    url: str,
    cache_key: str,
) -> FetchResult:
    refreshed = cast(
        PageCacheRecord,
        replace(
            previous,
            fetched_at=now,
            expires_at=now + deps.page_cache_ttl_s,
        ),
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


def _refreshed_record(
    result: FetchResult,
    markdown: str | None,
    cache_key: str,
    url_identity: str,
    now: float,
    deps,
) -> PageCacheRecord | None:
    if not _cacheable(result):
        return None
    return _build_record(
        result,
        markdown,
        cache_key,
        url_identity,
        getattr(deps.markdown_cleaner, "cleaner_version", "unknown"),
        now,
        deps,
    )


def _watch_snapshot(
    result: FetchResult,
    source_html: str | None,
    request: FetchRequest,
    now: float,
) -> StoredTargetSnapshot:
    if result.outcome == FetchOutcomeCode.CONTENT.value:
        return _project_snapshot(
            resolve_target(source_html, request.watch.target),
            request,
            now,
        )
    if _is_tombstone(result):
        return StoredTargetSnapshot("missing", None, {}, now)
    return StoredTargetSnapshot("unsupported", None, {}, now)


async def _persist_refresh(
    request: FetchRequest,
    deps,
    cache_key: str,
    result: FetchResult,
    record: PageCacheRecord | None,
    source_html: str | None,
    now: float,
) -> None:
    if request.watch is None:
        if record is not None:
            await deps.page_cache.put(record)
        return
    current = _watch_snapshot(result, source_html, request, now)
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
            return await _coalesced_result(
                request,
                deps,
                latest,
                baseline,
                now,
                url,
                cache_key,
            )
        previous = latest or baseline
        outcome = None
        if previous is not None:
            outcome = await _conditionally_revalidate(url, capabilities, previous, deps)
        if previous is not None and outcome is not None and outcome.status_code == 304:
            return await _not_modified_result(
                request,
                deps,
                previous,
                now,
                url,
                cache_key,
            )
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
            _apply_change(result, previous, source_hash(markdown), markdown, deps)
        record = _refreshed_record(
            result,
            markdown,
            cache_key,
            url_identity,
            now,
            deps,
        )
        await _persist_refresh(
            request,
            deps,
            cache_key,
            result,
            record,
            source_html,
            now,
        )
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
        "raw_html" in capabilities
        or any(format_name != "json_schema" for format_name in request.structured_formats),
    )
    by_url = {item.requested_url: item for item in fetched}
    results: list[FetchResult] = []
    pending_schema: list[tuple[FetchResult, str | None]] = []
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
        result, markdown, _source_html = _materialize_fetch_result(
            outcome,
            capabilities,
            deps.markdown_cleaner,
            tuple(request.structured_formats),
        )
        result.cache = FetchCacheInfo(state="bypass", reason=reason)
        if request.watch is not None:
            result.watch = TargetWatchResult(
                resolution="unsupported",
                state=None,
                condition_met=False,
            )
        results.append(result)
        pending_schema.append((result, markdown))
    if request.extraction_schema is not None:
        await asyncio.gather(
            *(
                apply_schema_extraction(
                    result,
                    markdown,
                    request.extraction_schema,
                    deps,
                )
                for result, markdown in pending_schema
            )
        )
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
                tuple(req.structured_formats),
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
                        tuple(req.structured_formats),
                    )
                )
        reason = (
            "structured_source_required"
            if req.structured_formats
            else cache_bypass_reason(
                deps.page_cache.enabled,
                capabilities,
                deps.page_cache_raw_html_enabled,
            )
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
    response = FetchResponse(
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
    _bound_fetch_results(response.results, deps.resource_policy.max_response_body_bytes)
    return response


async def run_fetch(req: FetchRequest, deps) -> FetchResponse:
    async with deps.admission.fetch_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.fetch_route_deadline_s):
                return await _run_fetch(req, deps)
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("fetch") from exc
