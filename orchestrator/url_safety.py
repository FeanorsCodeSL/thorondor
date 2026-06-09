"""Impure URL safety checks for crawl candidates."""
from collections.abc import Iterable
import ipaddress
import socket
from urllib.parse import urlparse

from .types import DiscoveryResult


METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}

NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
SIX_TO_FOUR_NETWORK = ipaddress.ip_network("2002::/16")
V4_COMPAT_NETWORK = ipaddress.ip_network("::/96")


def filter_safe_discovery_results(results: Iterable[DiscoveryResult]) -> list[DiscoveryResult]:
    return [result for result in results if is_safe_crawl_url(result.url)]


def is_safe_crawl_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.hostname
    if not host:
        return False

    literal = parse_ip_literal(host)
    if literal is not None:
        return _is_safe_ip(literal)

    try:
        resolved = resolve_host_ips(host)
    except OSError:
        return False

    return bool(resolved) and all(_is_safe_ip(ip) for ip in resolved)


def resolve_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        addresses.append(ipaddress.ip_address(sockaddr[0]))
    return addresses


def parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    normalized = host.rstrip(".")
    try:
        return ipaddress.ip_address(normalized)
    except ValueError:
        pass

    return _parse_legacy_ipv4_literal(normalized)


def _parse_legacy_ipv4_literal(host: str) -> ipaddress.IPv4Address | None:
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(part == "" for part in parts):
        return None

    try:
        values = [_parse_ipv4_component(part) for part in parts]
    except ValueError:
        return None

    if len(values) == 1:
        return _ipv4_from_int(values[0], 32)

    if any(value > 255 for value in values[:-1]):
        return None

    last_bits = 8 * (5 - len(values))
    if _ipv4_from_int(values[-1], last_bits) is None:
        return None

    total = 0
    for value in values[:-1]:
        total = (total << 8) | value
    total = (total << last_bits) | values[-1]
    return ipaddress.IPv4Address(total)


def _parse_ipv4_component(part: str) -> int:
    lower = part.lower()
    if lower.startswith("0x"):
        return int(lower, 16)
    if len(lower) > 1 and lower.startswith("0"):
        return int(lower, 8)
    return int(lower, 10)


def _ipv4_from_int(value: int, bits: int) -> ipaddress.IPv4Address | None:
    if value < 0 or value >= (1 << bits):
        return None
    return ipaddress.IPv4Address(value)


def _is_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return all(_is_safe_ip_candidate(candidate) for candidate in _expand_ip_candidates(ip))


def _expand_ip_candidates(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            candidates.append(ip.ipv4_mapped)
        elif ip in NAT64_NETWORK or ip in V4_COMPAT_NETWORK:
            candidates.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        elif ip in SIX_TO_FOUR_NETWORK:
            candidates.append(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return candidates


def _is_safe_ip_candidate(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in METADATA_IPS:
        return False
    return not (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
