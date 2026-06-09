"""URL normalization helpers."""
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .url_safety import parse_ip_literal


def canonical_host(host: str | None) -> str:
    if not host:
        return ""
    normalized = host.rstrip(".").lower()
    literal = parse_ip_literal(normalized)
    if literal is not None:
        return str(literal)
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def host_for(url: str) -> str:
    parsed = urlparse(url)
    return canonical_host(parsed.hostname)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.rstrip("/")

    scheme = parsed.scheme.lower() or "https"
    host = canonical_host(parsed.hostname)
    port = parsed.port
    include_port = port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443))
    netloc = f"{host}:{port}" if include_port else host
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    )
    path = parsed.path.rstrip("/") or ""
    return urlunparse((scheme, netloc, path, "", query, ""))
