import asyncio
import ipaddress
import threading

import anyio

from orchestrator.types import DiscoveryResult
import orchestrator.url_safety as url_safety


def _policy() -> url_safety.UrlSafetyPolicy:
    return url_safety.UrlSafetyPolicy(
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


def test_async_dns_safety_does_not_block_the_event_loop(monkeypatch):
    resolver_started = threading.Event()
    resolver_release = threading.Event()
    released_by_event_loop = []

    def slow_resolver(_host):
        resolver_started.set()
        released_by_event_loop.append(resolver_release.wait(timeout=1))
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(url_safety, "resolve_host_ips", slow_resolver)

    async def exercise():
        task = asyncio.create_task(url_safety.is_safe_crawl_url_async("https://slow.example", _policy()))
        assert await asyncio.wait_for(asyncio.to_thread(resolver_started.wait), timeout=1)
        resolver_release.set()
        return await task

    is_safe = anyio.run(exercise)

    assert is_safe is True
    assert released_by_event_loop == [True]


def test_async_discovery_filter_bounds_dns_workers(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()
    two_workers_started = threading.Event()

    def slow_resolver(_host):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_workers_started.set()
        try:
            two_workers_started.wait(timeout=1)
            return [ipaddress.ip_address("93.184.216.34")]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(url_safety, "resolve_host_ips", slow_resolver)
    results = [
        DiscoveryResult(f"Title {index}", f"https://{index}.example", "snippet", "test", 1.0)
        for index in range(8)
    ]

    filtered = anyio.run(
        url_safety.filter_safe_discovery_results_async,
        results,
        _policy(),
        2,
    )

    assert filtered == results
    assert peak == 2


def test_async_url_safety_exceptions_fail_closed(monkeypatch):
    def failing_resolver(_host):
        raise RuntimeError("resolver failure")

    monkeypatch.setattr(url_safety, "resolve_host_ips", failing_resolver)
    result = DiscoveryResult("Title", "https://failure.example", "snippet", "test", 1.0)

    assert url_safety.is_safe_crawl_url(result.url, _policy()) is False
    assert anyio.run(url_safety.is_safe_crawl_url_async, result.url, _policy()) is False
    assert anyio.run(url_safety.filter_safe_discovery_results_async, [result], _policy(), 1) == []
