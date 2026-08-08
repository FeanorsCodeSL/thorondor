import asyncio
import hashlib
import json
import sys
from pathlib import Path

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

import orchestrator.app as appmod
import orchestrator.mcp_server as mcpmod
from orchestrator import fakes
from orchestrator.app import app
from orchestrator.page_cache import DisabledPageCache
from orchestrator.resource_policy import ResourcePolicy


def test_web_search_advertises_concise_descriptor_with_stable_input_schema():
    async def advertised_tool():
        return next(tool for tool in await mcpmod.mcp.list_tools() if tool.name == "web_search")

    tool = anyio.run(advertised_tool)
    description = tool.description
    schema_digest = hashlib.sha256(
        json.dumps(tool.input_schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert len(description) <= 700
    assert (
        "Search the live web and return source-cited evidence passages. Use for current or "
        "external information that requires verification."
    ) in description
    assert "POST /v1/search" not in description
    assert "SearchResponse" not in description
    assert schema_digest == "3b9ac3c785a893a4c5529964926475bc35f01ed5b24555efac18ba038780293b"


def test_web_search_returns_documented_shape():
    mcpmod.set_deps(fakes.deps())

    out = anyio.run(mcpmod.web_search, "x")

    assert set(out) == {"query", "passages", "citations", "stats", "raw_markdown", "schema_version"}
    assert out["schema_version"] == "thorondor.search.v1"
    assert out["passages"]
    assert out["citations"]
    assert out["passages"][0]["verbatim"] is True
    assert out["passages"][0]["evidence_id"]
    assert out["citations"][0]["evidence_spans"]


def test_web_fetch_returns_typed_outcomes():
    mcpmod.set_deps(fakes.deps())

    out = anyio.run(mcpmod.web_fetch, ["https://a.test/article"])

    assert out["schema_version"] == "thorondor.fetch.v1"
    assert out["results"][0]["outcome"] == "content"
    assert out["results"][0]["trust"] == "untrusted"


def test_stdio_runtime_starts_and_closes_page_cache(monkeypatch):
    events = []

    class TrackingPageCache(DisabledPageCache):
        async def start(self):
            events.append("start")

        async def aclose(self):
            events.append("close")

    runtime_deps = fakes.deps(page_cache=TrackingPageCache())
    mcpmod.set_deps(runtime_deps)

    async def run_stdio_async():
        events.append("run")

    monkeypatch.setattr(mcpmod.mcp, "run_stdio_async", run_stdio_async)

    anyio.run(mcpmod._run_stdio)

    assert events == ["start", "run", "close"]


def test_in_process_mcp_enforces_request_and_response_byte_limits():
    request_policy = ResourcePolicy(
        max_request_body_bytes=64,
    )
    mcpmod.set_deps(fakes.deps(resource_policy=request_policy))

    request_error = anyio.run(
        mcpmod.web_fetch,
        ["https://a.test/" + ("x" * 100)],
    )

    assert request_error == {
        "error": "request_body_too_large",
        "status_code": 413,
        "max_bytes": 64,
    }

    response_policy = ResourcePolicy(
        max_response_body_bytes=128,
        max_content_bytes=128,
    )
    mcpmod.set_deps(fakes.deps(resource_policy=response_policy))

    response_error = anyio.run(mcpmod.web_search, "x")

    assert response_error == {
        "error": "response_body_too_large",
        "status_code": 500,
    }


def test_stdio_mcp_serializes_exact_evidence_spans():
    server_code = (
        "from orchestrator import fakes; "
        "from orchestrator import mcp_server; "
        "mcp_server.set_deps(fakes.deps()); "
        "mcp_server.main()"
    )

    async def exercise():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-c", server_code],
            cwd=Path(__file__).parents[2],
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("web_search", {"query": "x"})
                if result.structured_content is not None:
                    return result.structured_content
                return json.loads(result.content[0].text)

    output = anyio.run(exercise)

    passage = output["passages"][0]
    citation = output["citations"][0]
    assert passage["verbatim"] is True
    assert passage["document_id"] == citation["document_id"]
    assert passage["evidence_id"] == citation["evidence_spans"][0]["evidence_id"]


def test_stdio_mcp_serializes_fetch_outcomes():
    server_code = (
        "from orchestrator import fakes; "
        "from orchestrator import mcp_server; "
        "mcp_server.set_deps(fakes.deps()); "
        "mcp_server.main()"
    )

    async def exercise():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-c", server_code],
            cwd=Path(__file__).parents[2],
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "web_fetch",
                    {"urls": ["https://a.test/article"]},
                )
                if result.structured_content is not None:
                    return result.structured_content
                return json.loads(result.content[0].text)

    output = anyio.run(exercise)

    assert output["schema_version"] == "thorondor.fetch.v1"
    assert output["results"][0]["outcome"] == "content"


def test_web_search_rejects_invalid_bounds_before_fanout():
    mcpmod.set_deps(fakes.deps(discovery=fakes.DownDiscovery()))

    with pytest.raises(ValidationError):
        anyio.run(lambda: mcpmod.web_search("x", max_urls=999))


def test_app_exposes_mcp_mount():
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)


