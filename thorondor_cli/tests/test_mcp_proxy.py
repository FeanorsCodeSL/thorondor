import asyncio
import inspect
import json

import httpx
import pytest
from mcp_types import CallToolResult
from thorondor_contracts import THORONDOR_VERSION

pytest.importorskip("mcp")
respx = pytest.importorskip("respx")
mcp_proxy = pytest.importorskip("thorondor_cli.mcp_proxy")


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


def test_mcp_proxy_signatures_match_orchestrator():
    pytest.importorskip("orchestrator.mcp_server")
    from orchestrator import mcp_server

    for name in ("web_search", "web_fetch", "web_map", "web_crawl"):
        assert inspect.signature(getattr(mcp_proxy, name)) == inspect.signature(
            getattr(mcp_server, name)
        )


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
            capabilities=["markdown"],
            structured_formats=["json_schema"],
            extraction_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
            },
        )
    )

    assert result["schema_version"] == "thorondor.fetch.v1"
    assert route.calls.last.request.read() == (
        b'{"urls":["https://a.test/article"],"capabilities":["markdown"],'
        b'"structured_formats":["json_schema"],"extraction_schema":{"type":"object",'
        b'"properties":{"title":{"type":"string"}}}}'
    )


@respx.mock
def test_web_crawl_forwards_structured_formats(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    route = respx.post("http://thorondor.test/v1/crawl").mock(
        return_value=httpx.Response(
            200,
            json={
                "requested_url": "https://a.test/article",
                "outcome": "completed",
                "urls": [],
                "results": [],
                "stats": {},
                "warnings": [],
                "schema_version": "thorondor.crawl.v1",
            },
        )
    )

    asyncio.run(
        mcp_proxy.web_crawl(
            "https://a.test/article",
            capabilities=["markdown"],
            structured_formats=["tables"],
        )
    )

    assert route.calls.last.request.read() == (
        b'{"url":"https://a.test/article","capabilities":["markdown"],'
        b'"structured_formats":["tables"]}'
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


def test_stdio_proxy_uses_configured_response_body_limit(monkeypatch):
    monkeypatch.setenv("MAX_RESPONSE_BODY_BYTES", "4096")

    assert mcp_proxy._max_response_body_bytes() == 4096
    assert mcp_proxy._max_mcp_response_bytes() == 4096


def test_web_fetch_signature_matches_orchestrator():
    pytest.importorskip("orchestrator.mcp_server")
    from orchestrator import mcp_server

    assert inspect.signature(mcp_proxy.web_fetch) == inspect.signature(mcp_server.web_fetch)


@respx.mock
@pytest.mark.parametrize(
    ("tool", "path", "schema_version"),
    [
        (mcp_proxy.web_map, "/v1/map", "thorondor.map.v1"),
        (mcp_proxy.web_crawl, "/v1/crawl", "thorondor.crawl.v1"),
    ],
)
def test_site_tools_forward_non_none_policy(monkeypatch, tool, path, schema_version):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    route = respx.post(f"http://thorondor.test{path}").mock(
        return_value=httpx.Response(
            200,
            json={
                "requested_url": "https://a.test/article",
                "outcome": "completed",
                "urls": [],
                "results": [],
                "stats": {},
                "warnings": [],
                "schema_version": schema_version,
            },
        )
    )

    result = asyncio.run(
        tool(
            "https://a.test/article",
            sitemap="skip",
            max_pages=2,
            include_subdomains=None,
        )
    )

    assert result["schema_version"] == schema_version
    assert route.calls.last.request.read() == (
        b'{"url":"https://a.test/article","sitemap":"skip","max_pages":2}'
    )


def test_site_tool_signatures_match_orchestrator():
    pytest.importorskip("orchestrator.mcp_server")
    from orchestrator import mcp_server

    assert inspect.signature(mcp_proxy.web_map) == inspect.signature(mcp_server.web_map)
    assert inspect.signature(mcp_proxy.web_crawl) == inspect.signature(mcp_server.web_crawl)


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


@respx.mock
def test_proxy_identity_annotations_and_structured_result(monkeypatch):
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

    async def exercise():
        return await mcp_proxy.mcp.call_tool("web_search", {"query": "x"})

    result = asyncio.run(exercise())
    tools = {tool.name: tool for tool in asyncio.run(mcp_proxy.mcp.list_tools())}
    annotations = {
        name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
        for name, tool in tools.items()
    }

    assert route.called
    assert mcp_proxy.mcp.version == THORONDOR_VERSION
    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    assert json.loads(result.content[0].text) == result.structured_content
    assert annotations["web_search"] == {"readOnlyHint": True, "openWorldHint": True}
    assert annotations["web_fetch"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }


def test_proxy_tool_descriptors_match_orchestrator():
    from orchestrator import mcp_server

    async def descriptors(server):
        return {
            tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in await server.list_tools()
        }

    assert asyncio.run(descriptors(mcp_proxy.mcp)) == asyncio.run(descriptors(mcp_server.mcp))


@respx.mock
def test_proxy_preserves_dependency_failure_as_structured_tool_error(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    respx.post("http://thorondor.test/v1/search").mock(
        return_value=httpx.Response(
            503,
            json={
                "detail": {
                    "dependency": "searxng",
                    "reason": "searxng_unavailable",
                }
            },
        )
    )

    result = asyncio.run(mcp_proxy.mcp.call_tool("web_search", {"query": "x"}))

    assert result.is_error is True
    assert result.structured_content == {
        "error": "searxng_unavailable",
        "status_code": 503,
        "dependency": "searxng",
    }


@respx.mock
def test_proxy_validation_and_request_limit_errors_match_orchestrator(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    validation = respx.post("http://thorondor.test/v1/search").mock(
        return_value=httpx.Response(422, json={"detail": [{"loc": ["body"]}]})
    )
    invalid = asyncio.run(
        mcp_proxy.mcp.call_tool(
            "web_search",
            {"query": "x", "max_urls": 999},
        )
    )
    assert validation.called
    assert invalid.structured_content == {"error": "invalid_request", "status_code": 422}

    request_limit = respx.post("http://thorondor.test/v1/fetch").mock(
        return_value=httpx.Response(
            413,
            json={
                "detail": {
                    "reason": "request_body_too_large",
                    "max_bytes": 64,
                }
            }
        )
    )
    oversized = asyncio.run(
        mcp_proxy.mcp.call_tool(
            "web_fetch",
            {"urls": ["https://a.test/" + "x" * 100]},
        )
    )
    assert request_limit.called
    assert oversized.structured_content == {
        "error": "request_body_too_large",
        "status_code": 413,
        "max_bytes": 64,
    }


@respx.mock
def test_proxy_sends_non_ascii_request_without_ascii_size_inflation(monkeypatch):
    monkeypatch.setenv("THORONDOR_BASE_URL", "http://thorondor.test")
    route = respx.post("http://thorondor.test/v1/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [],
                "stats": {},
                "schema_version": "thorondor.fetch.v1",
            },
        )
    )

    result = asyncio.run(
        mcp_proxy.mcp.call_tool(
            "web_fetch",
            {"urls": ["https://a.test/" + "é" * 16]},
        )
    )

    assert route.called
    assert result.is_error is False
    assert result.structured_content["schema_version"] == "thorondor.fetch.v1"


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
