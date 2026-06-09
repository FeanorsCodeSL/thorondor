import anyio
import pytest
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import ValidationError

import orchestrator.app as appmod
import orchestrator.mcp_server as mcpmod
from orchestrator import fakes
from orchestrator.app import app


def test_web_search_returns_documented_shape():
    mcpmod.set_deps(fakes.deps())

    out = anyio.run(mcpmod.web_search, "x")

    assert set(out) == {"query", "passages", "citations", "stats", "raw_markdown", "schema_version"}
    assert out["schema_version"] == "thorondor.search.v1"
    assert out["passages"]
    assert out["citations"]


def test_web_search_rejects_invalid_bounds_before_fanout():
    mcpmod.set_deps(fakes.deps(discovery=fakes.DownDiscovery()))

    with pytest.raises(ValidationError):
        anyio.run(mcpmod.web_search, "x", None, 999)


def test_app_exposes_mcp_mount():
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)


def test_streamable_http_initialize_tools_list_and_call_at_public_mcp():
    async def exercise():
        mcpmod.set_deps(fakes.deps())

        def client_factory(headers=None, timeout=None, auth=None):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers=headers,
                timeout=timeout,
                auth=auth,
            )

        async with app.router.lifespan_context(app):
            async with client_factory() as client:
                assert (await client.get("/mcp/mcp")).status_code == 404

            async with streamablehttp_client(
                "http://localhost:8000/mcp",
                httpx_client_factory=client_factory,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == ["web_search"]
                    result = await session.call_tool("web_search", {"query": "x"})
                    assert result.content

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

    assert mcp == rest, name
