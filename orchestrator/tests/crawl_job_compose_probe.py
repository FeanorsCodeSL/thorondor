import asyncio
import json
import os
import sys
import time
from pathlib import Path

from orchestrator.crawl_job_store import CrawlJobExpired, SqliteCrawlJobStore
from orchestrator.crawl_jobs import CrawlJobManager
from orchestrator.models import (
    CrawlRequest,
    CrawlResponse,
    FetchOutcomeCount,
    FetchResult,
    SiteStats,
)
from orchestrator.site_pipeline import CrawlOperationCancelled


def fetched(url):
    return FetchResult(
        requested_url=url,
        final_url=url,
        outcome="content",
        retryable=False,
        status_code=200,
        content_type="text/html",
        title="Fixture",
        retrieval_method="compose_fixture",
        elapsed_ms=1,
        capabilities=["markdown"],
        markdown=f"# {url.rsplit('/', 1)[-1]}",
    )


def completed(request, count):
    return CrawlResponse(
        requested_url=request.url,
        effective_url=request.url,
        requested_origin="https://fixture.test",
        effective_origin="https://fixture.test",
        outcome="completed",
        urls=[],
        results=[],
        stats=SiteStats(
            discovered=count,
            admitted=count,
            queued=count,
            fetched=count,
            filtered=0,
            failed=0,
            cancelled=0,
            omitted=0,
            results_omitted=0,
            pages_succeeded=count,
            pages_failed=0,
            sitemap_documents_attempted=0,
            sitemap_documents=0,
            sitemap_entries=0,
            sitemap_truncated=0,
            fetch_outcomes=[FetchOutcomeCount(outcome="content", count=count)],
            elapsed_ms=1,
        ),
    )


def build(path, runner, clock=time.time):
    return CrawlJobManager(
        enabled=True,
        store=SqliteCrawlJobStore(
            path,
            retention_s=1,
            tombstone_s=1,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
            clock=clock,
        ),
        runner=runner,
        synchronous_max_pages=10,
        max_attempts=3,
        retry_base_s=0,
        max_page_items=10,
        max_page_bytes=16_384,
        poll_interval_s=0.01,
    )


async def wait_state(manager, job_id, state):
    for _ in range(500):
        status = await manager.status(job_id, scope="compose-probe")
        if status.state == state:
            return status
        await asyncio.sleep(0.01)
    raise RuntimeError(f"job did not reach {state}")


async def interrupt(database, metadata):
    ready = asyncio.Event()

    async def runner(request, on_result, _on_failure, _cancelled):
        await ready.wait()
        await on_result(fetched(f"{request.url}/one"))
        os._exit(75)

    manager = build(database, runner)
    await manager.start()
    created = await manager.create(
        CrawlRequest(url="https://fixture.test", max_pages=11),
        scope="compose-probe",
        idempotency_key="compose-restart",
    )
    await asyncio.to_thread(
        Path(metadata).write_text,
        json.dumps({"job_id": created.job.job_id}),
        encoding="utf-8",
    )
    ready.set()
    await asyncio.Future()


async def recover(database, metadata):
    now = [time.time()]

    async def runner(request, on_result, _on_failure, cancelled):
        if request.url.endswith("/cancel"):
            for _ in range(1000):
                if cancelled():
                    break
                await asyncio.sleep(0.01)
            else:
                raise RuntimeError("cancellation was not observed")
            raise CrawlOperationCancelled
        await on_result(fetched(f"{request.url}/one"))
        await on_result(fetched(f"{request.url}/two"))
        return completed(request, 2)

    manager = build(database, runner, clock=lambda: now[0])
    await manager.start()
    metadata_text = await asyncio.to_thread(Path(metadata).read_text, encoding="utf-8")
    job_id = json.loads(metadata_text)["job_id"]
    recovered = await wait_state(manager, job_id, "completed")
    page = await manager.results(
        job_id,
        scope="compose-probe",
        cursor=None,
        max_items=10,
        max_bytes=16_384,
    )
    if recovered.progress.attempts != 2 or len(page.results) != 2:
        raise RuntimeError("restart recovery duplicated or lost results")
    cancelled = await manager.create(
        CrawlRequest(url="https://fixture.test/cancel", max_pages=11),
        scope="compose-probe",
        idempotency_key="compose-cancel",
    )
    await wait_state(manager, cancelled.job.job_id, "running")
    await manager.cancel(cancelled.job.job_id, scope="compose-probe")
    await wait_state(manager, cancelled.job.job_id, "cancelled")
    now[0] += 1.1
    try:
        await manager.status(job_id, scope="compose-probe")
    except CrawlJobExpired:
        pass
    else:
        raise RuntimeError("retention deadline did not expire the recovered job")
    now[0] += 1
    removed = await manager.store.cleanup()
    if removed < 1:
        raise RuntimeError("expired job tombstone was not purged")
    await manager.aclose()
    print(
        json.dumps(
            {
                "recovered_job": job_id,
                "attempts": recovered.progress.attempts,
                "deduplicated_results": len(page.results),
                "cancelled_job": cancelled.job.job_id,
                "purged_tombstones": removed,
            },
            sort_keys=True,
        )
    )


def main():
    mode, database, metadata = sys.argv[1:]
    asyncio.run(
        interrupt(database, metadata)
        if mode == "interrupt"
        else recover(database, metadata)
    )


if __name__ == "__main__":
    main()
