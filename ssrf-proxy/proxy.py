"""Minimal HTTP CONNECT proxy with SSRF egress blocking."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("ssrf-proxy")

IP_CATEGORY_CHECKS = {
    "loopback": lambda ip: ip.is_loopback,
    "link_local": lambda ip: ip.is_link_local,
    "private": lambda ip: ip.is_private,
    "reserved": lambda ip: ip.is_reserved,
    "multicast": lambda ip: ip.is_multicast,
    "unspecified": lambda ip: ip.is_unspecified,
}


@dataclass(frozen=True)
class ProxySettings:
    log_level: str
    listen_host: str
    listen_port: int
    max_header_bytes: int
    read_chunk_bytes: int
    relay_chunk_bytes: int
    blocked_ip_categories: set[str]
    blocked_special_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
    nat64_networks: list[ipaddress.IPv6Network]
    six_to_four_networks: list[ipaddress.IPv6Network]
    ipv4_compat_networks: list[ipaddress.IPv6Network]

    def __post_init__(self) -> None:
        invalid_categories = self.blocked_ip_categories - set(IP_CATEGORY_CHECKS)
        if invalid_categories:
            names = ", ".join(sorted(invalid_categories))
            raise RuntimeError(f"PROXY_BLOCKED_IP_CATEGORIES contains unsupported categories: {names}")
        if not 1 <= self.listen_port <= 65535:
            raise RuntimeError("PROXY_PORT must be between 1 and 65535")
        for name, value in (
            ("PROXY_MAX_HEADER_BYTES", self.max_header_bytes),
            ("PROXY_READ_CHUNK_BYTES", self.read_chunk_bytes),
            ("PROXY_RELAY_CHUNK_BYTES", self.relay_chunk_bytes),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be >= 1")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def _required_int(name: str) -> int:
    return int(_required(name))


def _required_set(name: str) -> set[str]:
    return {part.strip() for part in _required(name).split(",") if part.strip()}


def _required_ips(name: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return {ipaddress.ip_address(value) for value in _required_set(name)}


def _required_ipv6_networks(name: str) -> list[ipaddress.IPv6Network]:
    networks = [ipaddress.ip_network(value, strict=False) for value in _required_set(name)]
    invalid = [str(network) for network in networks if network.version != 6]
    if invalid:
        raise RuntimeError(f"{name} must contain only IPv6 networks: {', '.join(invalid)}")
    return [network for network in networks if isinstance(network, ipaddress.IPv6Network)]


def load_settings() -> ProxySettings:
    return ProxySettings(
        log_level=_required("PROXY_LOG_LEVEL").upper(),
        listen_host=_required("PROXY_HOST"),
        listen_port=_required_int("PROXY_PORT"),
        max_header_bytes=_required_int("PROXY_MAX_HEADER_BYTES"),
        read_chunk_bytes=_required_int("PROXY_READ_CHUNK_BYTES"),
        relay_chunk_bytes=_required_int("PROXY_RELAY_CHUNK_BYTES"),
        blocked_ip_categories={value.lower() for value in _required_set("PROXY_BLOCKED_IP_CATEGORIES")},
        blocked_special_ips=_required_ips("PROXY_BLOCKED_SPECIAL_IPS"),
        nat64_networks=_required_ipv6_networks("PROXY_NAT64_NETWORKS"),
        six_to_four_networks=_required_ipv6_networks("PROXY_SIX_TO_FOUR_NETWORKS"),
        ipv4_compat_networks=_required_ipv6_networks("PROXY_IPV4_COMPAT_NETWORKS"),
    )


def parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    normalized = host.strip("[]").rstrip(".")
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


def _expanded_ip_candidates(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    settings: ProxySettings,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            candidates.append(ip.ipv4_mapped)
        elif any(ip in network for network in settings.nat64_networks + settings.ipv4_compat_networks):
            candidates.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        elif any(ip in network for network in settings.six_to_four_networks):
            candidates.append(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return candidates


def is_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, settings: ProxySettings) -> bool:
    for candidate in _expanded_ip_candidates(ip, settings):
        if candidate in settings.blocked_special_ips:
            return False
        if any(IP_CATEGORY_CHECKS[category](candidate) for category in settings.blocked_ip_categories):
            return False
    return True


def normalize_hostname(host: str) -> str | None:
    stripped = host.strip("[]").rstrip(".")
    if not stripped:
        return None
    literal = parse_ip_literal(stripped)
    if literal is not None:
        return str(literal)
    try:
        return stripped.encode("idna").decode("ascii")
    except UnicodeError:
        return None


async def resolve_target_ips(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = await asyncio.to_thread(
        socket.getaddrinfo,
        host,
        port,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
    )
    ips = []
    for family, _, _, _, sockaddr in infos:
        if family in {socket.AF_INET, socket.AF_INET6}:
            ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips


async def safe_connect_ip(host: str, port: int, settings: ProxySettings) -> str | None:
    normalized = normalize_hostname(host)
    if normalized is None:
        return None
    literal = parse_ip_literal(normalized)
    if literal is not None:
        return str(literal) if is_safe_ip(literal, settings) else None
    try:
        addresses = await resolve_target_ips(normalized, port)
    except OSError:
        return None
    if not addresses or any(not is_safe_ip(ip, settings) for ip in addresses):
        return None
    return str(addresses[0])


def parse_connect_target(target: str) -> tuple[str, int] | None:
    if ":" not in target:
        return None
    host, raw_port = target.rsplit(":", 1)
    try:
        port = int(raw_port)
    except ValueError:
        return None
    return host.strip("[]"), port


def parse_http_target(target: str, headers: dict[str, str]) -> tuple[str, int, str] | None:
    parsed = urlsplit(target)
    if parsed.scheme and parsed.scheme != "http":
        return None
    if parsed.hostname:
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return host, port, path
    host_header = headers.get("host")
    if not host_header:
        return None
    host, _, raw_port = host_header.partition(":")
    port = int(raw_port) if raw_port else 80
    return host, port, target if target.startswith("/") else f"/{target}"


async def read_headers(reader: asyncio.StreamReader, settings: ProxySettings) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(settings.read_chunk_bytes)
        if not chunk:
            break
        data += chunk
        if len(data) > settings.max_header_bytes:
            raise ValueError("headers too large")
    return data


def parse_headers(header_bytes: bytes) -> tuple[str, str, str, dict[str, str], bytes]:
    head, _, rest = header_bytes.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return method, target, version, headers, rest


async def relay(source: asyncio.StreamReader, target: asyncio.StreamWriter, settings: ProxySettings) -> None:
    try:
        while True:
            data = await source.read(settings.relay_chunk_bytes)
            if not data:
                break
            target.write(data)
            await target.drain()
    finally:
        target.close()


async def reject(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode("ascii"))
    await writer.drain()
    writer.close()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    settings: ProxySettings,
) -> None:
    try:
        header_bytes = await read_headers(reader, settings)
        method, target, version, headers, rest = parse_headers(header_bytes)
        if method.upper() == "CONNECT":
            parsed = parse_connect_target(target)
            if parsed is None:
                await reject(writer, "400 Bad Request")
                return
            host, port = parsed
            ip = await safe_connect_ip(host, port, settings)
            if ip is None:
                logger.warning("blocked CONNECT target host=%s port=%s", host, port)
                await reject(writer, "403 Forbidden")
                return
            upstream_reader, upstream_writer = await asyncio.open_connection(ip, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                relay(reader, upstream_writer, settings),
                relay(upstream_reader, writer, settings),
            )
            return

        parsed_http = parse_http_target(target, headers)
        if parsed_http is None:
            await reject(writer, "400 Bad Request")
            return
        host, port, path = parsed_http
        ip = await safe_connect_ip(host, port, settings)
        if ip is None:
            logger.warning("blocked HTTP target host=%s port=%s", host, port)
            await reject(writer, "403 Forbidden")
            return
        upstream_reader, upstream_writer = await asyncio.open_connection(ip, port)
        header_head = header_bytes.partition(b"\r\n\r\n")[0]
        upstream_writer.write(f"{method} {path} {version}\r\n".encode("iso-8859-1"))
        for line in header_head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"proxy-connection:"):
                continue
            upstream_writer.write(line + b"\r\n")
        upstream_writer.write(b"\r\n" + rest)
        await upstream_writer.drain()
        await asyncio.gather(
            relay(reader, upstream_writer, settings),
            relay(upstream_reader, writer, settings),
        )
    except Exception as exc:
        logger.warning("proxy request failed: %s", exc.__class__.__name__)
        writer.close()


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, settings),
        settings.listen_host,
        settings.listen_port,
    )
    logger.info("ssrf proxy listening on %s:%s", settings.listen_host, settings.listen_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
