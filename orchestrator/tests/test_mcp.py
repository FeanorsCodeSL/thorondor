import anyio

import orchestrator.mcp_server as mcpmod
from orchestrator import fakes
from orchestrator.app import app


def test_web_search_returns_documented_shape():
    mcpmod.set_deps(fakes.deps())

    out = anyio.run(mcpmod.web_search, "x")

    assert set(out) == {"passages", "citations"}
    assert out["passages"]
    assert out["citations"]


def test_app_exposes_mcp_mount():
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)
