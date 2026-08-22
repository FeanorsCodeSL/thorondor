import asyncio
import hashlib
import json
import sys
import tomllib
from pathlib import Path

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult
from pydantic import BaseModel
from thorondor_cli import __version__ as cli_version
from thorondor_contracts import THORONDOR_VERSION
from thorondor_mcp import (
    MCPRequestValidationError,
    bounded_call_tool_result,
    minimum_mcp_response_bytes,
    wrap_mcp_tool,
)

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


def test_mcp_identity_annotations_and_version_are_shared():
    async def advertised():
        return {tool.name: tool for tool in await mcpmod.mcp.list_tools()}

    tools = anyio.run(advertised)
    annotations = {
        name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
        for name, tool in tools.items()
    }

    assert mcpmod.mcp.version == THORONDOR_VERSION
    assert cli_version == THORONDOR_VERSION
    assert annotations == {
        "web_search": {"readOnlyHint": True, "openWorldHint": True},
        "web_fetch": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "web_map": {"readOnlyHint": True, "openWorldHint": True},
        "web_crawl": {"readOnlyHint": True, "openWorldHint": True},
    }


def test_shared_version_matches_project_metadata():
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == THORONDOR_VERSION


def test_orchestrator_runtime_includes_shared_mcp_module():
    dockerfile = Path("orchestrator/Dockerfile").read_text(encoding="utf-8")
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "thorondor_mcp.py ./thorondor_mcp.py" in dockerfile
    assert "thorondor_mcp" in metadata["tool"]["setuptools"]["py-modules"]


def test_search_dependency_failure_is_a_structured_tool_error():
    mcpmod.set_deps(fakes.deps(discovery=fakes.DownDiscovery()))

    result = anyio.run(mcpmod.mcp.call_tool, "web_search", {"query": "x"})

    assert result.is_error is True
    assert result.structured_content == {
        "error": "searxng_unavailable",
        "status_code": 503,
        "dependency": "searxng",
    }


def test_invalid_tool_bounds_are_structured_validation_errors():
    mcpmod.set_deps(fakes.deps())

    result = anyio.run(
        mcpmod.mcp.call_tool,
        "web_search",
        {"query": "x", "max_urls": 999},
    )

    assert result.is_error is True
    assert result.structured_content == {
        "error": "invalid_request",
        "status_code": 422,
    }


def test_non_ascii_request_size_matches_utf8_transport_bytes():
    deps = fakes.deps(resource_policy=ResourcePolicy(max_request_body_bytes=64))
    mcpmod.set_deps(deps)

    result = anyio.run(
        mcpmod.mcp.call_tool,
        "web_fetch",
        {"urls": ["https://a.test/" + "é" * 16]},
    )

    assert result.is_error is False
    assert result.structured_content["schema_version"] == "thorondor.fetch.v1"


def test_tiny_mcp_response_limit_is_rejected_before_tool_execution():
    mcpmod.set_deps(
        fakes.deps(
            resource_policy=ResourcePolicy(
                max_response_body_bytes=128,
                max_content_bytes=128,
            )
        )
    )

    with pytest.raises(MCPError) as exc_info:
        anyio.run(mcpmod.mcp.call_tool, "web_search", {"query": "x"})

    assert minimum_mcp_response_bytes() > 128
    assert exc_info.value.code == -32603
    assert "minimum identity-bearing result size" in exc_info.value.message


def test_tiny_mcp_response_limit_is_checked_before_tool_execution():
    calls = 0

    async def tool():
        nonlocal calls
        calls += 1
        return {"result": "unused"}

    wrapped = wrap_mcp_tool(tool, lambda: minimum_mcp_response_bytes() - 1)

    with pytest.raises(MCPError):
        anyio.run(wrapped)

    assert calls == 0


def test_mcp_call_returns_structured_rest_body_and_identity():
    mcpmod.set_deps(fakes.deps())

    result = anyio.run(mcpmod.mcp.call_tool, "web_search", {"query": "x"})

    assert isinstance(result, CallToolResult)
    assert result.structured_content["schema_version"] == "thorondor.search.v1"
    assert result.is_error is False
    assert result.result_type == "complete"
    assert result.meta["io.modelcontextprotocol/serverInfo"] == {
        "name": "thorondor",
        "version": THORONDOR_VERSION,
    }
    assert json.loads(result.content[0].text) == result.structured_content


