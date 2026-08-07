import asyncio
import json
import tempfile
import time
from pathlib import Path

from orchestrator.crawl_job_store import SqliteCrawlJobStore
from orchestrator.crawl_jobs import CrawlJobManager
from orchestrator.models import (
    CrawlRequest,
    CrawlResponse,
    FetchOutcomeCount,
    FetchResult,
    SiteStats,
)

FIXTURE_REVISION = "web-intelligence-phase5-jobs-v1"
PAGE_COUNTS = (1, 10, 20)
SYNCHRONOUS_MAX_PAGES = 10
RESULT_BYTES = 2048


def _result(index: int) -> FetchResult:
    url = f"https://fixture.test/docs/{index}"
    return FetchResult(
        requested_url=url,
        final_url=url,
        outcome="content",
        retryable=False,
        status_code=200,
        content_type="text/html",
        title=f"Fixture {index}",
        retrieval_method="fixture",
        elapsed_ms=1,
        capabilities=["markdown"],
        markdown="x" * RESULT_BYTES,
    )


def _response(request: CrawlRequest, results: list[FetchResult]) -> CrawlResponse:
    count = len(results)
    return CrawlResponse(
        requested_url=request.url,
        effective_url=request.url,
        requested_origin="https://fixture.test",
        effective_origin="https://fixture.test",
        outcome="completed",
        urls=[],
        results=results,
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
            elapsed_ms=count,
        ),
    )


async def _runner(request, on_result, _on_failure, _cancelled):
    results = [_result(index) for index in range(request.max_pages)]
    for result in results:
        await on_result(result)
    return _response(request, results)


async def _wait_terminal(manager: CrawlJobManager, job_id: str):
    for _ in range(1000):
        status = await manager.status(job_id, scope="benchmark")
        if status.state == "completed":
            return status
        await asyncio.sleep(0.001)
    raise RuntimeError("benchmark job did not complete")


async def _synchronous(pages: int) -> dict:
    retained: list[FetchResult] = []
    request = CrawlRequest(url="https://fixture.test/docs", max_pages=pages)

    async def retain(result: FetchResult) -> None:
        retained.append(result)

    started = time.perf_counter()
    response = await _runner(request, retain, None, lambda: False)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "modeled_target_requests": pages,
        "network_requests": 0,
        "terminal_reason": response.outcome,
        "results_available": len(retained),
        "result_recall": len(retained) / pages,
        "duplicate_rate": 1 - (len({item.final_url for item in retained}) / pages),
        "serialized_response_bytes": len(response.model_dump_json().encode("utf-8")),
    }


async def _asynchronous(pages: int, root: Path) -> dict:
    manager = CrawlJobManager(
        enabled=True,
        store=SqliteCrawlJobStore(
            str(root / f"jobs-{pages}.sqlite3"),
            retention_s=86400,
            tombstone_s=3600,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=524288,
        ),
        runner=_runner,
        synchronous_max_pages=SYNCHRONOUS_MAX_PAGES,
        max_attempts=3,
        retry_base_s=0,
        max_page_items=20,
        max_page_bytes=1048576,
        poll_interval_s=0.001,
    )
    await manager.start()
    started = time.perf_counter()
    created = await manager.create(
        CrawlRequest(url="https://fixture.test/docs", max_pages=pages),
        scope="benchmark",
        idempotency_key=f"benchmark-{pages}",
    )
    terminal = await _wait_terminal(manager, created.job.job_id)
    page = await manager.results(
        created.job.job_id,
        scope="benchmark",
        cursor=None,
        max_items=20,
        max_bytes=1048576,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    await manager.aclose()
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "modeled_target_requests": pages,
        "network_requests": 0,
        "terminal_reason": terminal.outcome,
        "results_available": terminal.progress.results_available,
        "result_recall": len(page.results) / pages,
        "duplicate_rate": 1 - (len({item.final_url for item in page.results}) / pages),
        "attempts": terminal.progress.attempts,
        "returned_bytes": page.returned_bytes,
    }


async def _build_report() -> dict:
    with tempfile.TemporaryDirectory(prefix="thorondor-phase5-") as directory:
        root = Path(directory)
        cases = {}
        for pages in PAGE_COUNTS:
            cases[str(pages)] = {
                "synchronous": await _synchronous(pages),
                "asynchronous": await _asynchronous(pages, root),
            }
    return {
        "fixture_revision": FIXTURE_REVISION,
        "configuration": {
            "page_counts": list(PAGE_COUNTS),
            "result_markdown_bytes": RESULT_BYTES,
            "synchronous_max_pages": SYNCHRONOUS_MAX_PAGES,
            "job_max_attempts": 3,
            "job_result_page_max_bytes": 1048576,
        },
        "cases": cases,
        "decision": (
            "Keep crawls of at most 10 pages synchronous by default; use the explicit "
            "durable job endpoint for larger crawls or when recovery and polling matter."
        ),
        "limitations": (
            "Fixture timings measure local orchestration and SQLite persistence only; "
            "they do not represent target-site or Crawl4AI latency."
        ),
    }


def build_report() -> dict:
    return asyncio.run(_build_report())


def main() -> None:
    print(json.dumps(build_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
