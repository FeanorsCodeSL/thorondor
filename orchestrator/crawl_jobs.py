import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from .crawl_job_store import (
    TERMINAL_JOB_STATES,
    CrawlJobNotFound,
    CrawlJobRecord,
    CrawlJobResultTooLarge,
    SqliteCrawlJobStore,
)
from .models import (
    MAX_JOB_FAILURE_SUMMARY_CHARS,
    CrawlJobCreateResponse,
    CrawlJobProgress,
    CrawlJobResultPage,
    CrawlJobState,
    CrawlJobStatus,
    CrawlRequest,
    CrawlResponse,
    FetchResult,
)
from .resource_policy import CapacityUnavailable, RouteDeadlineExceeded
from .site_pipeline import CrawlOperationCancelled
from .url_identity import dedup_key_for

logger = logging.getLogger(__name__)

MAX_SCOPE_BYTES = 128
MAX_IDEMPOTENCY_KEY_BYTES = 128
MAX_CURSOR_BYTES = 512
JOB_ID = re.compile(r"[0-9a-f]{32}")
RETRYABLE_JOB_OUTCOMES = frozenset(
    {
        "capacity_unavailable",
        "deadline_cancelled",
        "rate_limited",
        "upstream_failure",
        "upstream_timeout",
    }
)


class CrawlJobsDisabled(Exception):
    pass


class CrawlJobWorkerUnavailable(Exception):
    pass


class CrawlJobApiCapacityUnavailable(Exception):
    def __init__(self, retry_after_s: int):
        super().__init__("crawl job API capacity unavailable")
        self.retry_after_s = retry_after_s


class RawHtmlPersistenceDisabled(Exception):
    pass


class InvalidJobScope(Exception):
    pass


class InvalidIdempotencyKey(Exception):
    pass


class InvalidCursor(Exception):
    pass


class InvalidPageLimit(Exception):
    pass


