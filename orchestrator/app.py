"""FastAPI app for Thorondor."""
import asyncio

from fastapi import FastAPI, HTTPException
import httpx

from .models import SearchRequest, SearchResponse
from .pipeline import SearchDependencyUnavailable, build_deps_from_settings, run_search
from .settings import load_settings

app = FastAPI(title="thorondor")
deps = None
settings = None


def get_settings():
    global settings
    if settings is None:
        settings = load_settings()
    return settings


def get_deps():
    global deps
    if deps is None:
        deps = build_deps_from_settings(get_settings())
    return deps


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    resolved = req.model_copy(
        update={
            "token_budget": req.token_budget or get_deps().default_token_budget,
            "max_urls": req.max_urls or get_deps().default_max_urls,
        }
    )
    try:
        return await run_search(resolved, get_deps())
    except SearchDependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"dependency": exc.dependency, "reason": exc.reason},
        ) from exc


async def _check_url(name: str, url: str) -> tuple[str, bool]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url)
        return name, response.status_code < 500
    except Exception:
        return name, False


def _join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{normalized_path}"


@app.get("/healthz")
async def healthz():
    if deps is not None:
        return {
            "status": "ok",
            "dependencies": {
                "searxng": True,
                "crawl4ai": True,
                "chunker": True,
                "reranker": True,
            },
        }
    try:
        s = get_settings()
    except RuntimeError:
        return {
            "status": "ok",
            "dependencies": {
                "searxng": False,
                "crawl4ai": False,
                "chunker": False,
                "reranker": False,
            },
        }
    checks = await asyncio.gather(
        _check_url("searxng", f"{s.searxng_url.rstrip('/')}/search?q=health&format=json"),
        _check_url("crawl4ai", f"{s.crawl4ai_url.rstrip('/')}/healthz"),
        _check_url("chunker", f"{s.chunker_url.rstrip('/')}/healthz"),
        _check_url("reranker", _join_url(s.reranker_endpoint, s.reranker_health_path)),
    )
    return {"status": "ok", "dependencies": dict(checks)}


from .mcp_server import mcp  # noqa: E402

app.mount("/mcp", mcp.streamable_http_app())
