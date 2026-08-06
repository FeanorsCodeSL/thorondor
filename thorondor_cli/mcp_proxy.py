"""Native stdio MCP proxy for a running Thorondor HTTP stack."""

import os
from typing import Literal

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("thorondor")


def _base_url() -> str:
    return os.environ.get("THORONDOR_BASE_URL", "http://localhost:8080").rstrip("/")


def _headers() -> dict[str, str]:
    api_key = os.environ.get("THORONDOR_API_KEY", "").strip()
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


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
        `text`, `score`, `token_count`, `citation_id`, `start_index`, `end_index`,
        `verbatim`, `document_id`, and `evidence_id`. Citations carry exact
        evidence spans and source-attributed metadata. Discovery degradation is
        exposed through `stats.discovery_status` and
        `stats.unresponsive_engines`; `stats.reason` is a closed enum when the
        call returns an empty/degraded 200 response.
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
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{_base_url()}/v1/search",
                json=payload,
                headers=_headers(),
            )
            if response.status_code >= 500:
                return {"error": "thorondor_unavailable", "status_code": response.status_code}
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": "thorondor_http_error",
            "status_code": exc.response.status_code,
            "detail": exc.response.text[:500],
        }
    except httpx.HTTPError as exc:
        return {"error": "thorondor_unreachable", "detail": type(exc).__name__}
    except ValueError:
        return {"error": "thorondor_invalid_json"}


def main() -> None:
    mcp.run(transport="stdio")
