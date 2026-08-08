import asyncio
import sqlite3

import pytest

from orchestrator.crawl_job_store import (
    CrawlJobExpired,
    CrawlJobNotFound,
    CrawlJobQueueFull,
    CrawlJobResultTooLarge,
    IdempotencyConflict,
    SqliteCrawlJobStore,
)
from orchestrator.crawl_jobs import (
    CrawlJobApiCapacityUnavailable,
    CrawlJobManager,
    InvalidCursor,
    RawHtmlPersistenceDisabled,
)
from orchestrator.models import (
    CrawlRequest,
    CrawlResponse,
    FetchOutcomeCount,
    FetchResult,
    SiteStats,
)
from orchestrator.resource_policy import RouteDeadlineExceeded
from orchestrator.site_pipeline import CrawlOperationCancelled


class Clock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value


def result(url: str, text: str = "content") -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        outcome="content",
        retryable=False,
        status_code=200,
        content_type="text/html",
        title=url.rsplit("/", 1)[-1],
        retrieval_method="fixture",
        elapsed_ms=1,
        capabilities=["markdown"],
        markdown=text,
    )


def response(
    request: CrawlRequest,
    *,
    outcome: str = "completed",
    succeeded: int = 1,
    failed: int = 0,
) -> CrawlResponse:
    return CrawlResponse(
        requested_url=request.url,
        effective_url=request.url,
        requested_origin="https://fixture.test",
        effective_origin="https://fixture.test",
        outcome=outcome,
        urls=[],
        results=[],
        stats=SiteStats(
            discovered=succeeded + failed,
            admitted=succeeded + failed,
            queued=succeeded + failed,
            fetched=succeeded,
            filtered=0,
            failed=failed,
            cancelled=0,
            omitted=0,
            results_omitted=0,
            pages_succeeded=succeeded,
            pages_failed=failed,
            sitemap_documents_attempted=0,
            sitemap_documents=0,
            sitemap_entries=0,
            sitemap_truncated=0,
            fetch_outcomes=[
                *(
                    [FetchOutcomeCount(outcome="content", count=succeeded)]
                    if succeeded
                    else []
                ),
                *(
                    [FetchOutcomeCount(outcome="robots_refused", count=failed)]
                    if failed
                    else []
                ),
            ],
            elapsed_ms=1,
        ),
    )


def manager(
    tmp_path,
    runner,
    *,
    clock=None,
    retention_s=60,
    max_attempts=3,
    max_records=100,
    raw_html_enabled=False,
    max_inflight_requests=16,
    admission_wait_s=1,
):
    store = SqliteCrawlJobStore(
        str(tmp_path / "jobs.sqlite3"),
        retention_s=retention_s,
        tombstone_s=30,
        max_failure_summaries=16,
        max_failure_summary_chars=256,
        max_result_item_bytes=16_384,
        max_records=max_records,
        clock=clock or Clock(),
    )
    return CrawlJobManager(
        enabled=True,
        store=store,
        runner=runner,
        synchronous_max_pages=10,
        max_attempts=max_attempts,
        retry_base_s=0,
        max_page_items=10,
        max_page_bytes=16_384,
        raw_html_enabled=raw_html_enabled,
        max_inflight_requests=max_inflight_requests,
        admission_wait_s=admission_wait_s,
        poll_interval_s=0.01,
    )


async def wait_for_state(job_manager, job_id, state, *, scope="agent-a"):
    for _ in range(200):
        status = await job_manager.status(job_id, scope=scope)
        if status.state == state:
            return status
        await asyncio.sleep(0.005)
    raise AssertionError(f"job did not reach {state}")


