import anyio
import httpx

from orchestrator.clients.planner import IdentityPlanner, LlmPlanner
from orchestrator.observability import reset_request_id, set_request_id


def test_identity_planner_returns_query():
    assert anyio.run(IdentityPlanner().plan, "q") == ["q"]


def test_llm_planner_falls_back_on_error(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    assert anyio.run(LlmPlanner("http://llm:80", "m").plan, "q") == ["q"]


def test_llm_planner_sends_api_key(monkeypatch):
    seen = {}

    def handler(req):
        seen["authorization"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "[\"q\"]"}}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    assert anyio.run(LlmPlanner("http://llm:80", "m", api_key="secret").plan, "q") == ["q"]
    assert seen["authorization"] == "Bearer secret"


def test_llm_planner_forwards_request_id(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"choices": [{"message": {"content": "[\"q\"]"}}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-plan")
    try:
        assert anyio.run(LlmPlanner("http://llm:80", "m").plan, "q") == ["q"]
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-plan"
