import anyio
import httpx

from orchestrator.clients.planner import IdentityPlanner, LlmPlanner


def test_identity_planner_returns_query():
    assert anyio.run(IdentityPlanner().plan, "q") == ["q"]


def test_llm_planner_falls_back_on_error(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    assert anyio.run(LlmPlanner("http://llm:80", "m").plan, "q") == ["q"]