def test_atomic_idempotency_replays_identical_request_and_conflicts_on_change(tmp_path):
    async def exercise():
        release = asyncio.Event()

        async def runner(request, on_result, _on_failure, _cancelled):
            await release.wait()
            await on_result(result(request.url))
            return response(request)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        request = CrawlRequest(url="https://fixture.test/a", max_pages=11)
        first, second = await asyncio.gather(
            job_manager.create(request, scope="agent-a", idempotency_key="request-0001"),
            job_manager.create(request, scope="agent-a", idempotency_key="request-0001"),
        )
        assert first.job.job_id == second.job.job_id
        assert sorted((first.replayed, second.replayed)) == [False, True]
        with pytest.raises(IdempotencyConflict):
            await job_manager.create(
                CrawlRequest(url="https://fixture.test/b", max_pages=11),
                scope="agent-a",
                idempotency_key="request-0001",
            )
        with pytest.raises(CrawlJobNotFound):
            await job_manager.status(first.job.job_id, scope="agent-b")
        release.set()
        await wait_for_state(job_manager, first.job.job_id, "completed")
        await job_manager.aclose()

    asyncio.run(exercise())


def test_idle_worker_waits_for_a_wake_instead_of_polling_sqlite(tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, _cancelled):
            return response(request)

        job_manager = manager(tmp_path, runner)
        original = job_manager.store.claim_next
        calls = 0

        async def counted_claim_next():
            nonlocal calls
            calls += 1
            return await original()

        job_manager.store.claim_next = counted_claim_next
        await job_manager.start()
        await asyncio.sleep(0.005)
        assert calls == 2
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-idle-wake",
        )
        await wait_for_state(job_manager, created.job.job_id, "completed")
        await job_manager.aclose()

    asyncio.run(exercise())


def test_partial_completion_and_terminal_reads_are_idempotent(tmp_path):
    async def exercise():
        async def runner(request, on_result, on_failure, _cancelled):
            await on_result(result(f"{request.url}/ok"))
            await on_failure(f"{request.url}/denied", "robots_refused")
            return response(request, outcome="partial", succeeded=1, failed=1)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=12),
            scope="agent-a",
            idempotency_key="request-0002",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "partial")
        reread = await job_manager.status(created.job.job_id, scope="agent-a")
        assert reread == terminal
        assert terminal.progress.pages_succeeded == 1
        assert terminal.progress.pages_failed == 1
        assert terminal.failure_summaries == [
            "https://fixture.test/denied robots_refused"
        ]
        await job_manager.aclose()

    asyncio.run(exercise())


def test_retry_exhaustion_is_bounded(tmp_path):
    async def exercise():
        attempts = 0

        async def runner(_request, _on_result, _on_failure, _cancelled):
            nonlocal attempts
            attempts += 1
            raise RouteDeadlineExceeded("crawl")

        job_manager = manager(tmp_path, runner, max_attempts=3)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0003",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "failed")
        assert attempts == 3
        assert terminal.progress.attempts == 3
        assert terminal.outcome == "deadline_cancelled"
        assert terminal.failure_summaries == ["deadline_cancelled"]
        await job_manager.aclose()

    asyncio.run(exercise())


def test_cancellation_wins_one_terminal_transition(tmp_path):
    async def exercise():
        running = asyncio.Event()

        async def runner(request, _on_result, _on_failure, cancelled):
            running.set()
            for _ in range(1000):
                if cancelled():
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("cancellation was not observed")
            raise CrawlOperationCancelled

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0004",
        )
        await running.wait()
        requested = await job_manager.cancel(created.job.job_id, scope="agent-a")
        assert requested.cancel_requested is True
        terminal = await wait_for_state(job_manager, created.job.job_id, "cancelled")
        repeated = await job_manager.cancel(created.job.job_id, scope="agent-a")
        assert repeated == terminal
        await job_manager.aclose()

    asyncio.run(exercise())


