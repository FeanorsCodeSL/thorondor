from fastapi.testclient import TestClient

import orchestrator.app as appmod
from orchestrator import fakes


def test_search_happy_path(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    body = client.post("/search", json={"query": "x"}).json()

    assert body["passages"]
    assert body["citations"]
    assert body["stats"]
    assert body["schema_version"] == "thorondor.search.v1"


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


def test_healthz_shape(monkeypatch):
    monkeypatch.setattr(appmod, "deps", fakes.deps())
    client = TestClient(appmod.app)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"searxng", "crawl4ai", "chunker", "reranker"}


def test_join_url_normalizes_slashes():
    assert appmod._join_url("http://reranker:8080/", "health") == "http://reranker:8080/health"
    assert appmod._join_url("http://reranker:8080", "/health") == "http://reranker:8080/health"
