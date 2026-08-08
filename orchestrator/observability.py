"""Request correlation and safe structured logging helpers."""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "thorondor_request_id",
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


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


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


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key in (
            "event",
            "query_hash",
            "sub_query_count",
            "sub_queries_failed",
            "urls_discovered",
            "urls_selected",
            "urls_crawled_ok",
            "urls_crawled_failed",
            "reranked",
            "evidence_quality_chunks_dropped",
            "evidence_items_omitted",
            "raw_markdown_omitted",
            "reason",
            "elapsed_ms",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_json_logging(log_level: str) -> None:
    root = logging.getLogger()
    if getattr(root, "_thorondor_json_logging", False):
        root.setLevel(log_level.upper())
        return
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    formatter = JsonFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
    root.setLevel(log_level.upper())
    setattr(root, "_thorondor_json_logging", True)
