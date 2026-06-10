import anyio
import json
import httpx
import pytest

from orchestrator.clients.chunker_client import ChunkerClient, ChunkerUnavailable
from orchestrator.observability import reset_request_id, set_request_id
from orchestrator.types import Page


def test_flattens_chunks_with_provenance(monkeypatch):
    seen = {}

    def handler(req):
        seen["metadata"] = json.loads(req.content)["metadata"]
        return httpx.Response(200, json={"chunks": [
            {
                "text": "chunk",
                "token_count": 2,
                "position": 0,
                "metadata": {
                    "source_url": "https://a.test",
                    "title": "A",
                    "source_id": 7,
                    "chunk_strategy": "cluster-semantic-greedy-token",
                    "embedding_degraded": True,
                },
            }
        ], "chunk_strategy": "cluster-semantic-greedy-token", "embedding_degraded": True})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(ChunkerClient("http://chunker:8000").chunk, [Page("https://a.test", "A", "md", 7)])

    assert seen["metadata"]["source_id"] == 7
    assert out[0].source_url == "https://a.test"
    assert out[0].title == "A"
    assert out[0].position == 0
    assert out[0].source_id == 7
    assert out[0].chunk_strategy == "cluster-semantic-greedy-token"
    assert out[0].embedding_degraded is True


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(ChunkerUnavailable):
        anyio.run(ChunkerClient("http://chunker:8000").chunk, [Page("u", "T", "md")])


def test_per_page_4xx_skips_page_and_continues(monkeypatch):
    def handler(req):
        source_url = json.loads(req.content)["metadata"]["source_url"]
        if source_url == "https://b.test":
            return httpx.Response(422, json={"detail": "bad page"})
        return httpx.Response(200, json={"chunks": [
            {
                "text": f"chunk for {source_url}",
                "token_count": 3,
                "position": 0,
                "metadata": {"source_url": source_url, "title": source_url},
            }
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        ChunkerClient("http://chunker:8000").chunk,
        [
            Page("https://a.test", "A", "md a"),
            Page("https://b.test", "B", "md b"),
            Page("https://c.test", "C", "md c"),
        ],
    )

    assert [chunk.source_url for chunk in out] == ["https://a.test", "https://c.test"]


def test_api_key_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    def handler(req):
        seen["authorization"] = req.headers.get("authorization")
        return httpx.Response(200, json={"chunks": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(
        ChunkerClient("http://chunker:8000", api_key="secret").chunk,
        [Page("https://a.test", "A", "md")],
    )

    assert seen["authorization"] == "Bearer secret"


def test_request_id_is_forwarded(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"chunks": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-456")
    try:
        anyio.run(ChunkerClient("http://chunker:8000").chunk, [Page("https://a.test", "A", "md")])
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-456"