def test_retention_is_absolute_and_idempotency_claim_expires_with_job(tmp_path):
    async def exercise():
        clock = Clock()

        async def runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(f"{request.url}/1"))
            await on_result(result(f"{request.url}/2"))
            return response(request, succeeded=2)

        job_manager = manager(tmp_path, runner, clock=clock, retention_s=10)
        await job_manager.start()
        request = CrawlRequest(url="https://fixture.test", max_pages=11)
        created = await job_manager.create(
            request,
            scope="agent-a",
            idempotency_key="request-0005",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "completed")
        deadline = terminal.retention_deadline
        first_page = await job_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=None,
            max_items=1,
            max_bytes=16_384,
        )
        assert first_page.next_cursor is not None
        with pytest.raises(CrawlJobResultTooLarge):
            await job_manager.results(
                created.job.job_id,
                scope="agent-a",
                cursor=None,
                max_items=1,
                max_bytes=1,
            )
        await job_manager.status(created.job.job_id, scope="agent-a")
        reread = await job_manager.status(created.job.job_id, scope="agent-a")
        assert reread.retention_deadline == deadline
        clock.value += 11
        with pytest.raises(CrawlJobExpired):
            await job_manager.status(created.job.job_id, scope="agent-a")
        with pytest.raises(CrawlJobExpired):
            await job_manager.results(
                created.job.job_id,
                scope="agent-a",
                cursor=first_page.next_cursor,
                max_items=1,
                max_bytes=16_384,
            )
        replay = await job_manager.create(
            request,
            scope="agent-a",
            idempotency_key="request-0005",
        )
        assert replay.replayed is False
        assert replay.job.job_id != created.job.job_id
        await job_manager.aclose()

    asyncio.run(exercise())


def test_pagination_is_stable_bounded_and_observes_concurrent_completion(tmp_path):
    async def exercise():
        release = asyncio.Event()
        first_batch = asyncio.Event()

        async def runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(f"{request.url}/1", "one"))
            await on_result(result(f"{request.url}/2", "two"))
            first_batch.set()
            await release.wait()
            await on_result(result(f"{request.url}/3", "three"))
            return response(request, succeeded=3)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0006",
        )
        await first_batch.wait()
        first = await job_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=None,
            max_items=1,
            max_bytes=16_384,
        )
        second = await job_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=first.next_cursor,
            max_items=1,
            max_bytes=16_384,
        )
        assert [first.results[0].final_url, second.results[0].final_url] == [
            "https://fixture.test/1",
            "https://fixture.test/2",
        ]
        assert first.returned_items == second.returned_items == 1
        assert first.complete is False
        assert second.complete is False
        release.set()
        await wait_for_state(job_manager, created.job.job_id, "completed")
        third = await job_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=second.next_cursor,
            max_items=1,
            max_bytes=16_384,
        )
        assert [item.final_url for item in third.results] == ["https://fixture.test/3"]
        assert third.complete is True
        assert third.next_cursor is None
        forged = job_manager._cursor(created.job.job_id, 999)
        with pytest.raises(InvalidCursor):
            await job_manager.results(
                created.job.job_id,
                scope="agent-a",
                cursor=forged,
                max_items=1,
                max_bytes=16_384,
            )
        await job_manager.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "cursor",
    [
        "é" * 1000,
        "!" * 16,
        "eyJ2IjoxLCJqb2IiOiJiYWQiLCJhZnRlciI6dHJ1ZX0",
    ],
)
def test_cursor_validation_fails_closed(cursor):
    with pytest.raises(InvalidCursor):
        CrawlJobManager._decode_cursor("0" * 32, cursor)


def test_result_storage_failure_cannot_report_completed(tmp_path):
    async def exercise():
        async def runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(request.url, "x" * 20_000))
            return response(request)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test/oversized", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0010",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "failed")
        assert terminal.outcome == "failed"
        assert terminal.progress.results_available == 0
        assert terminal.progress.pages_failed == 1
        assert terminal.failure_summaries == [
            "https://fixture.test/oversized result_too_large"
        ]
        await job_manager.aclose()

    asyncio.run(exercise())


