"""MCP web_search tool for Thorondor."""
import json
import os
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from thorondor_contracts import (
    CRAWL_TOOL_DESCRIPTION,
    FETCH_TOOL_DESCRIPTION,
    MAP_TOOL_DESCRIPTION,
    SEARCH_TOOL_DESCRIPTION,
)

from .fetch_pipeline import run_fetch
from .models import (
    CrawlRequest,
    FetchCapability,
    FetchRequest,
    MapRequest,
    SearchRequest,
)
from .pipeline import run_search
from .resource_policy import CapacityUnavailable, RouteDeadlineExceeded
from .site_pipeline import run_crawl, run_map

DEFAULT_MCP_ALLOWED_HOSTS = (
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
    "thorondor:*",
    "orchestrator:*",
)
DEFAULT_MCP_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
    "http://thorondor:*",
    "http://orchestrator:*",
)


def _csv_env(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


mcp = MCPServer("thorondor")
deps_override = None
def set_deps(deps) -> None:
    global deps_override
    deps_override = deps


def _get_deps():
    if deps_override is not None:
        return deps_override
    from .app import get_deps

    return get_deps()


def _operation_error(exc: CapacityUnavailable | RouteDeadlineExceeded) -> dict:
    if isinstance(exc, CapacityUnavailable):
        return {
            "error": "capacity_unavailable",
            "status_code": 429,
            "route": exc.route,
            "retry_after_s": exc.retry_after_s,
        }
    return {
        "error": exc.reason,
        "status_code": 504,
        "route": exc.route,
    }


def _request_limit_error(payload: dict, deps) -> dict | None:
    size = len(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    max_bytes = deps.resource_policy.max_request_body_bytes
    if size <= max_bytes:
        return None
    return {
        "error": "request_body_too_large",
        "status_code": 413,
        "max_bytes": max_bytes,
    }


def _bounded_response(response, deps) -> dict:
    if (
        len(response.model_dump_json().encode("utf-8"))
        > deps.resource_policy.max_response_body_bytes
    ):
        return {"error": "response_body_too_large", "status_code": 500}
    return response.model_dump()


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
    request_data = {key: value for key, value in request_data.items() if value is not None}
    deps = _get_deps()
    if error := _request_limit_error(request_data, deps):
        return error
    request = SearchRequest(**request_data)
    try:
        response = await run_search(request, deps)
    except (CapacityUnavailable, RouteDeadlineExceeded) as exc:
        return _operation_error(exc)
    return _bounded_response(response, deps)


@mcp.tool(description=FETCH_TOOL_DESCRIPTION)
async def web_fetch(
    urls: list[str],
    capabilities: list[FetchCapability] | None = None,
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
    request_data = {"urls": urls, "capabilities": capabilities}
    request_data = {key: value for key, value in request_data.items() if value is not None}
    deps = _get_deps()
    if error := _request_limit_error(request_data, deps):
        return error
    request = FetchRequest(**request_data)
    try:
        response = await run_fetch(request, deps)
    except (CapacityUnavailable, RouteDeadlineExceeded) as exc:
        return _operation_error(exc)
    return _bounded_response(response, deps)


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
    request_data = {key: value for key, value in request_data.items() if value is not None}
    deps = _get_deps()
    if error := _request_limit_error(request_data, deps):
        return error
    try:
        response = await run_map(MapRequest(**request_data), deps)
    except (CapacityUnavailable, RouteDeadlineExceeded) as exc:
        return _operation_error(exc)
    return _bounded_response(response, deps)


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
    request_data = {key: value for key, value in request_data.items() if value is not None}
    deps = _get_deps()
    if error := _request_limit_error(request_data, deps):
        return error
    try:
        response = await run_crawl(CrawlRequest(**request_data), deps)
    except (CapacityUnavailable, RouteDeadlineExceeded) as exc:
        return _operation_error(exc)
    return _bounded_response(response, deps)


mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS", DEFAULT_MCP_ALLOWED_HOSTS),
        allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS", DEFAULT_MCP_ALLOWED_ORIGINS),
    ),
)


def main() -> None:
    mcp.run(transport="stdio")
