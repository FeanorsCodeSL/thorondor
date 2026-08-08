import anyio
import asyncio
import json
import httpx
import pytest

from orchestrator.clients.chunker_client import ChunkerClient, ChunkerUnavailable
from orchestrator.observability import reset_request_id, set_request_id
from orchestrator.types import Page


def test_flattens_chunks_with_provenance(monkeypatch):
    seen = {}

    def handler(req):
        request = json.loads(req.content)
        seen["metadata"] = request["metadata"]
        seen["source_type"] = request["source_type"]
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
    assert seen["source_type"] == "ORCHESTRATOR_MARKDOWN"
    assert out[0].source_url == "https://a.test"
    assert out[0].title == "A"
    assert out[0].position == 0
    assert out[0].source_id == 7
    assert out[0].chunk_strategy == "cluster-semantic-greedy-token"
    assert out[0].embedding_degraded is True
    assert out[0].verbatim is False
    assert out[0].evidence_id is None


def test_exact_chunks_receive_document_evidence_identity_and_section_heading():
    document = (
        "# First\n\nAlpha evidence.\n\n"
        "```python\n# Not a section\nvalue = 1\n```\n\n"
        "## Second ###\n\nBeta evidence."
    )

    def handler(req):
        request = json.loads(req.content)
        code_heading = document.index("# Not a section")
        code_heading_end = document.index("\n", code_heading) + 1
        beta = document.index("Beta")
        return httpx.Response(
            200,
            json={
                "chunks": [
                    {
                        "text": document[:code_heading],
                        "token_count": 4,
                        "position": 0,
                        "start_index": 0,
                        "end_index": code_heading,
                        "verbatim": True,
                        "metadata": request["metadata"],
                    },
                    {
                        "text": document[code_heading:code_heading_end],
                        "token_count": 4,
                        "position": 1,
                        "start_index": code_heading,
                        "end_index": code_heading_end,
                        "verbatim": True,
                        "metadata": request["metadata"],
                    },
                    {
                        "text": document[beta:],
                        "token_count": 2,
                        "position": 2,
                        "start_index": beta,
                        "end_index": len(document),
                        "verbatim": True,
                        "metadata": request["metadata"],
                    }
                ],
                "chunk_strategy": "cluster-semantic-dp",
                "embedding_degraded": False,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    page = Page(
        "https://requested.test/start",
        "Evidence",
        document,
        source_id=4,
        final_url="https://final.test/article",
    )

    chunks = anyio.run(ChunkerClient("http://chunker:8000", client=client).chunk, [page])

    assert len(chunks) == 3
    assert all(chunk.text == document[chunk.start_index:chunk.end_index] for chunk in chunks)
    assert all(chunk.verbatim is True for chunk in chunks)
    assert all(chunk.source_url == "https://final.test/article" for chunk in chunks)
    assert len({chunk.document_id for chunk in chunks}) == 1
    assert all(chunk.evidence_id for chunk in chunks)
    assert [chunk.section_heading for chunk in chunks] == ["First", "First", "Second"]


def test_mismatched_or_unmarked_chunks_cannot_receive_evidence_ids():
    responses = [
        {
            "text": "changed",
            "token_count": 1,
            "position": 0,
            "start_index": 0,
            "end_index": 5,
            "verbatim": True,
        },
        {
            "text": "Alpha",
            "token_count": 1,
            "position": 1,
            "start_index": 0,
            "end_index": 5,
        },
    ]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"chunks": responses}))
    )

    chunks = anyio.run(
        ChunkerClient("http://chunker:8000", client=client).chunk,
        [Page("https://a.test", "A", "Alpha evidence")],
    )

    assert len(chunks) == 2
    assert all(chunk.verbatim is False for chunk in chunks)
    assert all(chunk.start_index is None and chunk.end_index is None for chunk in chunks)
    assert all(chunk.evidence_id is None for chunk in chunks)


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


def test_all_pages_rejected_by_chunker_is_an_explicit_dependency_failure():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(422, json={"detail": "unsupported source type"})
        )
    )

    with pytest.raises(ChunkerUnavailable, match="all pages rejected with status 422"):
        anyio.run(
            ChunkerClient("http://chunker:8000", client=client).chunk,
            [
                Page("https://a.test", "A", "md a"),
                Page("https://b.test", "B", "md b"),
            ],
        )


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


def test_chunking_uses_bounded_concurrency_and_preserves_page_order():
    active = 0
    peak = 0

    async def handler(req):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        payload = json.loads(req.content)
        await anyio.sleep(0.01 if payload["metadata"]["source_url"].endswith("a") else 0)
        active -= 1
        return httpx.Response(
            200,
            json={
                "chunks": [
                    {
                        "text": payload["text"],
                        "token_count": 1,
                        "position": 0,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chunker = ChunkerClient(
        "http://chunker:8000",
        client=client,
        concurrency=2,
        timeout_s=1,
    )

    chunks = anyio.run(
        chunker.chunk,
        [Page("https://a.test/a", "A", "a"), Page("https://b.test/b", "B", "b")],
    )

    assert peak == 2
    assert [chunk.text for chunk in chunks] == ["a", "b"]
    anyio.run(client.aclose)


def test_chunk_stage_timeout_cancels_pending_work():
    cancelled = asyncio.Event()

    async def handler(_req):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chunker = ChunkerClient(
        "http://chunker:8000",
        client=client,
        concurrency=1,
        timeout_s=0.01,
    )

    with pytest.raises(ChunkerUnavailable) as exc_info:
        anyio.run(chunker.chunk, [Page("https://a.test", "A", "a")])

    assert exc_info.value.reason == "timeout"
    assert cancelled.is_set()
    anyio.run(client.aclose)


def test_chunk_concurrency_is_shared_across_calls():
    active = 0
    peak = 0

    async def handler(req):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await anyio.sleep(0.01)
        active -= 1
        payload = json.loads(req.content)
        return httpx.Response(
            200,
            json={"chunks": [{"text": payload["text"], "token_count": 1}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chunker = ChunkerClient(
        "http://chunker:8000",
        client=client,
        concurrency=1,
        timeout_s=1,
    )

    async def exercise():
        await asyncio.gather(
            chunker.chunk([Page("https://a.test", "A", "a")]),
            chunker.chunk([Page("https://b.test", "B", "b")]),
        )

    anyio.run(exercise)

    assert peak == 1
    anyio.run(client.aclose)
