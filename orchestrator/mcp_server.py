"""MCP web_search tool for Thorondor."""
import os
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .models import SearchRequest
from .pipeline import run_search

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


@mcp.tool()
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
    """Search the live web for evidence-bearing passages.

    Use this when an agent needs current web evidence with citations rather
    than a prose summary. The tool is a thin twin of REST `POST /v1/search`:
    defaults are resolved by the shared pipeline, and the returned envelope is
    the same `SearchResponse` shape.

    Args:
        query: The user's original information need. Reranking always scores
            against this query, not any discovery sub-query.
        search_profile: Optional default profile. `quick` is for narrow factual
            lookup, `research` broadens URL/passages/token defaults for normal
            investigation, and `deep` uses the largest bounded defaults.
        token_budget: Optional maximum returned passage budget. If omitted,
            the profile or server default is used. This controls assembly, not
            crawling.
        max_urls: Optional cap on selected URLs before crawl. If omitted, the
            profile or server default is used.
        freshness: Optional discovery freshness hint: day, week, month, year.
        domains: Optional domain allowlist for this call.
        exclude_domains: Optional domain blocklist for this call.
        decompose: Whether to let the optional query planner split/expand the
            query. If omitted, the REST default is used.
        max_passages: Optional returned chunk/passage count cap after token
            budgeting. It is not a page count.
        include_raw_markdown: Include raw markdown for returned citations when
            the caller needs source-preserving evidence.

    Returns:
        A versioned response envelope with `query`, `passages`, `citations`,
        `stats`, optional `raw_markdown`, and `schema_version`. Each passage has
        `text`, `score`, `token_count`, and `citation_id`;
        `stats.discovery_status` and `stats.unresponsive_engines` report search
        engine degradation, while `stats.reason` is a closed enum when the call
        returns an empty 200 response.
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
    request = SearchRequest(**{key: value for key, value in request_data.items() if value is not None})
    response = await run_search(request, _get_deps())
    return response.model_dump()


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