def test_streamable_http_initialize_tools_list_and_call_at_public_mcp(monkeypatch):
    async def exercise():
        deps = fakes.deps()
        monkeypatch.setattr(appmod, "deps", deps)
        mcpmod.set_deps(deps)

        async with app.router.lifespan_context(app):
            for base_url in ["http://localhost:8000", "http://thorondor:8080"]:
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url=base_url,
                ) as client:
                    assert (await client.get("/mcp/mcp")).status_code == 404
                    async with streamable_http_client(
                        f"{base_url}/mcp",
                        http_client=client,
                    ) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            assert [tool.name for tool in tools.tools] == [
                                "web_search",
                                "web_fetch",
                                "web_map",
                                "web_crawl",
                            ]
                            result = await session.call_tool("web_search", {"query": "x"})
                            assert result.content

                    async with Client(
                        streamable_http_client(
                            f"{base_url}/mcp",
                            http_client=client,
                        )
                    ) as modern_client:
                        assert modern_client.protocol_version == "2026-07-28"
                        tools = await modern_client.list_tools()
                        assert [tool.name for tool in tools.tools] == [
                            "web_search",
                            "web_fetch",
                            "web_map",
                            "web_crawl",
                        ]
                        result = await modern_client.call_tool("web_search", {"query": "x"})
                        assert result.content

            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://evil.test:8080",
            ) as client:
                assert (await client.post("/mcp", json={})).status_code == 421

    anyio.run(exercise)


@pytest.mark.parametrize(
    ("name", "request_json", "deps"),
    [
        ("default", {"query": "x"}, fakes.deps()),
        (
            "custom budget/urls",
            {"query": "x", "token_budget": 8, "max_urls": 1},
            fakes.deps(),
        ),
        (
            "search profile",
            {"query": "x", "search_profile": "research"},
            fakes.deps(),
        ),
        (
            "include raw markdown",
            {"query": "x", "include_raw_markdown": True},
            fakes.deps(),
        ),
        (
            "degraded discovery reason",
            {"query": "x"},
            fakes.deps(discovery=fakes.EmptyDiscovery()),
        ),
        (
            "partial search provider degradation",
            {"query": "x"},
            fakes.deps(discovery=fakes.DegradedDiscovery()),
        ),
        (
            "search provider unavailable",
            {"query": "x"},
            fakes.deps(discovery=fakes.UnavailableDiscovery()),
        ),
        (
            "degraded crawl reason",
            {"query": "x"},
            fakes.deps(extractor=fakes.EmptyExtractor()),
        ),
        (
            "degraded chunk reason",
            {"query": "x"},
            fakes.deps(chunker=fakes.EmptyChunker()),
        ),
        (
            "reranked false",
            {"query": "x"},
            fakes.deps(reranker=fakes.DownReranker()),
        ),
        (
            "settings override",
            {"query": "x"},
            fakes.deps(default_token_budget=12, default_max_urls=1),
        ),
    ],
)
def test_rest_mcp_parity_over_fake_dependency_cases(monkeypatch, name, request_json, deps):
    monkeypatch.setattr(appmod, "deps", deps)
    mcpmod.set_deps(deps)

    rest = TestClient(appmod.app).post("/search", json=request_json).json()
    mcp = anyio.run(lambda: mcpmod.web_search(**request_json))

    rest["stats"]["elapsed_ms"] = 0
    mcp["stats"]["elapsed_ms"] = 0
    assert mcp == rest, name


def test_fetch_rest_and_in_process_mcp_are_equivalent(monkeypatch):
    deps = fakes.deps()
    monkeypatch.setattr(appmod, "deps", deps)
    mcpmod.set_deps(deps)
    request_json = {"urls": ["https://a.test/article"]}

    rest = TestClient(appmod.app).post("/v1/fetch", json=request_json).json()
    direct = anyio.run(lambda: mcpmod.web_fetch(**request_json))

    rest["stats"]["elapsed_ms"] = 0
    direct["stats"]["elapsed_ms"] = 0
    assert direct == rest


