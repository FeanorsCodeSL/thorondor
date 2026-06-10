import asyncio
import importlib.util
import ipaddress
from pathlib import Path
import sys

import pytest


def _load_proxy_module():
    path = Path(__file__).resolve().parents[2] / "ssrf-proxy" / "proxy.py"
    spec = importlib.util.spec_from_file_location("ssrf_proxy_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _settings(proxy):
    return proxy.ProxySettings(
        log_level="INFO",
        listen_host="0.0.0.0",
        listen_port=8888,
        max_header_bytes=65536,
        read_chunk_bytes=4096,
        relay_chunk_bytes=65536,
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


def test_proxy_rejects_private_and_metadata_ip_literals():
    proxy = _load_proxy_module()
    settings = _settings(proxy)

    assert asyncio.run(proxy.safe_connect_ip("127.0.0.1", 80, settings)) is None
    assert asyncio.run(proxy.safe_connect_ip("10.1.2.3", 80, settings)) is None
    assert asyncio.run(proxy.safe_connect_ip("169.254.169.254", 80, settings)) is None
    assert asyncio.run(proxy.safe_connect_ip("2130706433", 80, settings)) is None


def test_proxy_rejects_any_unsafe_dns_answer(monkeypatch):
    proxy = _load_proxy_module()
    settings = _settings(proxy)

    async def fake_resolve(host, port):
        return [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.1.2.3")]

    monkeypatch.setattr(proxy, "resolve_target_ips", fake_resolve)

    assert asyncio.run(proxy.safe_connect_ip("example.test", 443, settings)) is None


def test_proxy_allows_and_pins_safe_public_dns(monkeypatch):
    proxy = _load_proxy_module()
    settings = _settings(proxy)

    async def fake_resolve(host, port):
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(proxy, "resolve_target_ips", fake_resolve)

    assert asyncio.run(proxy.safe_connect_ip("example.test", 443, settings)) == "93.184.216.34"


def test_missing_proxy_config_raises_before_start(monkeypatch):
    proxy = _load_proxy_module()
    for name in (
        "PROXY_LOG_LEVEL",
        "PROXY_HOST",
        "PROXY_PORT",
        "PROXY_MAX_HEADER_BYTES",
        "PROXY_READ_CHUNK_BYTES",
        "PROXY_RELAY_CHUNK_BYTES",
        "PROXY_BLOCKED_IP_CATEGORIES",
        "PROXY_BLOCKED_SPECIAL_IPS",
        "PROXY_NAT64_NETWORKS",
        "PROXY_SIX_TO_FOUR_NETWORKS",
        "PROXY_IPV4_COMPAT_NETWORKS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="PROXY_LOG_LEVEL"):
        proxy.load_settings()


def test_proxy_config_loads_explicit_policy(monkeypatch):
    proxy = _load_proxy_module()
    values = {
        "PROXY_LOG_LEVEL": "INFO",
        "PROXY_HOST": "0.0.0.0",
        "PROXY_PORT": "8888",
        "PROXY_MAX_HEADER_BYTES": "65536",
        "PROXY_READ_CHUNK_BYTES": "4096",
        "PROXY_RELAY_CHUNK_BYTES": "65536",
        "PROXY_BLOCKED_IP_CATEGORIES": "loopback,link_local,private,reserved,multicast,unspecified",
        "PROXY_BLOCKED_SPECIAL_IPS": "169.254.169.254,fd00:ec2::254",
        "PROXY_NAT64_NETWORKS": "64:ff9b::/96",
        "PROXY_SIX_TO_FOUR_NETWORKS": "2002::/16",
        "PROXY_IPV4_COMPAT_NETWORKS": "::/96",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = proxy.load_settings()

    assert settings.listen_port == 8888
    assert ipaddress.ip_address("169.254.169.254") in settings.blocked_special_ips
