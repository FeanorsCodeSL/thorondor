"""Request correlation and safe logging helpers for the chunker."""
from __future__ import annotations

import contextvars
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit
import uuid

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "thorondor_chunker_request_id",
    default=None,
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def request_id_headers(headers: Mapping[str, str] | None = None) -> dict[str, str] | None:
    merged = dict(headers or {})
    request_id = get_request_id()
    if request_id:
        merged[REQUEST_ID_HEADER] = request_id
    return merged or None


def redact_url(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return value.split("?", 1)[0]
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
