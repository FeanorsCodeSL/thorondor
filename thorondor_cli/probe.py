"""Token-safe endpoint probes for Thorondor model dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

ProbeStatus = Literal["ok", "auth_error", "unreachable", "model_not_found", "other"]


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    detail: str = ""


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _clean_detail(text: str, token: str | None) -> str:
    if not token:
        return text
    return text.replace(token, "[redacted]")


def _status_from_response(response: httpx.Response, token: str | None) -> ProbeResult:
    detail = f"HTTP {response.status_code}"
    body = _clean_detail(response.text[:240], token)
    if response.status_code in (401, 403):
        return ProbeResult("auth_error", detail)
    if response.status_code in (404,):
        return ProbeResult("model_not_found", detail)
    if response.status_code == 400 and "model" in body.lower():
        return ProbeResult("model_not_found", "HTTP 400 (model)")
    if 200 <= response.status_code < 300:
        return ProbeResult("ok")
    if 500 <= response.status_code:
        return ProbeResult("other", detail)
    return ProbeResult("other", detail)


def _post_json(url: str, payload: dict, token: str | None, timeout: float = 30) -> ProbeResult:
    try:
        response = httpx.post(url, json=payload, headers=_auth_headers(token), timeout=timeout)
    except httpx.TransportError as exc:
        return ProbeResult("unreachable", type(exc).__name__)
    return _status_from_response(response, token)


def list_models(base_url: str, token: str | None = None) -> list[str] | None:
    """Return model ids from OpenAI-style ``/models`` or ``None`` on failure."""
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers=_auth_headers(token),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None
    models: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
        elif isinstance(item, str):
            models.append(item)
    return models or None


def probe_embedding(base_url: str, model: str, token: str | None = None) -> ProbeResult:
    payload = {"input": "ping", "model": model}
    return _post_json(f"{base_url.rstrip('/')}/v1/embeddings", payload, token, timeout=30)


def probe_reranker(
    base_url: str,
    rerank_path: str,
    model: str,
    token: str | None = None,
) -> ProbeResult:
    payload = {"query": "ping", "documents": ["ping"], "model": model}
    rerank_url = f"{base_url.rstrip('/')}/{rerank_path.lstrip('/')}"
    return _post_json(rerank_url, payload, token, timeout=30)


def probe_llm(base_url: str, model: str, token: str | None = None) -> ProbeResult:
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    return _post_json(f"{base_url.rstrip('/')}/chat/completions", payload, token, timeout=30)


def rewrite_host_for_docker(url: str) -> tuple[str, bool]:
    """Rewrite host loopback URLs to Docker's host gateway name."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return url, False
    netloc = "host.docker.internal"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)), True
