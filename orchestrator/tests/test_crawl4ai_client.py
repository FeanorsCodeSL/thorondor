import anyio
import httpx

from orchestrator.clients.crawl4ai_client import Crawl4aiExtractor


def test_partial_failures_do_not_sink_batch(monkeypatch):
    def handler(req):
        payload = req.read().decode()
        if "bad" in payload:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"title": "Good", "markdown": "content"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(Crawl4aiExtractor("http://crawl4ai:11235", 2, 1).extract, ["https://good", "https://bad"])

    assert len(out) == 1
    assert out[0].url == "https://good"


def test_concurrency_is_bounded(monkeypatch):
    active = 0
    peak = 0

    def handler(req):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        active -= 1
        return httpx.Response(200, json={"title": "T", "markdown": "content"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(Crawl4aiExtractor("http://crawl4ai:11235", 1, 1).extract, ["u1", "u2", "u3"])

    assert peak <= 1
