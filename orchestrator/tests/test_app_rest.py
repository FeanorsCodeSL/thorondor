import anyio
import asyncio
from types import SimpleNamespace
from fastapi.testclient import TestClient
import httpx
import ipaddress
import pytest
import time

import orchestrator.app as appmod
from orchestrator import fakes
import orchestrator.url_safety as url_safety
from orchestrator.types import DiscoveryOutcome, DiscoveryResult, Page


def _health_settings(**overrides):
    values = {
        "searxng_url": "http://searxng:8080",
        "crawl4ai_url": "http://crawl4ai:11235",
        "chunker_url": "http://chunker:8000",
        "reranker_endpoint": "http://reranker:80",
        "reranker_health_path": "/health",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_search_happy_path(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    body = client.post("/search", json={"query": "x"}).json()

    assert body["passages"]
    assert body["citations"]
    assert body["stats"]
    assert body["schema_version"] == "thorondor.search.v1"
    passage = body["passages"][0]
    citation = body["citations"][0]
    assert passage["verbatim"] is True
    assert passage["document_id"] == citation["document_id"]
    assert passage["evidence_id"] == citation["evidence_spans"][0]["evidence_id"]
    assert citation["metadata"]["title"]["source"] == "fetch_title"


def test_search_exposes_publication_modification_sources_and_conflicts(monkeypatch):
    class PublishedDiscovery:
        async def search(self, _query, _freshness=None):
            return DiscoveryOutcome(
                [
                    DiscoveryResult(
                        "Discovered",
                        "https://a.test/article",
                        "snippet",
                        "fixture",
                        1.0,
                        published_at="2026-08-04T08:00:00Z",
                    )
                ],
                [],
            )

    class MetadataExtractor:
        async def extract(self, urls):
            html = """
            <html lang="en"><head>
              <title>Evidence title</title>
              <script type="application/ld+json">
                {"datePublished":"2026-08-04T10:15:00+02:00","dateModified":"2026-08-05T11:30:00+02:00"}
              </script>
            </head></html>
            """
            return [Page(urls[0], "Fetched", "# Heading\n\nEvidence body.", html=html)]

    monkeypatch.setattr(
        appmod,
        "deps",
        fakes.deps(discovery=PublishedDiscovery(), extractor=MetadataExtractor()),
    )

    body = TestClient(appmod.app).post("/search", json={"query": "x"}).json()
    citation = body["citations"][0]

    assert citation["published"] == "2026-08-04T08:15:00Z"
    assert citation["modified_at"] == "2026-08-05T09:30:00Z"
    assert citation["metadata"]["published_at"]["source"] == "json_ld"
    assert citation["metadata"]["modified_at"]["source"] == "json_ld"
    conflicts = {item["field"]: item for item in citation["metadata"]["conflicts"]}
    assert {candidate["source"] for candidate in conflicts["published_at"]["candidates"]} == {
        "json_ld",
        "discovery",
    }


def test_search_returns_request_id_header(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    response = client.post("/search", json={"query": "x"}, headers={"X-Request-ID": "req-123"})

    assert response.headers["X-Request-ID"] == "req-123"


def test_v1_search_alias_matches_legacy_path(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    legacy = client.post("/search", json={"query": "x"}).json()
    versioned = client.post("/v1/search", json={"query": "x"}).json()

    for response in (legacy, versioned):
        response["stats"]["elapsed_ms"] = 0
        for attempt in response["stats"]["subquery_diagnostics"]:
            attempt["elapsed_ms"] = 0
    assert versioned == legacy


def test_search_includes_raw_markdown(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    body = client.post("/search", json={"query": "x", "include_raw_markdown": True}).json()

    assert body["raw_markdown"]
    raw = body["raw_markdown"][0]
    citation = next(item for item in body["citations"] if item["id"] == raw["citation_id"])
    passage = next(item for item in body["passages"] if item["citation_id"] == raw["citation_id"])
    assert raw["document_id"] == citation["document_id"] == passage["document_id"]
    assert raw["cleaned_markdown"][passage["start_index"]:passage["end_index"]] == passage["text"]


def test_dependency_outage_maps_to_503(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps(discovery=fakes.DownDiscovery()))
    client = TestClient(appmod.app)

    r = client.post("/search", json={"query": "x"})

    assert r.status_code == 503
    assert r.json()["detail"] == {"dependency": "searxng", "reason": "searxng_unavailable"}


def test_missing_query_is_422(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)
    assert client.post("/search", json={}).status_code == 422


def test_invalid_bounds_are_422_before_fanout(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps(discovery=fakes.DownDiscovery()))
    client = TestClient(appmod.app)

    assert client.post("/search", json={"query": "x", "max_urls": 999}).status_code == 422


def test_livez_is_process_only(monkeypatch):
    class FailingHealthClient:
        async def get(self, url, headers=None):
            raise AssertionError(f"unexpected dependency probe: {url}")

    monkeypatch.setattr(appmod, "get_health_client", lambda: FailingHealthClient())
    client = TestClient(appmod.app)

    for _ in range(3):
        response = client.get("/livez")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_livez_stays_responsive_while_url_safety_dns_is_slow(monkeypatch):
    policy = url_safety.UrlSafetyPolicy(
        blocked_ip_categories={
            "loopback",
            "link_local",
            "private",
            "reserved",
            "multicast",
            "unspecified",
        },
        blocked_special_ips={
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("fd00:ec2::254"),
        },
        nat64_networks=[ipaddress.ip_network("64:ff9b::/96")],
        six_to_four_networks=[ipaddress.ip_network("2002::/16")],
        ipv4_compat_networks=[ipaddress.ip_network("::/96")],
    )

    def slow_resolver(_host):
        time.sleep(0.2)
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(url_safety, "resolve_host_ips", slow_resolver)

    async def exercise():
        started = time.perf_counter()
        safety_task = asyncio.create_task(url_safety.is_safe_crawl_url_async("https://slow.example", policy))
        await asyncio.sleep(0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=appmod.app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/livez")
        elapsed = time.perf_counter() - started
        return response, elapsed, await safety_task

    response, elapsed, is_safe = anyio.run(exercise)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert elapsed < 0.1
    assert is_safe is True


def test_healthz_probes_even_when_deps_are_warm(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    monkeypatch.setattr(appmod, "settings", _health_settings(searxng_url="http://closed-port:9"))

    class FakeHealthClient:
        async def get(self, url, headers=None):
            if "closed-port" in url:
                raise httpx.ConnectError("closed")
            if "chunker" in url:
                return httpx.Response(200, json={"status": "ok", "embedding": True})
            return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())
    client = TestClient(appmod.app)

    body = client.get("/healthz").json()

    assert body["status"] == "degraded"
    assert body["dependencies"]["searxng"] is False
    assert body["dependencies"]["chunker"] is True
    assert body["dependencies"]["embedding"] is True
    assert body["hard_failures"] == ["searxng"]


def test_healthz_reports_all_green(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    monkeypatch.setattr(appmod, "settings", _health_settings())

    class FakeHealthClient:
        async def get(self, url, headers=None):
            if "chunker" in url:
                return httpx.Response(200, json={"status": "ok", "embedding": True})
            return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())
    client = TestClient(appmod.app)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert body["dependencies"] == {
        "searxng": True,
        "crawl4ai": True,
        "chunker": True,
        "embedding": True,
        "reranker": True,
    }


def test_repeated_healthz_requests_never_search_searxng(monkeypatch):
    seen = []

    class FakeHealthClient:
        async def get(self, url, headers=None):
            seen.append((url, headers))
            if "chunker" in url:
                return httpx.Response(200, json={"status": "ok", "embedding": True})
            return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(appmod, "settings", _health_settings())
    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())
    client = TestClient(appmod.app)

    for _ in range(3):
        assert client.get("/healthz").json()["dependencies"]["searxng"] is True

    searxng_requests = [request for request in seen if request[0].startswith("http://searxng:8080")]
    assert searxng_requests == [
        ("http://searxng:8080/healthz", {"X-Real-IP": "127.0.0.1"})
    ] * 3
    assert all("/search" not in url for url, _headers in seen)


def test_join_url_normalizes_slashes():
    assert appmod._join_url("http://reranker:8080/", "health") == "http://reranker:8080/health"
    assert appmod._join_url("http://reranker:8080", "/health") == "http://reranker:8080/health"


def test_health_check_rejects_non_success_status(monkeypatch):
    class FakeHealthClient:
        async def get(self, url, headers=None):
            return httpx.Response(404)

    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())

    name, ok = anyio.run(appmod._check_url, "searxng", "http://searxng:8080/healthz")

    assert (name, ok) == ("searxng", False)


def test_health_check_rejects_timeout(monkeypatch):
    class FakeHealthClient:
        async def get(self, url, headers=None):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())

    name, ok = anyio.run(appmod._check_url, "searxng", "http://searxng:8080/healthz")

    assert (name, ok) == ("searxng", False)


def test_v1_fetch_returns_typed_outcome(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())

    response = TestClient(appmod.app).post(
        "/v1/fetch",
        json={"urls": ["https://a.test/article"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "thorondor.fetch.v1"
    assert body["results"][0]["outcome"] == "content"
    assert body["results"][0]["trust"] == "untrusted"
    assert body["stats"] == {
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "outcomes": [{"outcome": "content", "count": 1}],
        "elapsed_ms": body["stats"]["elapsed_ms"],
    }


def test_oversized_request_body_returns_413_before_validation(monkeypatch):
    deps = fakes.deps()
    deps.resource_policy = appmod.ResourcePolicy(
        **(deps.resource_policy.__dict__ | {"max_request_body_bytes": 32})
    )
    deps.admission = appmod.RuntimeAdmission(deps.resource_policy)
    monkeypatch.setattr(appmod, "deps", deps)

    response = TestClient(appmod.app).post(
        "/v1/search",
        content=b'{"query":"' + (b"x" * 64) + b'"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "reason": "request_body_too_large",
        "max_bytes": 32,
    }


def test_capacity_returns_429_with_retry_after_and_livez_remains_available(monkeypatch):
    deps = fakes.deps(
        resource_policy=appmod.ResourcePolicy(
            max_inflight_searches=1,
            admission_wait_s=0.001,
        )
    )
    monkeypatch.setattr(appmod, "deps", deps)

    async def exercise():
        async with deps.admission.search_slot():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=appmod.app),
                base_url="http://testserver",
            ) as client:
                rejected = await client.post("/v1/search", json={"query": "x"})
                live = await client.get("/livez")
        return rejected, live

    rejected, live = anyio.run(exercise)

    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "1"
    assert rejected.json()["detail"] == {
        "reason": "capacity_unavailable",
        "route": "search",
    }
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_search_route_deadline_returns_closed_504(monkeypatch):
    class BlockingPlanner:
        async def plan(self, _query):
            await asyncio.Event().wait()

    policy = appmod.ResourcePolicy(search_route_deadline_s=0.01)
    monkeypatch.setattr(
        appmod,
        "deps",
        fakes.deps(planner=BlockingPlanner(), resource_policy=policy),
    )

    response = TestClient(appmod.app).post("/v1/search", json={"query": "x"})

    assert response.status_code == 504
    assert response.json()["detail"] == {
        "reason": "deadline_cancelled",
        "route": "search",
    }


def test_caller_cancellation_stops_underlying_operation():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class ConnectedRequest:
        async def is_disconnected(self):
            await asyncio.sleep(0)
            return False

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def exercise():
        task = asyncio.create_task(
            appmod._run_http_operation(ConnectedRequest(), operation(), appmod.SearchResponse)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()

    anyio.run(exercise)


def test_client_disconnect_stops_underlying_operation():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class DisconnectedRequest:
        async def is_disconnected(self):
            await started.wait()
            return True

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def exercise():
        with pytest.raises(appmod.HTTPException) as exc_info:
            await appmod._run_http_operation(
                DisconnectedRequest(),
                operation(),
                appmod.SearchResponse,
            )
        assert exc_info.value.status_code == 499
        assert exc_info.value.detail == {"reason": "client_disconnected"}
        assert cancelled.is_set()

    anyio.run(exercise)


def test_response_body_limit_returns_closed_error(monkeypatch):
    policy = appmod.ResourcePolicy(
        max_response_body_bytes=128,
        max_content_bytes=128,
    )
    monkeypatch.setattr(
        appmod,
        "deps",
        fakes.deps(resource_policy=policy),
    )

    response = TestClient(appmod.app).post("/v1/search", json={"query": "x"})

    assert response.status_code == 500
    assert response.json()["detail"] == {"reason": "response_body_too_large"}


def test_openapi_documents_shared_transport_errors_and_fetch_contract(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())

    schema = TestClient(appmod.app).get("/openapi.json").json()

    assert "/v1/fetch" in schema["paths"]
    for path in ("/v1/search", "/v1/fetch"):
        responses = schema["paths"][path]["post"]["responses"]
        assert {"413", "429", "500", "504"}.issubset(responses)