class CrawlJobManager:
    def __init__(
        self,
        *,
        enabled: bool,
        store: SqliteCrawlJobStore | None,
        runner: Callable[
            [
                CrawlRequest,
                Callable[[FetchResult], Awaitable[None]],
                Callable[[str, str], Awaitable[None]],
                Callable[[], bool],
            ],
            Awaitable[CrawlResponse],
        ]
        | None,
        synchronous_max_pages: int,
        max_attempts: int,
        retry_base_s: float,
        max_page_items: int,
        max_page_bytes: int,
        raw_html_enabled: bool = False,
        max_inflight_requests: int = 16,
        admission_wait_s: float = 1,
        admission_retry_after_s: int = 1,
        poll_interval_s: float = 0.05,
    ):
        self.enabled = enabled
        self.store = store
        self.runner = runner
        self.synchronous_max_pages = synchronous_max_pages
        self.max_attempts = max_attempts
        self.retry_base_s = retry_base_s
        self.max_page_items = max_page_items
        self.max_page_bytes = max_page_bytes
        self.raw_html_enabled = raw_html_enabled
        self.admission_wait_s = admission_wait_s
        self.admission_retry_after_s = admission_retry_after_s
        self.poll_interval_s = poll_interval_s
        self._api_slots = asyncio.Semaphore(max_inflight_requests)
        self._wake = asyncio.Event()
        self._closing = False
        self._worker: asyncio.Task | None = None
        self._active_cancel: dict[str, asyncio.Event] = {}
        self._store_available = not enabled

    @property
    def healthy(self) -> bool:
        if not self.enabled:
            return True
        return (
            self._store_available
            and self._worker is not None
            and not self._worker.done()
        )

    async def start(self) -> None:
        if not self.enabled:
            return
        if self.store is None or self.runner is None:
            raise RuntimeError("enabled crawl jobs require a store and runner")
        await self.store.start()
        await self.store.recover_interrupted(self.max_attempts)
        self._closing = False
        self._store_available = True
        self._worker = asyncio.create_task(self._worker_loop(), name="crawl-job-worker")

    async def aclose(self) -> None:
        self._closing = True
        self._wake.set()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Crawl job worker exited before shutdown")
            self._worker = None
        self._active_cancel.clear()

    @staticmethod
    def _bounded_header(value: str, max_bytes: int) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError
        if len(normalized.encode("utf-8")) > max_bytes:
            raise ValueError
        return normalized

    @classmethod
    def scope_hash(cls, scope: str) -> str:
        try:
            bounded = cls._bounded_header(scope, MAX_SCOPE_BYTES)
        except (UnicodeError, ValueError) as exc:
            raise InvalidJobScope from exc
        return hashlib.sha256(bounded.encode("utf-8")).hexdigest()

    @classmethod
    def idempotency_hash(cls, scope_hash: str, key: str) -> str:
        try:
            bounded = cls._bounded_header(key, MAX_IDEMPOTENCY_KEY_BYTES)
        except (UnicodeError, ValueError) as exc:
            raise InvalidIdempotencyKey from exc
        return hashlib.sha256(
            scope_hash.encode("ascii") + b"\x00" + bounded.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def request_fingerprint(request: CrawlRequest) -> str:
        encoded = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _timestamp(value: float | None) -> datetime | None:
        return datetime.fromtimestamp(value, tz=UTC) if value is not None else None

    @classmethod
    def status_from_record(cls, record: CrawlJobRecord) -> CrawlJobStatus:
        return CrawlJobStatus(
            job_id=record.job_id,
            state=record.state,
            request=record.request,
            progress=CrawlJobProgress(
                pages_target=record.pages_target,
                pages_processed=record.pages_processed,
                pages_succeeded=record.pages_succeeded,
                pages_failed=record.pages_failed,
                results_available=record.results_available,
                attempts=record.attempts,
            ),
            failure_summaries=list(record.failure_summaries),
            created_at=cls._timestamp(record.created_at),
            started_at=cls._timestamp(record.started_at),
            terminal_at=cls._timestamp(record.terminal_at),
            retention_deadline=cls._timestamp(record.retention_deadline),
            cancel_requested=record.cancel_requested,
            outcome=record.outcome,
            warnings=list(record.warnings),
        )

    def _require_enabled(self) -> SqliteCrawlJobStore:
        if not self.enabled or self.store is None:
            raise CrawlJobsDisabled
        return self.store

    @asynccontextmanager
    async def _api_slot(self):
        try:
            await asyncio.wait_for(
                self._api_slots.acquire(),
                timeout=self.admission_wait_s,
            )
        except TimeoutError as exc:
            raise CrawlJobApiCapacityUnavailable(
                self.admission_retry_after_s
            ) from exc
        try:
            yield
        finally:
            self._api_slots.release()

    async def create(
        self,
        request: CrawlRequest,
        *,
        scope: str,
        idempotency_key: str,
    ) -> CrawlJobCreateResponse:
        async with self._api_slot():
            store = self._require_enabled()
            if not self.healthy:
                raise CrawlJobWorkerUnavailable
            if "raw_html" in request.capabilities and not self.raw_html_enabled:
                raise RawHtmlPersistenceDisabled
            scope_hash = self.scope_hash(scope)
            idempotency_hash = self.idempotency_hash(scope_hash, idempotency_key)
            claim = await store.claim(
                job_id=uuid.uuid4().hex,
                scope_hash=scope_hash,
                idempotency_hash=idempotency_hash,
                request_fingerprint=self.request_fingerprint(request),
                request=request,
            )
            self._wake.set()
            return CrawlJobCreateResponse(
                job=self.status_from_record(claim.record),
                replayed=claim.replayed,
                synchronous_max_pages=self.synchronous_max_pages,
            )

    async def status(self, job_id: str, *, scope: str) -> CrawlJobStatus:
        async with self._api_slot():
            store = self._require_enabled()
            self._validate_job_id(job_id)
            return self.status_from_record(await store.get(job_id, self.scope_hash(scope)))

    async def cancel(self, job_id: str, *, scope: str) -> CrawlJobStatus:
        async with self._api_slot():
            store = self._require_enabled()
            self._validate_job_id(job_id)
            record = await store.request_cancel(job_id, self.scope_hash(scope))
            event = self._active_cancel.get(job_id)
            if event is not None:
                event.set()
            self._wake.set()
            return self.status_from_record(record)

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if JOB_ID.fullmatch(job_id) is None:
            raise CrawlJobNotFound(job_id)

    @staticmethod
    def _cursor(job_id: str, ordinal: int) -> str:
        payload = json.dumps(
            {"v": 1, "job": job_id, "after": ordinal},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_cursor(job_id: str, cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            encoded = cursor.encode("ascii")
        except UnicodeError as exc:
            raise InvalidCursor from exc
        if len(encoded) > MAX_CURSOR_BYTES:
            raise InvalidCursor
        try:
            padding = b"=" * (-len(encoded) % 4)
            decoded = base64.b64decode(
                encoded + padding,
                altchars=b"-_",
                validate=True,
            )
            payload = json.loads(decoded)
        except (binascii.Error, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidCursor from exc
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("job") != job_id
            or type(payload.get("after")) is not int
            or payload["after"] < 0
        ):
            raise InvalidCursor
        return payload["after"]

    async def results(
        self,
        job_id: str,
        *,
        scope: str,
        cursor: str | None,
        max_items: int | None,
        max_bytes: int | None,
    ) -> CrawlJobResultPage:
        async with self._api_slot():
            store = self._require_enabled()
            self._validate_job_id(job_id)
            max_items = self.max_page_items if max_items is None else max_items
            max_bytes = self.max_page_bytes if max_bytes is None else max_bytes
            if not 1 <= max_items <= self.max_page_items:
                raise InvalidPageLimit
            if not 1 <= max_bytes <= self.max_page_bytes:
                raise InvalidPageLimit
            after = self._decode_cursor(job_id, cursor)
            record, page = await store.result_page(
                job_id,
                self.scope_hash(scope),
                after,
                max_items,
                max_bytes,
            )
            if after > record.results_available:
                raise InvalidCursor
            terminal = record.state in TERMINAL_JOB_STATES
            complete = (
                terminal
                and not page.has_more
                and page.last_ordinal >= record.results_available
            )
            next_cursor = None
            if page.results and (page.has_more or not complete):
                next_cursor = self._cursor(job_id, page.last_ordinal)
            elif not page.results and not complete:
                next_cursor = cursor
            return CrawlJobResultPage(
                job_id=job_id,
                state=record.state,
                results=list(page.results),
                next_cursor=next_cursor,
                complete=complete,
                returned_items=len(page.results),
                returned_bytes=page.returned_bytes,
            )

    async def _worker_loop(self) -> None:
        store = self._require_enabled()
        retry_delay = max(0.1, self.poll_interval_s)
        recover_running = False
        while not self._closing:
            try:
                if recover_running:
                    await store.recover_interrupted(self.max_attempts)
                    recover_running = False
                record = await store.claim_next()
                self._store_available = True
                retry_delay = max(0.1, self.poll_interval_s)
                if record is None:
                    self._wake.clear()
                    record = await store.claim_next()
                if record is None:
                    try:
                        await asyncio.wait_for(
                            self._wake.wait(),
                            timeout=self.poll_interval_s,
                        )
                    except TimeoutError:
                        await store.cleanup()
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self._store_available = False
                logger.exception("Crawl job worker could not access its durable store")
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=retry_delay)
                except TimeoutError:
                    pass
                retry_delay = min(30.0, retry_delay * 2)
                continue
            try:
                await self._run_job(record)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Crawl job worker failed job %s", record.job_id)
                try:
                    await store.append_summary(record.job_id, "local_processing_failure")
                    await store.finish(
                        record.job_id,
                        "failed",
                        "local_processing_failure",
                    )
                except Exception:
                    recover_running = True
                    logger.exception(
                        "Crawl job worker could not persist failure for %s",
                        record.job_id,
                    )

    @staticmethod
    def _result_key(result: FetchResult) -> str:
        url = result.final_url or result.requested_url
        try:
            identity = str(dedup_key_for(url))
        except Exception:
            identity = url
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def _failure_key(cls, url: str) -> str:
        try:
            identity = str(dedup_key_for(url))
        except Exception:
            identity = url
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _bounded_failure(url: str, reason: str) -> str:
        return f"{url} {reason}"[:MAX_JOB_FAILURE_SUMMARY_CHARS]

    @staticmethod
    def _retryable_response(response: CrawlResponse) -> bool:
        return (
            response.outcome in RETRYABLE_JOB_OUTCOMES
            and response.stats.pages_succeeded == 0
        )

    async def _wait_backoff(self, event: asyncio.Event, attempt: int) -> bool:
        delay = min(30.0, self.retry_base_s * (2 ** max(0, attempt - 1)))
        try:
            await asyncio.wait_for(event.wait(), timeout=delay)
            return True
        except TimeoutError:
            return False

    async def _run_job(self, record: CrawlJobRecord) -> None:
        store = self._require_enabled()
        if self.runner is None:
            raise RuntimeError("crawl job runner is unavailable")
        cancel_event = asyncio.Event()
        self._active_cancel[record.job_id] = cancel_event
        response: CrawlResponse | None = None
        last_failure = "local_processing_failure"

        async def on_result(result: FetchResult) -> None:
            try:
                await store.append_result(
                    record.job_id,
                    self._result_key(result),
                    result,
                    (self._failure_key(result.requested_url),),
                )
            except CrawlJobResultTooLarge:
                await on_failure(result.requested_url, "result_too_large")

        async def on_failure(url: str, reason: str) -> None:
            await store.append_failure(
                record.job_id,
                self._failure_key(url),
                self._bounded_failure(url, reason),
            )

        try:
            current = record
            while True:
                if cancel_event.is_set():
                    await store.finish(record.job_id, "cancelled", "cancelled")
                    return
                response = None
                try:
                    response = await self.runner(
                        current.request,
                        on_result,
                        on_failure,
                        cancel_event.is_set,
                    )
                    retryable = self._retryable_response(response)
                    last_failure = response.outcome
                except asyncio.CancelledError:
                    raise
                except CrawlOperationCancelled:
                    await store.finish(record.job_id, "cancelled", "cancelled")
                    return
                except CapacityUnavailable:
                    retryable = True
                    last_failure = "capacity_unavailable"
                    await store.append_summary(record.job_id, last_failure)
                except (RouteDeadlineExceeded, TimeoutError):
                    retryable = True
                    last_failure = "deadline_cancelled"
                    await store.append_summary(record.job_id, last_failure)
                except Exception:
                    retryable = False
                    last_failure = "local_processing_failure"
                    await store.append_summary(record.job_id, last_failure)
                if cancel_event.is_set():
                    await store.finish(record.job_id, "cancelled", "cancelled")
                    return
                if not retryable or current.attempts >= self.max_attempts:
                    break
                if await self._wait_backoff(cancel_event, current.attempts):
                    await store.finish(record.job_id, "cancelled", "cancelled")
                    return
                current = await store.increment_attempt(record.job_id)

            final_record = await store.get_any(record.job_id)
            if final_record is None:
                return
            state: CrawlJobState
            if final_record.pages_failed > 0:
                state = "partial" if final_record.results_available > 0 else "failed"
            elif response is not None and response.outcome in {"completed", "limit_reached"}:
                state = "completed"
            elif final_record.results_available > 0:
                state = "partial"
            else:
                state = "failed"
            if final_record.pages_failed > 0 and response is not None:
                outcome = "partial" if final_record.results_available > 0 else "failed"
            else:
                outcome = response.outcome if response is not None else last_failure
            warnings = tuple(response.warnings) if response is not None else ()
            await store.finish(record.job_id, state, outcome, warnings)
        finally:
            self._active_cancel.pop(record.job_id, None)


def disabled_crawl_jobs() -> CrawlJobManager:
    return CrawlJobManager(
        enabled=False,
        store=None,
        runner=None,
        synchronous_max_pages=10,
        max_attempts=1,
        retry_base_s=0,
        max_page_items=1,
        max_page_bytes=1,
    )