def test_restart_recovers_running_job_without_duplicate_results(tmp_path):
    async def exercise():
        interrupted = asyncio.Event()
        hold = asyncio.Event()

        async def first_runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(f"{request.url}/1"))
            interrupted.set()
            await hold.wait()
            return response(request)

        first_manager = manager(tmp_path, first_runner)
        await first_manager.start()
        created = await first_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0007",
        )
        await interrupted.wait()
        await first_manager.aclose()

        async def recovered_runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(f"{request.url}/1"))
            await on_result(result(f"{request.url}/2"))
            return response(request, succeeded=2)

        second_manager = manager(tmp_path, recovered_runner)
        await second_manager.start()
        terminal = await wait_for_state(second_manager, created.job.job_id, "completed")
        page = await second_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=None,
            max_items=10,
            max_bytes=16_384,
        )
        assert terminal.progress.attempts == 2
        assert terminal.progress.results_available == 2
        assert [item.final_url for item in page.results] == [
            "https://fixture.test/1",
            "https://fixture.test/2",
        ]
        await second_manager.aclose()

    asyncio.run(exercise())


def test_restart_preserves_a_persisted_running_cancellation(tmp_path):
    async def exercise():
        running = asyncio.Event()
        hold = asyncio.Event()

        async def first_runner(request, _on_result, _on_failure, _cancelled):
            running.set()
            await hold.wait()
            return response(request)

        first_manager = manager(tmp_path, first_runner)
        await first_manager.start()
        created = await first_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0008",
        )
        await running.wait()
        await first_manager.store.request_cancel(
            created.job.job_id,
            first_manager.scope_hash("agent-a"),
        )
        await first_manager.aclose()

        resumed = False

        async def recovered_runner(request, _on_result, _on_failure, _cancelled):
            nonlocal resumed
            resumed = True
            return response(request)

        second_manager = manager(tmp_path, recovered_runner)
        await second_manager.start()
        terminal = await second_manager.status(created.job.job_id, scope="agent-a")
        assert terminal.state == "cancelled"
        assert terminal.outcome == "cancelled"
        assert resumed is False
        await second_manager.aclose()

    asyncio.run(exercise())


def test_successful_redirect_retry_reconciles_the_requested_url_failure(tmp_path):
    async def exercise():
        attempts = 0

        async def runner(request, on_result, on_failure, _cancelled):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await on_failure(request.url, "upstream_timeout")
                transient = response(
                    request,
                    outcome="upstream_timeout",
                    succeeded=0,
                    failed=1,
                )
                transient.stats.fetch_outcomes = [
                    FetchOutcomeCount(outcome="upstream_timeout", count=1)
                ]
                return transient
            await on_result(
                FetchResult(
                    requested_url=request.url,
                    final_url="https://fixture.test/final",
                    outcome="content",
                    retryable=False,
                    status_code=200,
                    content_type="text/html",
                    title="Final",
                    retrieval_method="fixture",
                    elapsed_ms=1,
                    capabilities=["markdown"],
                    markdown="content",
                )
            )
            return response(request)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test/requested", max_pages=11),
            scope="agent-a",
            idempotency_key="request-0009",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "completed")
        assert terminal.progress.pages_processed == 1
        assert terminal.progress.pages_succeeded == 1
        assert terminal.progress.pages_failed == 0
        assert terminal.failure_summaries == [
            "https://fixture.test/requested upstream_timeout"
        ]
        await job_manager.aclose()

    asyncio.run(exercise())


def test_completed_response_with_transient_page_failure_is_not_retried(tmp_path):
    async def exercise():
        attempts = 0

        async def runner(request, on_result, on_failure, _cancelled):
            nonlocal attempts
            attempts += 1
            await on_result(result(f"{request.url}/ok"))
            await on_failure(f"{request.url}/timeout", "upstream_timeout")
            completed = response(request, outcome="completed", succeeded=1, failed=1)
            completed.stats.fetch_outcomes.append(
                FetchOutcomeCount(outcome="upstream_timeout", count=1)
            )
            return completed

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="no-amplification",
        )
        terminal = await wait_for_state(job_manager, created.job.job_id, "partial")
        assert attempts == 1
        assert terminal.progress.attempts == 1
        await job_manager.aclose()

    asyncio.run(exercise())


