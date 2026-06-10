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


def test_join_url_normalizes_slashes():
    assert appmod._join_url("http://reranker:8080/", "health") == "http://reranker:8080/health"
    assert appmod._join_url("http://reranker:8080", "/health") == "http://reranker:8080/health"


def test_health_check_can_send_internal_headers(monkeypatch):
    seen = {}

    class FakeHealthClient:
        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            return httpx.Response(200)

    monkeypatch.setattr(appmod, "get_health_client", lambda: FakeHealthClient())

    name, ok = anyio.run(
        appmod._check_url,
        "searxng",
        "http://searxng:8080/search?q=health&format=json",
        appmod.SEARXNG_INTERNAL_HEADERS,
    )

    assert (name, ok) == ("searxng", True)
    assert seen["headers"] == {"X-Real-IP": "127.0.0.1"}
