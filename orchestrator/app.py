"""FastAPI app for Thorondor."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
import httpx

from .models import SearchRequest, SearchResponse
from .mcp_server import mcp
from .pipeline import SearchDependencyUnavailable, build_deps_from_settings, run_search
from .settings import load_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            await close_runtime_clients()


app = FastAPI(title="thorondor", lifespan=lifespan, redirect_slashes=False)
deps = None
settings = None
health_client: httpx.AsyncClient | None = None


@app.middleware("http")
async def route_mcp_without_redirect(request: Request, call_next):
    if request.scope["path"] == "/mcp":
        request.scope["path"] = "/mcp/"
    return await call_next(request)


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


def get_health_client() -> httpx.AsyncClient:
    global health_client
    if health_client is None or health_client.is_closed:
        health_client = httpx.AsyncClient(
            timeout=2.0,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
    return health_client


async def close_runtime_clients() -> None:
    global deps, health_client
    if deps is not None:
        close = getattr(deps, "aclose", None)
        if close is not None:
            await close()
        deps = None
    if health_client is not None:
        await health_client.aclose()
        health_client = None


@app.post("/v1/search", response_model=SearchResponse)
@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        return await run_search(req, get_deps())
    except SearchDependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"dependency": exc.dependency, "reason": exc.reason},
        ) from exc


async def _check_url(name: str, url: str) -> tuple[str, bool]:
    try:
        response = await get_health_client().get(url)
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

app.mount("/mcp", mcp.streamable_http_app())
