import anyio
import httpx
import pytest

from orchestrator.clients.reranker_client import RerankerClient, RerankerUnavailable
from orchestrator.observability import reset_request_id, set_request_id
from orchestrator.types import Chunk


def _chunks():
    return [
        Chunk("a", 1, "u1", "T1", 0),
        Chunk("b", 1, "u2", "T2", 0),
        Chunk("c", 1, "u3", "T3", 0),
    ]


def _reranker(**kwargs) -> RerankerClient:
    values = {
        "endpoint": "http://reranker:80",
        "model": "m",
        "path": "/rerank",
        "batch_size": 32,
        "timeout_s": 30.0,
    }
    values.update(kwargs)
    return RerankerClient(**values)


def test_maps_scores_by_index(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]})
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(_reranker().rerank, "q", _chunks())

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

    out = anyio.run(_reranker().rerank, "q", _chunks())

    assert [(item.chunk.text, item.score) for item in out] == [("a", 0.9), ("b", 0.4), ("c", 0.1)]


def test_partial_response_floor_scores_missing_chunks(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"index": 2, "score": 0.5}]}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(_reranker().rerank, "q", _chunks())

    assert [item.chunk.text for item in out] == ["c", "a", "b"]
    assert len(out) == 3
    assert out[1].score == out[2].score
    assert out[1].score < out[0].score


def test_empty_results_raise_unavailable(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": []}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(_reranker().rerank, "q", _chunks())


def test_malformed_result_raises_unavailable(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"index": 0, "score": "bad"}]}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(_reranker().rerank, "q", _chunks())


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(RerankerUnavailable):
        anyio.run(_reranker().rerank, "q", _chunks())


def test_custom_path_and_relevance_score_shape(monkeypatch):
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.7}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(_reranker(endpoint="http://reranker:8080", path="/reranking").rerank, "q", _chunks())

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

    anyio.run(_reranker(api_key="secret").rerank, "q", _chunks())

    assert seen["authorization"] == "Bearer secret"


def test_request_id_is_forwarded(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"results": [{"index": 0, "score": 0.7}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-789")
    try:
        anyio.run(_reranker().rerank, "q", _chunks())
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-789"


def test_batches_requests_and_maps_local_indexes_to_global_chunks():
    seen_document_batches = []

    def handler(req):
        import json

        documents = json.loads(req.content)["documents"]
        seen_document_batches.append(documents)
        return httpx.Response(200, json={"results": [{"index": len(documents) - 1, "score": 0.9}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reranker = _reranker(client=client, batch_size=2)

    out = anyio.run(reranker.rerank, "q", _chunks())

    assert seen_document_batches == [["a", "b"], ["c"]]
    assert [item.chunk.text for item in out[:2]] == ["b", "c"]
    assert out.telemetry.batches == 2
    assert out.telemetry.batches_failed == 0
    assert out.telemetry.floor_filled is True
    assert out.telemetry.scored_count == 2
    assert not hasattr(reranker, "last_batches")


def test_partial_batch_failure_keeps_successful_scores_and_floor_fills_failed_batch():
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"results": [{"index": 0, "score": 0.9}, {"index": 1, "score": 0.8}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reranker = _reranker(client=client, batch_size=2)
    chunks = _chunks() + [Chunk("d", 1, "u4", "T4", 0)]

    out = anyio.run(reranker.rerank, "q", chunks)

    assert len(out) == 4
    assert out.telemetry.batches == 2
    assert out.telemetry.batches_failed == 1
    assert out.telemetry.floor_filled is True
    assert out.telemetry.scored_count == 2
    assert [item.chunk.text for item in out[:2]] == ["a", "b"]
    assert out[2].score == out[3].score
    assert out[2].score < out[1].score


def test_all_batch_failures_raise_unavailable():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(500, json={})))
    reranker = _reranker(client=client, batch_size=2)

    with pytest.raises(RerankerUnavailable) as exc:
        anyio.run(reranker.rerank, "q", _chunks())

    assert exc.value.telemetry.batches == 2
    assert exc.value.telemetry.batches_failed == 2


def test_concurrent_calls_keep_request_owned_telemetry_isolated():
    first_started = anyio.Event()
    release_first = anyio.Event()

    async def handler(req):
        import json

        payload = json.loads(req.content)
        if payload["query"] == "first" and payload["documents"][0] == "a":
            first_started.set()
            await release_first.wait()
        if payload["query"] == "second":
            release_first.set()
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"results": [{"index": 0, "score": 0.9}]})

    reranker = _reranker(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        batch_size=1,
    )
    outcomes = {}

    async def run(name, chunks):
        try:
            outcomes[name] = await reranker.rerank(name, chunks)
        except RerankerUnavailable as exc:
            outcomes[name] = exc

    async def exercise():
        async with anyio.create_task_group() as group:
            group.start_soon(run, "first", _chunks()[:2])
            await first_started.wait()
            group.start_soon(run, "second", _chunks()[:1])

    anyio.run(exercise)

    assert outcomes["first"].telemetry.batches == 2
    assert outcomes["first"].telemetry.batches_failed == 0
    assert outcomes["second"].telemetry.batches == 1
    assert outcomes["second"].telemetry.batches_failed == 1
