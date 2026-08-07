"""Native stdio MCP proxy for a running Thorondor HTTP stack."""

import json
import os
from typing import Literal

import httpx
from mcp.server.mcpserver import MCPServer
from thorondor_contracts import (
    CRAWL_TOOL_DESCRIPTION,
    DEFAULT_FETCH_ROUTE_DEADLINE_S,
    DEFAULT_MAP_ROUTE_DEADLINE_S,
    DEFAULT_MAX_RESPONSE_BODY_BYTES,
    DEFAULT_SEARCH_ROUTE_DEADLINE_S,
    DEFAULT_SITE_CRAWL_ROUTE_DEADLINE_S,
    FETCH_TOOL_DESCRIPTION,
    MAP_TOOL_DESCRIPTION,
    SEARCH_TOOL_DESCRIPTION,
    FetchCapability,
    TargetWatch,
)

mcp = MCPServer("thorondor")


def _base_url() -> str:
    return os.environ.get("THORONDOR_BASE_URL", "http://localhost:8080").rstrip("/")


def _headers() -> dict[str, str]:
    api_key = os.environ.get("THORONDOR_API_KEY", "").strip()
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _error_response(
    status_code: int,
    headers: httpx.Headers,
    body: bytes,
) -> dict:
    try:
        payload = json.loads(body)
        detail = payload.get("detail", {}) if isinstance(payload, dict) else {}
    except ValueError:
        detail = {}
    reason = detail.get("reason") if isinstance(detail, dict) else None
    result = {
        "error": reason or "thorondor_http_error",
        "status_code": status_code,
    }
    if isinstance(detail, dict) and isinstance(detail.get("route"), str):
        result["route"] = detail["route"]
    retry_after = headers.get("retry-after")
    if status_code == 429 and retry_after and retry_after.isdigit():
        result["retry_after_s"] = int(retry_after)
    return result


