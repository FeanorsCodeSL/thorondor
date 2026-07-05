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
