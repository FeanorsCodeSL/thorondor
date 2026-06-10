"""Impure URL safety checks for crawl candidates."""
from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlparse

from .types import DiscoveryResult


IP_CATEGORY_CHECKS = {
    "loopback": lambda ip: ip.is_loopback,
    "link_local": lambda ip: ip.is_link_local,
    "private": lambda ip: ip.is_private,
    "reserved": lambda ip: ip.is_reserved,
    "multicast": lambda ip: ip.is_multicast,
    "unspecified": lambda ip: ip.is_unspecified,
}


@dataclass(frozen=True)
class UrlSafetyPolicy:
    blocked_ip_categories: set[str]
    blocked_special_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
    nat64_networks: list[ipaddress.IPv6Network]
    six_to_four_networks: list[ipaddress.IPv6Network]
    ipv4_compat_networks: list[ipaddress.IPv6Network]

    def __post_init__(self) -> None:
        invalid_categories = self.blocked_ip_categories - set(IP_CATEGORY_CHECKS)
        if invalid_categories:
            names = ", ".join(sorted(invalid_categories))
            raise RuntimeError(f"URL_SAFETY_BLOCKED_IP_CATEGORIES contains unsupported categories: {names}")


def filter_safe_discovery_results(
    results: Iterable[DiscoveryResult],
    policy: UrlSafetyPolicy,
) -> list[DiscoveryResult]:
    return [result for result in results if is_safe_crawl_url(result.url, policy)]


def is_safe_crawl_url(url: str, policy: UrlSafetyPolicy) -> bool:
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
        return _is_safe_ip(literal, policy)

    try:
        resolved = resolve_host_ips(host)
    except OSError:
        return False

    return bool(resolved) and all(_is_safe_ip(ip, policy) for ip in resolved)


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


def _is_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, policy: UrlSafetyPolicy) -> bool:
    return all(_is_safe_ip_candidate(candidate, policy) for candidate in _expand_ip_candidates(ip, policy))


def _expand_ip_candidates(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    policy: UrlSafetyPolicy,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            candidates.append(ip.ipv4_mapped)
        elif any(ip in network for network in policy.nat64_networks + policy.ipv4_compat_networks):
            candidates.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        elif any(ip in network for network in policy.six_to_four_networks):
            candidates.append(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return candidates


def _is_safe_ip_candidate(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    policy: UrlSafetyPolicy,
) -> bool:
    if ip in policy.blocked_special_ips:
        return False
    return not any(IP_CATEGORY_CHECKS[category](ip) for category in policy.blocked_ip_categories)