async def _post(path: str, payload: dict, route: str, timeout_s: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            async with client.stream(
                "POST",
                f"{_base_url()}{path}",
                json=payload,
                headers=_headers(),
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > DEFAULT_MAX_RESPONSE_BODY_BYTES:
                        return {"error": "response_body_too_large", "status_code": 502}
                    body.extend(chunk)
                body_bytes = bytes(body)
                if response.is_error:
                    return _error_response(
                        response.status_code,
                        response.headers,
                        body_bytes,
                    )
                return json.loads(body_bytes)
    except httpx.TimeoutException:
        return {"error": "deadline_cancelled", "status_code": 504, "route": route}
    except httpx.HTTPError as exc:
        return {"error": "thorondor_unreachable", "detail": type(exc).__name__}
    except ValueError:
        return {"error": "thorondor_invalid_json"}


@mcp.tool(description=SEARCH_TOOL_DESCRIPTION)
async def web_search(
    query: str,
    search_profile: Literal["quick", "research", "deep"] | None = None,
    token_budget: int | None = None,
    max_urls: int | None = None,
    freshness: Literal["day", "week", "month", "year"] | None = None,
    domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    decompose: bool | None = None,
    max_passages: int | None = None,
    include_raw_markdown: bool | None = None,
) -> dict:
    """Search the live web and return source-cited evidence passages.

    Use for current or external information that requires verification.
    search_profile selects quick, research, or deep bounded search. decompose
    controls query expansion. include_raw_markdown adds source Markdown when
    exact source context is needed.
    """
    request_data = {
        "query": query,
        "search_profile": search_profile,
        "token_budget": token_budget,
        "max_urls": max_urls,
        "freshness": freshness,
        "domains": domains,
        "exclude_domains": exclude_domains,
        "decompose": decompose,
        "max_passages": max_passages,
        "include_raw_markdown": include_raw_markdown,
    }
    payload = {key: value for key, value in request_data.items() if value is not None}
    return await _post(
        "/v1/search",
        payload,
        "search",
        DEFAULT_SEARCH_ROUTE_DEADLINE_S,
    )


@mcp.tool(description=FETCH_TOOL_DESCRIPTION)
async def web_fetch(
    urls: list[str],
    capabilities: list[FetchCapability] | None = None,
    force_refresh: bool | None = None,
    stale_while_revalidate: bool | None = None,
    watch: TargetWatch | None = None,
) -> dict:
    """Fetch evidence from one to four known URLs.

    Use this when an agent already knows the target URLs. Each URL returns a
    typed terminal outcome, retryability, final URL, status, content type,
    bounded metadata and links, and untrusted provenance. Raw HTML is returned
    only when explicitly requested. Server-configured shared byte limits,
    route and stage deadlines, process-wide admission slots, per-host crawl
    limits, and internal fan-out caps apply.

    Args:
        urls: Unique HTTP or HTTPS targets to fetch.
        capabilities: Required output capabilities. Omit for Markdown,
            JavaScript rendering, links, and metadata. Unsupported capability
            combinations fail closed per URL.

    Returns:
        The versioned `thorondor.fetch.v1` envelope with bounded per-URL
        results and aggregate terminal-outcome counts.
    """
    request_data = {
        "urls": urls,
        "capabilities": capabilities,
        "force_refresh": force_refresh,
        "stale_while_revalidate": stale_while_revalidate,
        "watch": watch.model_dump() if watch is not None else None,
    }
    payload = {key: value for key, value in request_data.items() if value is not None}
    return await _post(
        "/v1/fetch",
        payload,
        "fetch",
        DEFAULT_FETCH_ROUTE_DEADLINE_S,
    )


@mcp.tool(description=MAP_TOOL_DESCRIPTION)
async def web_map(
    url: str,
    sitemap: Literal["include", "only", "skip"] | None = None,
    max_depth: int | None = None,
    max_pages: int | None = None,
    max_discovered_urls: int | None = None,
    include_parent_paths: bool | None = None,
    include_subdomains: bool | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    query_parameters: Literal["preserve", "strip", "exclude"] | None = None,
    allowed_file_extensions: list[str] | None = None,
    include_search: bool | None = None,
) -> dict:
    """Discover a bounded, robots-aware URL map for one site."""
    request_data = {
        "url": url,
        "sitemap": sitemap,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "max_discovered_urls": max_discovered_urls,
        "include_parent_paths": include_parent_paths,
        "include_subdomains": include_subdomains,
        "include_paths": include_paths,
        "exclude_paths": exclude_paths,
        "query_parameters": query_parameters,
        "allowed_file_extensions": allowed_file_extensions,
        "include_search": include_search,
    }
    payload = {key: value for key, value in request_data.items() if value is not None}
    return await _post(
        "/v1/map",
        payload,
        "map",
        DEFAULT_MAP_ROUTE_DEADLINE_S,
    )


@mcp.tool(description=CRAWL_TOOL_DESCRIPTION)
async def web_crawl(
    url: str,
    sitemap: Literal["include", "only", "skip"] | None = None,
    max_depth: int | None = None,
    max_pages: int | None = None,
    max_discovered_urls: int | None = None,
    include_parent_paths: bool | None = None,
    include_subdomains: bool | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    query_parameters: Literal["preserve", "strip", "exclude"] | None = None,
    allowed_file_extensions: list[str] | None = None,
    include_search: bool | None = None,
    capabilities: list[FetchCapability] | None = None,
) -> dict:
    """Crawl a small, bounded part of one site and return typed evidence."""
    request_data = {
        "url": url,
        "sitemap": sitemap,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "max_discovered_urls": max_discovered_urls,
        "include_parent_paths": include_parent_paths,
        "include_subdomains": include_subdomains,
        "include_paths": include_paths,
        "exclude_paths": exclude_paths,
        "query_parameters": query_parameters,
        "allowed_file_extensions": allowed_file_extensions,
        "include_search": include_search,
        "capabilities": capabilities,
    }
    payload = {key: value for key, value in request_data.items() if value is not None}
    return await _post(
        "/v1/crawl",
        payload,
        "crawl",
        DEFAULT_SITE_CRAWL_ROUTE_DEADLINE_S,
    )


def main() -> None:
    mcp.run(transport="stdio")