def test_raw_html_persistence_requires_job_specific_opt_in(tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, _cancelled):
            return response(request)

        job_manager = manager(tmp_path, runner)
        await job_manager.start()
        with pytest.raises(RawHtmlPersistenceDisabled):
            await job_manager.create(
                CrawlRequest(
                    url="https://fixture.test",
                    max_pages=11,
                    capabilities=["markdown", "raw_html"],
                ),
                scope="agent-a",
                idempotency_key="raw-html-disabled",
            )
        await job_manager.aclose()

    asyncio.run(exercise())


def test_raw_html_persistence_is_available_after_explicit_opt_in(tmp_path):
    async def exercise():
        async def runner(request, on_result, _on_failure, _cancelled):
            await on_result(
                FetchResult(
                    requested_url=request.url,
                    final_url=request.url,
                    outcome="content",
                    retryable=False,
                    status_code=200,
                    content_type="text/html",
                    title="Fixture",
                    retrieval_method="fixture",
                    elapsed_ms=1,
                    capabilities=["markdown", "raw_html"],
                    markdown="fixture",
                    raw_html="<p>fixture</p>",
                )
            )
            return response(request)

        job_manager = manager(tmp_path, runner, raw_html_enabled=True)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(
                url="https://fixture.test",
                max_pages=11,
                capabilities=["markdown", "raw_html"],
            ),
            scope="agent-a",
            idempotency_key="raw-html-enabled",
        )
        await wait_for_state(job_manager, created.job.job_id, "completed")
        page = await job_manager.results(
            created.job.job_id,
            scope="agent-a",
            cursor=None,
            max_items=1,
            max_bytes=16_384,
        )
        assert page.results[0].raw_html == "<p>fixture</p>"
        await job_manager.aclose()

    asyncio.run(exercise())


def test_store_limit_replays_existing_claim_but_rejects_new_jobs(tmp_path):
    async def exercise():
        release = asyncio.Event()

        async def runner(request, _on_result, _on_failure, _cancelled):
            await release.wait()
            return response(request)

        job_manager = manager(tmp_path, runner, max_records=1)
        await job_manager.start()
        request = CrawlRequest(url="https://fixture.test", max_pages=11)
        first = await job_manager.create(
            request,
            scope="agent-a",
            idempotency_key="record-one",
        )
        replay = await job_manager.create(
            request,
            scope="agent-a",
            idempotency_key="record-one",
        )
        assert replay.replayed is True
        assert replay.job.job_id == first.job.job_id
        with pytest.raises(CrawlJobQueueFull):
            await job_manager.create(
                request,
                scope="agent-a",
                idempotency_key="record-two",
            )
        release.set()
        await job_manager.aclose()

    asyncio.run(exercise())


def test_job_api_store_operations_have_bounded_admission(tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, _cancelled):
            return response(request)

        job_manager = manager(
            tmp_path,
            runner,
            max_inflight_requests=1,
            admission_wait_s=0.01,
        )
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="bounded-api",
        )
        entered = asyncio.Event()
        release = asyncio.Event()
        original = job_manager.store.get

        async def blocking_get(job_id, scope_hash):
            entered.set()
            await release.wait()
            return await original(job_id, scope_hash)

        job_manager.store.get = blocking_get
        first = asyncio.create_task(
            job_manager.status(created.job.job_id, scope="agent-a")
        )
        await entered.wait()
        with pytest.raises(CrawlJobApiCapacityUnavailable):
            await job_manager.status(created.job.job_id, scope="agent-a")
        release.set()
        await first
        await job_manager.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("access", ["status", "results", "cancel"])
