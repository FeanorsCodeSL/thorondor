import asyncio
import time

import httpx
import pytest

from orchestrator import app as appmod
from orchestrator import fakes
from orchestrator.crawl_job_store import SqliteCrawlJobStore
from orchestrator.crawl_jobs import CrawlJobManager
from orchestrator.models import CrawlResponse, FetchOutcomeCount, FetchResult, SiteStats
from orchestrator.site_pipeline import CrawlOperationCancelled


def api_manager(
    tmp_path,
    *,
    runner,
    clock=time.time,
    retention_s=60,
    max_page_items=10,
    max_page_bytes=16_384,
    max_records=100,
    raw_html_enabled=False,
):
    return CrawlJobManager(
        enabled=True,
        store=SqliteCrawlJobStore(
            str(tmp_path / "jobs.sqlite3"),
            retention_s=retention_s,
            tombstone_s=30,
            max_failure_summaries=16,
            max_failure_summary_chars=256,
            max_result_item_bytes=16_384,
            max_records=max_records,
            clock=clock,
        ),
        runner=runner,
        synchronous_max_pages=10,
        max_attempts=2,
        retry_base_s=0,
        max_page_items=max_page_items,
        max_page_bytes=max_page_bytes,
        raw_html_enabled=raw_html_enabled,
        poll_interval_s=0.01,
    )


def completed_response(request):
    return CrawlResponse(
        requested_url=request.url,
        effective_url=request.url,
        requested_origin="https://fixture.test",
        effective_origin="https://fixture.test",
        outcome="completed",
        urls=[],
        results=[],
        stats=SiteStats(
            discovered=1,
            admitted=1,
            queued=1,
            fetched=1,
            filtered=0,
            failed=0,
            cancelled=0,
            omitted=0,
            results_omitted=0,
            pages_succeeded=1,
            pages_failed=0,
            sitemap_documents_attempted=0,
            sitemap_documents=0,
            sitemap_entries=0,
            sitemap_truncated=0,
            fetch_outcomes=[FetchOutcomeCount(outcome="content", count=1)],
            elapsed_ms=1,
        ),
    )


async def client_for(runtime):
    appmod.deps = runtime
    await runtime.start()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=appmod.app),
        base_url="http://thorondor:8080",
    )


async def wait_for_http_state(client, job_id, state):
    headers = {"X-Thorondor-Job-Scope": "agent-a"}
    for _ in range(100):
        status = await client.get(f"/v1/crawl/jobs/{job_id}", headers=headers)
        if status.json()["state"] == state:
            return status
        await asyncio.sleep(0.005)
    raise AssertionError(f"job did not reach {state}")


def test_crawl_job_api_create_replay_scope_status_and_results(monkeypatch, tmp_path):
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
                    capabilities=["markdown"],
                    markdown="fixture",
                )
            )
            return completed_response(request)

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(tmp_path, runner=runner)
        monkeypatch.setattr(appmod, "deps", runtime)
        headers = {
            "X-Thorondor-Job-Scope": "agent-a",
            "Idempotency-Key": "api-request-0001",
        }
        body = {"url": "https://fixture.test", "max_pages": 11}
        client = await client_for(runtime)
        try:
            created = await client.post("/v1/crawl/jobs", headers=headers, json=body)
            assert created.status_code == 202
            job_id = created.json()["job"]["job_id"]
            replay = await client.post("/v1/crawl/jobs", headers=headers, json=body)
            assert replay.status_code == 200
            assert replay.json()["replayed"] is True
            assert replay.json()["job"]["job_id"] == job_id
            assert (await client.get(f"/v1/crawl/jobs/{job_id}")).status_code == 422
            assert (
                await client.get(
                    f"/v1/crawl/jobs/{job_id}",
                    headers={"X-Thorondor-Job-Scope": "agent-b"},
                )
            ).status_code == 404
            status = await wait_for_http_state(client, job_id, "completed")
            assert status.json()["progress"]["results_available"] == 1
            page = await client.get(
                f"/v1/crawl/jobs/{job_id}/results",
                headers={"X-Thorondor-Job-Scope": "agent-a"},
                params={"max_items": 1, "max_bytes": 16384},
            )
            assert page.status_code == 200
            assert page.json()["results"][0]["outcome"] == "content"
            assert page.json()["complete"] is True
        finally:
            await client.aclose()
            await runtime.aclose()

    asyncio.run(exercise())


