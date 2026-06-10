import anyio
import httpx
import ipaddress
import json
import time

from orchestrator.clients.crawl4ai_client import Crawl4aiExtractor
from orchestrator.observability import reset_request_id, set_request_id
from orchestrator.url_safety import UrlSafetyPolicy, is_safe_crawl_url


def _extractor(**kwargs) -> Crawl4aiExtractor:
    policy = UrlSafetyPolicy(
        blocked_ip_categories={
            "loopback",
            "link_local",
            "private",
            "reserved",
            "multicast",
            "unspecified",
        },
        blocked_special_ips={
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("fd00:ec2::254"),
        },
        nat64_networks=[ipaddress.ip_network("64:ff9b::/96")],
        six_to_four_networks=[ipaddress.ip_network("2002::/16")],
        ipv4_compat_networks=[ipaddress.ip_network("::/96")],
    )
    values = {
        "base_url": "http://crawl4ai:11235",
        "concurrency": 1,
        "timeout_s": 1,
        "respect_robots_txt": True,
        "per_host_concurrency": 1,
        "validate_redirects": False,
        "max_preflight_redirects": 5,
        "url_safety": lambda url: is_safe_crawl_url(url, policy),
    }
    values.update(kwargs)
    return Crawl4aiExtractor(**values)


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
        _extractor(concurrency=2).extract,
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
                        "cleaned_html": "<article>content</article>",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        _extractor(api_key="crawl-secret").extract,
        ["https://good"],
    )

    assert out and out[0].markdown == "content"
    assert out[0].html == "<article>content</article>"
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


def test_request_id_is_forwarded_to_crawl4ai(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        payload = json.loads(req.content)
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

    token = set_request_id("req-crawl")
    try:
        anyio.run(
            _extractor().extract,
            ["https://good"],
        )
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-crawl"


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
        _extractor(respect_robots_txt=False).extract,
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
        _extractor().extract,
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
        _extractor(validate_redirects=True).extract,
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
        _extractor(concurrency=2, timeout_s=0.05).extract,
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
        _extractor().extract,
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
        _extractor(concurrency=3, validate_redirects=True).extract,
        ["https://a.test/1", "https://a.test/2", "https://b.test/1"],
    )

    assert peak_by_host["a.test"] == 1
