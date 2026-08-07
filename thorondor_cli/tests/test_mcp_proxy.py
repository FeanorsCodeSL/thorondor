import asyncio
import inspect

import httpx
import pytest

pytest.importorskip("mcp")
respx = pytest.importorskip("respx")

from thorondor_cli import mcp_proxy


@respx.mock
def test_web_search_forwards_non_none_args(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    route = respx.post("http://thorondor.test/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "query": "x",
                "passages": [],
                "citations": [],
                "stats": {},
                "schema_version": "thorondor.search.v1",
            },
        )
    )

    result = asyncio.run(mcp_proxy.web_search("x", token_budget=10, max_urls=None))

    assert result["query"] == "x"
    assert route.calls.last.request.read() == b'{"query":"x","token_budget":10}'


@respx.mock
def test_web_search_returns_structured_error_on_unreachable(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    respx.post("http://thorondor.test/v1/search").mock(side_effect=httpx.ConnectError("nope"))

    result = asyncio.run(mcp_proxy.web_search("x"))

    assert result["error"] == "thorondor_unreachable"


def test_web_search_signature_matches_orchestrator():
    pytest.importorskip("orchestrator.mcp_server")
    from orchestrator import mcp_server

    assert inspect.signature(mcp_proxy.web_search) == inspect.signature(mcp_server.web_search)


@respx.mock
def test_web_fetch_forwards_capabilities(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    route = respx.post("http://thorondor.test/v1/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [],
                "stats": {
                    "requested": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "outcomes": [],
                    "elapsed_ms": 0,
                },
                "schema_version": "thorondor.fetch.v1",
            },
        )
    )

    result = asyncio.run(
        mcp_proxy.web_fetch(
            ["https://a.test/article"],
            capabilities=["markdown", "links"],
        )
    )

    assert result["schema_version"] == "thorondor.fetch.v1"
    assert route.calls.last.request.read() == (
        b'{"urls":["https://a.test/article"],"capabilities":["markdown","links"]}'
    )


@respx.mock
def test_web_fetch_preserves_closed_capacity_error(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    respx.post("http://thorondor.test/v1/fetch").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "2"},
            json={"detail": {"reason": "capacity_unavailable", "route": "fetch"}},
        )
    )

    result = asyncio.run(mcp_proxy.web_fetch(["https://a.test/article"]))

    assert result == {
        "error": "capacity_unavailable",
        "status_code": 429,
        "route": "fetch",
        "retry_after_s": 2,
    }


@respx.mock
def test_stdio_proxy_stops_reading_response_at_shared_byte_limit(monkeypatch):
    class ChunkedBody(httpx.AsyncByteStream):
        def __init__(self):
            self.yielded = 0

        async def __aiter__(self):
            for chunk in (b"x" * 40, b"y" * 40, b"z" * 40):
                self.yielded += 1
                yield chunk

    stream = ChunkedBody()
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    monkeypatch.setattr(mcp_proxy, "DEFAULT_MAX_RESPONSE_BODY_BYTES", 64)
    respx.post("http://thorondor.test/v1/fetch").mock(
        return_value=httpx.Response(200, stream=stream)
    )

    result = asyncio.run(mcp_proxy.web_fetch(["https://a.test/article"]))

    assert result == {"error": "response_body_too_large", "status_code": 502}
    assert stream.yielded == 2


def test_web_fetch_signature_matches_orchestrator():
    pytest.importorskip("orchestrator.mcp_server")
    from orchestrator import mcp_server

    assert inspect.signature(mcp_proxy.web_fetch) == inspect.signature(mcp_server.web_fetch)


def test_web_search_advertises_the_shared_concise_description():
    async def advertised_tool():
        return next(
            tool
            for tool in await mcp_proxy.mcp.list_tools()
            if tool.name == "web_search"
        )

    description = asyncio.run(advertised_tool()).description

    assert description == mcp_proxy.SEARCH_TOOL_DESCRIPTION
    assert len(description) <= 700
    assert "Search the live web and return source-cited evidence passages." in description
    assert "POST /v1/search" not in description
    assert "SearchResponse" not in description


def test_stdio_proxy_caller_cancellation_stops_http_request(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingResponse:
        is_error = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_bytes(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            yield b""

    class BlockingClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return BlockingResponse()

    monkeypatch.setattr(mcp_proxy.httpx, "AsyncClient", BlockingClient)

    async def exercise():
        task = asyncio.create_task(
            mcp_proxy.web_fetch(["https://a.test/article"])
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()

    asyncio.run(exercise())