def test_expiry_commits_before_the_typed_expired_error(tmp_path, access):
    async def exercise():
        clock = Clock()

        async def runner(request, on_result, _on_failure, _cancelled):
            await on_result(result(request.url))
            return response(request)

        job_manager = manager(tmp_path, runner, clock=clock, retention_s=1)
        await job_manager.start()
        created = await job_manager.create(
            CrawlRequest(url="https://fixture.test", max_pages=11),
            scope="agent-a",
            idempotency_key="expiry-commit",
        )
        await wait_for_state(job_manager, created.job.job_id, "completed")
        clock.value += 2
        with pytest.raises(CrawlJobExpired):
            if access == "status":
                await job_manager.status(created.job.job_id, scope="agent-a")
            elif access == "results":
                await job_manager.results(
                    created.job.job_id,
                    scope="agent-a",
                    cursor=None,
                    max_items=1,
                    max_bytes=16_384,
                )
            else:
                await job_manager.cancel(created.job.job_id, scope="agent-a")
        persisted = await job_manager.store.get_any(created.job.job_id)
        assert persisted.state == "expired"
        with sqlite3.connect(job_manager.store.path) as connection:
            row = connection.execute(
                "SELECT idempotency_hash FROM crawl_jobs WHERE job_id = ?",
                (created.job.job_id,),
            ).fetchone()
            results = connection.execute(
                "SELECT COUNT(*) FROM crawl_job_results WHERE job_id = ?",
                (created.job.job_id,),
            ).fetchone()[0]
        assert row[0] is None
        assert results == 0
        await job_manager.aclose()

    asyncio.run(exercise())


def test_result_reconciliation_accounts_for_every_prior_failure_key(tmp_path):
    async def exercise():
        store = SqliteCrawlJobStore(
            str(tmp_path / "accounting.sqlite3"),
            retention_s=60,
            tombstone_s=30,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
        )
        await store.start()
        request = CrawlRequest(url="https://fixture.test", max_pages=11)
        claim = await store.claim(
            job_id="a" * 32,
            scope_hash="scope",
            idempotency_hash="key",
            request_fingerprint="request",
            request=request,
        )
        assert claim.replayed is False
        await store.claim_next()
        await store.append_failure("a" * 32, "requested", "requested timeout")
        await store.append_failure("a" * 32, "final", "final timeout")
        await store.append_result(
            "a" * 32,
            "final",
            result("https://fixture.test/final"),
            ("requested",),
        )
        record = await store.get_any("a" * 32)
        assert record.pages_processed == 1
        assert record.pages_succeeded == 1
        assert record.pages_failed == 0

    asyncio.run(exercise())


def test_worker_recovers_after_a_transient_store_error(tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, _cancelled):
            return response(request)

        job_manager = manager(tmp_path, runner)
        original = job_manager.store.claim_next
        calls = 0

        async def flaky_claim_next():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("temporary store failure")
            return await original()

        job_manager.store.claim_next = flaky_claim_next
        await job_manager.start()
        for _ in range(100):
            if calls >= 2 and job_manager.healthy:
                break
            await asyncio.sleep(0.01)
        assert calls >= 2
        assert job_manager.healthy is True
        await job_manager.aclose()

    asyncio.run(exercise())


def test_corrupt_or_incompatible_job_database_is_quarantined(tmp_path):
    async def exercise():
        path = tmp_path / "corrupt.sqlite3"
        path.write_bytes(b"not a sqlite database")
        store = SqliteCrawlJobStore(
            str(path),
            retention_s=60,
            tombstone_s=30,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
        )
        await store.start()
        assert path.exists()
        assert list(tmp_path.glob("corrupt.sqlite3.corrupt-*"))

        incompatible = tmp_path / "incompatible.sqlite3"
        connection = sqlite3.connect(incompatible)
        try:
            connection.execute("CREATE TABLE crawl_jobs (unexpected TEXT)")
        finally:
            connection.close()
        second = SqliteCrawlJobStore(
            str(incompatible),
            retention_s=60,
            tombstone_s=30,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
        )
        await second.start()
        assert list(tmp_path.glob("incompatible.sqlite3.corrupt-*"))

    asyncio.run(exercise())


def test_future_job_schema_fails_closed_without_quarantine(tmp_path):
    async def exercise():
        path = tmp_path / "future.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE crawl_job_schema (version INTEGER NOT NULL)")
            connection.execute("INSERT INTO crawl_job_schema VALUES (2)")
        store = SqliteCrawlJobStore(
            str(path),
            retention_s=60,
            tombstone_s=30,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
        )
        with pytest.raises(RuntimeError, match="newer than supported"):
            await store.start()
        assert not list(tmp_path.glob("future.sqlite3.corrupt-*"))

    asyncio.run(exercise())
