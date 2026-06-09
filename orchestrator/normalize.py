"""URL normalization helpers."""
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def host_for(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or url).lower()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.rstrip("/")

    scheme = parsed.scheme.lower() or "https"
    host = (parsed.hostname or "").lower()
    port = parsed.port
    include_port = port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443))
    netloc = f"{host}:{port}" if include_port else host
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    )
    path = parsed.path.rstrip("/") or ""
    return urlunparse((scheme, netloc, path, "", query, ""))
