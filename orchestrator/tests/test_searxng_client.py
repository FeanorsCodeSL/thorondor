import anyio
import httpx
import pytest

from orchestrator.clients.searxng_client import DiscoveryUnavailable, SearxngDiscovery
from orchestrator.observability import reset_request_id, set_request_id


def test_parses_searxng_json_and_freshness(monkeypatch):
    seen = {}

    def handler(req):
        seen["query"] = str(req.url)
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://a.test", "content": "snip", "engine": "brave", "score": 1.0}
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q", "week")

    assert out[0].url == "https://a.test" and out[0].engine == "brave"
    assert "time_range=week" in seen["query"]


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(DiscoveryUnavailable):
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")


def test_zero_scores_fall_back_to_rank_order(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "first", "url": "https://first.test", "score": 0},
            {"title": "second", "url": "https://second.test", "score": 0},
            {"title": "third", "url": "https://third.test", "score": 0},
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")

    assert [item.url for item in out] == ["https://first.test", "https://second.test", "https://third.test"]
    assert [item.score for item in out] == [1.0, 0.5, 1 / 3]


def test_api_key_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    def handler(req):
        seen["authorization"] = req.headers.get("authorization")
        seen["x_real_ip"] = req.headers.get("x-real-ip")
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(SearxngDiscovery("http://searxng:8080", api_key="secret").search, "q")

    assert seen["authorization"] == "Bearer secret"
    assert seen["x_real_ip"] == "127.0.0.1"


def test_request_id_is_forwarded(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-101")
    try:
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-101"