@pytest.mark.parametrize(
    ("path", "tool", "schema_version"),
    [
        ("/v1/map", mcpmod.web_map, "thorondor.map.v1"),
        ("/v1/crawl", mcpmod.web_crawl, "thorondor.crawl.v1"),
    ],
)
def test_site_rest_and_in_process_mcp_are_equivalent(
    monkeypatch,
    path,
    tool,
    schema_version,
):
    rest_deps = fakes.deps()
    monkeypatch.setattr(appmod, "deps", rest_deps)
    request_json = {
        "url": "https://a.test/article",
        "sitemap": "skip",
        "max_pages": 1,
    }

    rest = TestClient(appmod.app).post(path, json=request_json).json()
    mcpmod.set_deps(fakes.deps())
    direct = anyio.run(lambda: tool(**request_json))

    rest["stats"]["elapsed_ms"] = 0
    direct["stats"]["elapsed_ms"] = 0
    assert direct == rest
    assert direct["schema_version"] == schema_version


def test_map_and_crawl_advertise_concise_agent_oriented_descriptors():
    async def advertised():
        return {tool.name: tool for tool in await mcpmod.mcp.list_tools()}

    tools = anyio.run(advertised)

    assert "robots-aware URL map" in tools["web_map"].description
    assert "typed page evidence" in tools["web_crawl"].description
    assert len(tools["web_map"].description) <= 300
    assert len(tools["web_crawl"].description) <= 300


def test_web_fetch_advertises_typed_target_watch_schema():
    async def advertised():
        return next(tool for tool in await mcpmod.mcp.list_tools() if tool.name == "web_fetch")

    tool = anyio.run(advertised)
    watch_schema = tool.input_schema["properties"]["watch"]

    assert watch_schema["anyOf"][0]["$ref"] == "#/$defs/TargetWatch"
    assert set(tool.input_schema["$defs"]["TargetWatch"]["properties"]) == {
        "target",
        "desired",
        "expected",
        "match",
    }


def test_fetch_and_crawl_mcp_advertise_closed_structured_formats():
    async def advertised():
        return {tool.name: tool for tool in await mcpmod.mcp.list_tools()}

    tools = anyio.run(advertised)

    for name in ("web_fetch", "web_crawl"):
        schema = tools[name].input_schema["properties"]["structured_formats"]
        item_schema = schema["anyOf"][0]["items"]
        assert item_schema["enum"] == ["links", "tables", "json_ld", "json_schema"]
        extraction_schema = tools[name].input_schema["properties"]["extraction_schema"]
        assert extraction_schema["anyOf"][0]["type"] == "object"


def test_fetch_mcp_returns_closed_capacity_error():
    deps = fakes.deps(
        resource_policy=appmod.ResourcePolicy(
            max_inflight_fetches=1,
            admission_wait_s=0,
        )
    )
    mcpmod.set_deps(deps)

    async def exercise():
        async with deps.admission.fetch_slot():
            return await mcpmod.web_fetch(["https://a.test/article"])

    out = anyio.run(exercise)

    assert out == {
        "error": "capacity_unavailable",
        "status_code": 429,
        "route": "fetch",
        "retry_after_s": 1,
    }


def test_map_mcp_returns_closed_capacity_error():
    deps = fakes.deps(
        resource_policy=appmod.ResourcePolicy(
            max_inflight_maps=1,
            admission_wait_s=0,
        )
    )
    mcpmod.set_deps(deps)

    async def exercise():
        async with deps.admission.map_slot():
            return await mcpmod.web_map(
                "https://a.test/article",
                sitemap="skip",
                max_pages=1,
            )

    out = anyio.run(exercise)

    assert out == {
        "error": "capacity_unavailable",
        "status_code": 429,
        "route": "map",
        "retry_after_s": 1,
    }


def test_in_process_mcp_caller_cancellation_stops_fetch_and_releases_slot():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingExtractor:
        supported_capabilities = frozenset(
            {"markdown", "javascript", "links", "metadata"}
        )

        async def fetch(self, _urls, _capabilities, _include_raw_html):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(extractor=BlockingExtractor())
    mcpmod.set_deps(deps)

    async def exercise():
        task = asyncio.create_task(mcpmod.web_fetch(["https://a.test/article"]))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert deps.admission.active_fetches == 0

    anyio.run(exercise)


def test_in_process_mcp_caller_cancellation_stops_crawl_and_releases_slot():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingExtractor:
        supported_capabilities = frozenset(
            {"markdown", "javascript", "links", "metadata", "raw_html"}
        )

        async def fetch(self, _urls, _capabilities, _include_raw_html):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(extractor=BlockingExtractor())
    mcpmod.set_deps(deps)

    async def exercise():
        task = asyncio.create_task(
            mcpmod.web_crawl(
                "https://a.test/article",
                sitemap="skip",
                max_pages=1,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert deps.admission.active_crawls == 0

    anyio.run(exercise)
