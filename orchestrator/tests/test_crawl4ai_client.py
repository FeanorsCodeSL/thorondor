import anyio
import httpx
import json
import time

from orchestrator.clients.crawl4ai_client import Crawl4aiExtractor


def test_partial_failures_do_not_sink_batch(monkeypatch):
    def handler(req):
        payload = json.loads(req.read().decode())
        if "bad" in payload["urls"][0]:
            return httpx.Response(500, json={})
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": "Good"},
                        "markdown": {"fit_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        Crawl4aiExtractor("http://crawl4ai:11235", 2, 1, validate_redirects=False).extract,
        ["https://good", "https://bad"],
    )

    assert len(out) == 1
    assert out[0].url == "https://good"


def test_uses_crawl4ai_docker_api_payload(monkeypatch):
    seen = []

    def handler(req):
        seen.append({"authorization": req.headers.get("authorization")})
        payload = json.loads(req.read().decode())
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": "Good"},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        Crawl4aiExtractor(
            "http://crawl4ai:11235",
            1,
            1,
            respect_robots_txt=True,
            api_key="crawl-secret",
            validate_redirects=False,
        ).extract,
        ["https://good"],
    )

    assert out and out[0].markdown == "content"
    assert seen == [
        {"authorization": "Bearer crawl-secret"},
        {
            "urls": ["https://good"],
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"stream": False, "cache_mode": "bypass", "check_robots_txt": True},
            },
        }
    ]


def test_can_disable_crawl4ai_robots_flag(monkeypatch):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://good",
                        "metadata": {"title": "Good"},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(
        Crawl4aiExtractor(
            "http://crawl4ai:11235",
            1,
            1,
            respect_robots_txt=False,
            validate_redirects=False,
        ).extract,
        ["https://good"],
    )

    assert seen[0]["crawler_config"]["params"]["check_robots_txt"] is False


def test_unsafe_redirected_url_is_dropped(monkeypatch):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://safe.example/start",
                        "redirected_url": "http://127.0.0.1/admin",
                        "markdown": {"raw_markdown": "internal secret"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        Crawl4aiExtractor("http://crawl4ai:11235", 1, 1, validate_redirects=False).extract,
        ["https://safe.example/start"],
    )

    assert out == []


def test_http_redirect_to_internal_target_drops_before_crawl4ai_post(monkeypatch):
    posted = False

    def handler(req):
        nonlocal posted
        if req.method == "HEAD":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        posted = True
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        Crawl4aiExtractor("http://crawl4ai:11235", 1, 1).extract,
        ["https://safe.example/start"],
    )

    assert out == []
    assert posted is False


def test_extract_returns_completed_pages_within_overall_deadline(monkeypatch):
    async def handler(req):
        payload = json.loads(req.content)
        url = payload["urls"][0]
        if "slow" in url:
            await anyio.sleep(1)
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": url,
                        "metadata": {"title": url},
                        "markdown": {"raw_markdown": f"content {url}"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    started = time.perf_counter()
    out = anyio.run(
        Crawl4aiExtractor("http://crawl4ai:11235", 2, 0.05, validate_redirects=False).extract,
        ["https://fast.example", "https://slow.example"],
    )

    assert time.perf_counter() - started < 0.5
    assert [page.url for page in out] == ["https://fast.example"]


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

    anyio.run(
        Crawl4aiExtractor("http://crawl4ai:11235", 1, 1, validate_redirects=False).extract,
        ["u1", "u2", "u3"],
    )

    assert peak <= 1


def test_per_host_concurrency_is_bounded(monkeypatch):
    active_by_host = {}
    peak_by_host = {}

    async def handler(req):
        if req.method == "HEAD":
            return httpx.Response(200)
        payload = json.loads(req.content)
        host = payload["urls"][0].split("/")[2]
        active_by_host[host] = active_by_host.get(host, 0) + 1
        peak_by_host[host] = max(peak_by_host.get(host, 0), active_by_host[host])
        await anyio.sleep(0.01)
        active_by_host[host] -= 1
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": host},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(
        Crawl4aiExtractor(
            "http://crawl4ai:11235",
            concurrency=3,
            timeout_s=1,
            per_host_concurrency=1,
        ).extract,
        ["https://a.test/1", "https://a.test/2", "https://b.test/1"],
    )

    assert peak_by_host["a.test"] == 1