def test_crawl_job_api_idempotency_conflict_and_disabled_mode(monkeypatch, tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, _cancelled):
            return completed_response(request)

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(tmp_path, runner=runner)
        monkeypatch.setattr(appmod, "deps", runtime)
        headers = {
            "X-Thorondor-Job-Scope": "agent-a",
            "Idempotency-Key": "api-request-0002",
        }
        client = await client_for(runtime)
        try:
            assert (
                await client.post(
                    "/v1/crawl/jobs",
                    headers=headers,
                    json={"url": "https://fixture.test/a", "max_pages": 11},
                )
            ).status_code == 202
            conflict = await client.post(
                "/v1/crawl/jobs",
                headers=headers,
                json={"url": "https://fixture.test/b", "max_pages": 11},
            )
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["reason"] == "idempotency_conflict"
        finally:
            await client.aclose()
            await runtime.aclose()

        disabled = fakes.deps()
        monkeypatch.setattr(appmod, "deps", disabled)
        client = await client_for(disabled)
        try:
            response = await client.post(
                "/v1/crawl/jobs",
                headers=headers,
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            assert response.status_code == 503
            assert response.json()["detail"]["reason"] == "crawl_jobs_disabled"
        finally:
            await client.aclose()
            await disabled.aclose()

    asyncio.run(exercise())


def test_crawl_job_api_returns_expired_state_after_absolute_retention(monkeypatch, tmp_path):
    async def exercise():
        now = [1_000.0]

        async def runner(request, _on_result, _on_failure, _cancelled):
            return completed_response(request)

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(
            tmp_path,
            runner=runner,
            clock=lambda: now[0],
            retention_s=5,
        )
        monkeypatch.setattr(appmod, "deps", runtime)
        headers = {
            "X-Thorondor-Job-Scope": "agent-a",
            "Idempotency-Key": "api-request-0003",
        }
        client = await client_for(runtime)
        try:
            created = await client.post(
                "/v1/crawl/jobs",
                headers=headers,
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            job_id = created.json()["job"]["job_id"]
            await wait_for_http_state(client, job_id, "completed")
            now[0] += 6
            expired = await client.get(
                f"/v1/crawl/jobs/{job_id}",
                headers={"X-Thorondor-Job-Scope": "agent-a"},
            )
            assert expired.status_code == 410
            assert expired.json()["state"] == "expired"
        finally:
            await client.aclose()
            await runtime.aclose()

    asyncio.run(exercise())


def test_crawl_job_api_uses_configured_result_page_defaults(monkeypatch, tmp_path):
    async def exercise():
        async def runner(request, on_result, _on_failure, _cancelled):
            for suffix in ("one", "two"):
                await on_result(
                    FetchResult(
                        requested_url=f"{request.url}/{suffix}",
                        final_url=f"{request.url}/{suffix}",
                        outcome="content",
                        retryable=False,
                        status_code=200,
                        content_type="text/html",
                        title=suffix,
                        retrieval_method="fixture",
                        elapsed_ms=1,
                        capabilities=["markdown"],
                        markdown=suffix,
                    )
                )
            return completed_response(request)

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(
            tmp_path,
            runner=runner,
            max_page_items=1,
            max_page_bytes=4096,
        )
        monkeypatch.setattr(appmod, "deps", runtime)
        headers = {
            "X-Thorondor-Job-Scope": "agent-a",
            "Idempotency-Key": "api-request-0004",
        }
        client = await client_for(runtime)
        try:
            created = await client.post(
                "/v1/crawl/jobs",
                headers=headers,
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            job_id = created.json()["job"]["job_id"]
            await wait_for_http_state(client, job_id, "completed")
            page = await client.get(
                f"/v1/crawl/jobs/{job_id}/results",
                headers={"X-Thorondor-Job-Scope": "agent-a"},
            )
            assert page.status_code == 200
            assert page.json()["returned_items"] == 1
            assert page.json()["next_cursor"] is not None
        finally:
            await client.aclose()
            await runtime.aclose()

    asyncio.run(exercise())


def test_crawl_job_api_cancellation_and_validation_errors(monkeypatch, tmp_path):
    async def exercise():
        async def runner(request, _on_result, _on_failure, cancelled):
            for _ in range(1000):
                if cancelled():
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("cancellation was not observed")
            raise CrawlOperationCancelled

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(tmp_path, runner=runner)
        monkeypatch.setattr(appmod, "deps", runtime)
        client = await client_for(runtime)
        try:
            raw_html = await client.post(
                "/v1/crawl/jobs",
                headers={
                    "X-Thorondor-Job-Scope": "agent-a",
                    "Idempotency-Key": "raw-html-api",
                },
                json={
                    "url": "https://fixture.test/raw",
                    "max_pages": 11,
                    "capabilities": ["markdown", "raw_html"],
                },
            )
            assert raw_html.status_code == 400
            assert raw_html.json()["detail"]["reason"] == "crawl_job_raw_html_disabled"

            invalid_scope = await client.post(
                "/v1/crawl/jobs",
                headers={
                    "X-Thorondor-Job-Scope": " ",
                    "Idempotency-Key": "invalid-scope-api",
                },
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            assert invalid_scope.status_code == 400
            assert invalid_scope.json()["detail"]["reason"] == "invalid_job_scope"

            created = await client.post(
                "/v1/crawl/jobs",
                headers={
                    "X-Thorondor-Job-Scope": "agent-a",
                    "Idempotency-Key": "cancel-api",
                },
                json={"url": "https://fixture.test/cancel", "max_pages": 11},
            )
            job_id = created.json()["job"]["job_id"]
            cancelled = await client.post(
                f"/v1/crawl/jobs/{job_id}/cancel",
                headers={"X-Thorondor-Job-Scope": "agent-a"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["state"] in {"running", "cancelled"}
        finally:
            await client.aclose()
            await runtime.aclose()

    asyncio.run(exercise())


def test_crawl_job_api_reports_bounded_store_capacity(monkeypatch, tmp_path):
    async def exercise():
        release = asyncio.Event()

        async def runner(request, _on_result, _on_failure, _cancelled):
            await release.wait()
            return completed_response(request)

        runtime = fakes.deps()
        runtime.crawl_jobs = api_manager(tmp_path, runner=runner, max_records=1)
        monkeypatch.setattr(appmod, "deps", runtime)
        client = await client_for(runtime)
        try:
            headers = {
                "X-Thorondor-Job-Scope": "agent-a",
                "Idempotency-Key": "bounded-one",
            }
            first = await client.post(
                "/v1/crawl/jobs",
                headers=headers,
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            assert first.status_code == 202
            full = await client.post(
                "/v1/crawl/jobs",
                headers={**headers, "Idempotency-Key": "bounded-two"},
                json={"url": "https://fixture.test", "max_pages": 11},
            )
            assert full.status_code == 429
            assert full.headers["Retry-After"]
            assert full.json()["detail"]["reason"] == "crawl_job_store_full"
        finally:
            release.set()
            await client.aclose()
            await runtime.aclose()

    asyncio.run(exercise())


def test_crawl_job_openapi_advertises_headers_limits_and_runtime_statuses():
    appmod.app.openapi_schema = None
    schema = appmod.app.openapi()
    paths = schema["paths"]
    create = paths["/v1/crawl/jobs"]["post"]
    status = paths["/v1/crawl/jobs/{job_id}"]["get"]
    results = paths["/v1/crawl/jobs/{job_id}/results"]["get"]
    cancel = paths["/v1/crawl/jobs/{job_id}/cancel"]["post"]

    assert {"200", "202", "400", "409", "413", "429", "503"}.issubset(
        create["responses"]
    )
    for operation in (status, results, cancel):
        assert {"400", "404", "410", "429", "503"}.issubset(
            operation["responses"]
        )
        headers = {
            item["name"]
            for item in operation["parameters"]
            if item["in"] == "header"
        }
        assert "X-Thorondor-Job-Scope" in headers
    assert "413" in results["responses"]
    assert "413" in cancel["responses"]

    create_headers = {
        item["name"] for item in create["parameters"] if item["in"] == "header"
    }
    assert create_headers == {"X-Thorondor-Job-Scope", "Idempotency-Key"}
    assert all(
        item["required"]
        for item in create["parameters"]
        if item["in"] == "header"
    )
    query = {
        item["name"]: item["schema"]
        for item in results["parameters"]
        if item["in"] == "query"
    }
    assert query["cursor"]["anyOf"][0]["maxLength"] == 512
    assert query["max_items"]["anyOf"][0]["minimum"] == 1
    assert query["max_items"]["anyOf"][0]["maximum"] == 20
    assert query["max_bytes"]["anyOf"][0]["minimum"] == 1


def test_lifespan_closes_partially_started_dependencies(monkeypatch):
    async def exercise():
        class BrokenRuntime:
            def __init__(self):
                self.closed = False

            async def start(self):
                raise RuntimeError("crawl job startup failed")

            async def aclose(self):
                self.closed = True

        runtime = BrokenRuntime()
        monkeypatch.setattr(appmod, "deps", runtime)
        with pytest.raises(RuntimeError, match="startup failed"):
            async with appmod.lifespan(appmod.app):
                pass
        assert runtime.closed is True

    asyncio.run(exercise())
