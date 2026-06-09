import anyio
import httpx
import pytest

from orchestrator.clients.reranker_client import RerankerClient, RerankerUnavailable
from orchestrator.types import Chunk


def _chunks():
    return [
        Chunk("a", 1, "u1", "T1", 0),
        Chunk("b", 1, "u2", "T2", 0),
        Chunk("c", 1, "u3", "T3", 0),
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
    assert [item.chunk.text for item in out] == ["b", "a", "c"]
    assert out[2].score < 0.2


def test_indexless_response_maps_positionally(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"results": [{"score": 0.9}, {"score": 0.4}, {"score": 0.1}]})
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())

    assert [(item.chunk.text, item.score) for item in out] == [("a", 0.9), ("b", 0.4), ("c", 0.1)]


def test_partial_response_floor_scores_missing_chunks(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"index": 2, "score": 0.5}]}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())

    assert [item.chunk.text for item in out] == ["c", "a", "b"]
    assert len(out) == 3
    assert out[1].score == out[2].score
    assert out[1].score < out[0].score


def test_empty_results_raise_unavailable(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": []}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())


def test_malformed_result_raises_unavailable(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"index": 0, "score": "bad"}]}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(RerankerClient("http://reranker:80", "m").rerank, "q", _chunks())


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
    assert len(out) == 3


def test_api_key_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    def handler(req):
        seen["authorization"] = req.headers.get("authorization")
        return httpx.Response(200, json={"results": [{"index": 0, "score": 0.7}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(RerankerClient("http://reranker:80", "m", api_key="secret").rerank, "q", _chunks())

    assert seen["authorization"] == "Bearer secret"
