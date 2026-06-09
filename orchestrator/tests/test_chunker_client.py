import anyio
import httpx
import pytest

from orchestrator.clients.chunker_client import ChunkerClient, ChunkerUnavailable
from orchestrator.types import Page


def test_flattens_chunks_with_provenance(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"chunks": [
            {
                "text": "chunk",
                "token_count": 2,
                "position": 0,
                "metadata": {"source_url": "https://a.test", "title": "A"},
            }
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(ChunkerClient("http://chunker:8000").chunk, [Page("https://a.test", "A", "md")])

    assert out[0].source_url == "https://a.test"
    assert out[0].title == "A"
    assert out[0].position == 0


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(ChunkerUnavailable):
        anyio.run(ChunkerClient("http://chunker:8000").chunk, [Page("u", "T", "md")])
