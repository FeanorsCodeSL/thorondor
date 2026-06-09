import anyio
import httpx
import pytest

from orchestrator.clients.reranker_client import RerankerClient, RerankerUnavailable
from orchestrator.types import Chunk


def _chunks():
    return [
        Chunk("a", 1, "u1", "T1", 0),
        Chunk("b", 1, "u2", "T2", 0),
    ]


def test_maps_scores_by_index(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]})
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())

    assert out[0].chunk.text == "b"
    assert out[0].score == 0.9


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())


def test_custom_path_and_relevance_score_shape(monkeypatch):
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.7}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(RerankerClient("http://reranker:8080", "m", "/reranking").rerank, "q", _chunks())

    assert seen["path"] == "/reranking"
    assert out[0].score == 0.7