def test_controlled_mcp_failure_is_a_tool_error_with_structured_body():
    deps = fakes.deps(
        resource_policy=ResourcePolicy(max_inflight_fetches=1, admission_wait_s=0)
    )
    mcpmod.set_deps(deps)

    async def exercise():
        async with deps.admission.fetch_slot():
            return await mcpmod.mcp.call_tool(
                "web_fetch", {"urls": ["https://a.test/article"]}
            )

    result = anyio.run(exercise)

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert result.structured_content == {
        "error": "capacity_unavailable",
        "status_code": 429,
        "route": "fetch",
        "retry_after_s": 1,
    }


def test_unknown_mcp_tool_is_a_top_level_invalid_params_error():
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
                try:
                    await session.call_tool("missing_tool", {})
                except MCPError as exc:
                    return exc

    error = anyio.run(exercise)

    assert isinstance(error, MCPError)
    assert error.code == -32602
    assert error.message == "Unknown tool"


def test_mcp_result_limit_covers_complete_result_object():
    accepted = bounded_call_tool_result({"payload": "x" * 100}, 4096)
    oversized = bounded_call_tool_result({"payload": "x" * 10000}, 4096)

    assert len(accepted.model_dump_json(by_alias=True, exclude_none=True).encode()) <= 4096
    assert accepted.structured_content == {"payload": "x" * 100}
    assert oversized.is_error is True
    assert oversized.structured_content == {
        "error": "response_body_too_large",
        "status_code": 500,
        "max_bytes": 4096,
    }
    assert len(oversized.model_dump_json(by_alias=True, exclude_none=True).encode()) <= 4096


def test_mcp_result_limit_counts_transport_ascii_escaping_for_unicode():
    limit = 4096
    last_accepted = None
    first_rejected = None
    for length in range(1, 3000):
        result = bounded_call_tool_result({"payload": "é" * length}, limit)
        if result.structured_content == {"payload": "é" * length}:
            last_accepted = result
        else:
            first_rejected = result
            break

    assert last_accepted is not None
    assert first_rejected is not None
    serialized = json.dumps(
        last_accepted.model_dump(mode="json", by_alias=True, exclude_none=True),
        separators=(",", ":"),
    )
    assert len(serialized.encode("utf-8")) <= limit
    assert first_rejected.structured_content["error"] == "response_body_too_large"


def test_unexpected_tool_defect_is_sanitized_as_internal_protocol_error():
    async def defective_tool():
        raise RuntimeError("private failure detail")

    wrapped = wrap_mcp_tool(defective_tool, lambda: 4096)

    with pytest.raises(MCPError) as exc_info:
        anyio.run(wrapped)

    assert exc_info.value.code == -32603
    assert exc_info.value.message == "Internal error"
    assert "private failure detail" not in str(exc_info.value)


def test_internal_pydantic_defect_is_not_misclassified_as_invalid_request():
    class InternalModel(BaseModel):
        value: int

    async def defective_tool():
        InternalModel(value="not-an-integer")
        return {"result": "unreachable"}

    wrapped = wrap_mcp_tool(defective_tool, lambda: 4096)

    with pytest.raises(MCPError) as exc_info:
        anyio.run(wrapped)

    assert exc_info.value.code == -32603
    assert exc_info.value.message == "Internal error"


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

    with pytest.raises(MCPRequestValidationError):
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
                        assert tools.ttl_ms == 0
                        assert tools.cache_scope == "private"
                        result = await modern_client.call_tool("web_search", {"query": "x"})
                        assert result.content
                        assert result.structured_content["schema_version"] == "thorondor.search.v1"
                        assert result.meta["io.modelcontextprotocol/serverInfo"] == {
                            "name": "thorondor",
                            "version": THORONDOR_VERSION,
                        }
                        assert result.result_type == "complete"

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
