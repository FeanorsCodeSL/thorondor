import anyio
from types import SimpleNamespace
from fastapi.testclient import TestClient
import httpx

import orchestrator.app as appmod
from orchestrator import fakes


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

    assert versioned == legacy


def test_search_includes_raw_markdown(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    body = client.post("/search", json={"query": "x", "include_raw_markdown": True}).json()

    assert body["raw_markdown"]


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
