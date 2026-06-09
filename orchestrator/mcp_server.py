"""MCP web_search tool for Thorondor."""
from mcp.server.fastmcp import FastMCP

from .models import SearchRequest
from .pipeline import run_search

mcp = FastMCP("thorondor")
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
async def web_search(query: str, token_budget: int = 4000, max_urls: int = 6) -> dict:
    """Search the live web and return reranked, citation-bearing passages."""
    response = await run_search(
        SearchRequest(query=query, token_budget=token_budget, max_urls=max_urls),
        _get_deps(),
    )
    return {
        "passages": [passage.model_dump() for passage in response.passages],
        "citations": [citation.model_dump() for citation in response.citations],
    }


def main() -> None:
    mcp.run(transport="stdio")
