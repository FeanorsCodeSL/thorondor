"""FastAPI app for Thorondor."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
import httpx

from .clients.searxng_client import SEARXNG_INTERNAL_HEADERS
from .models import SearchRequest, SearchResponse
from .mcp_server import mcp, mcp_http_app
from .observability import configure_json_logging, new_request_id, reset_request_id, set_request_id
from .pipeline import SearchDependencyUnavailable, build_deps_from_settings, run_search
from .settings import load_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_deps()
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
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = set_request_id(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)


def get_settings():
    global settings
    if settings is None:
        settings = load_settings()
        configure_json_logging(settings.log_level)
    return settings


def get_deps():
    global deps
    if deps is None:
        deps = build_deps_from_settings(get_settings())
    return deps


def get_health_client() -> httpx.AsyncClient:
    global health_client
    if health_client is None or health_client.is_closed:
        s = get_settings()
        health_client = httpx.AsyncClient(
            timeout=s.healthcheck_timeout_s,
            limits=httpx.Limits(
                max_connections=s.healthcheck_max_connections,
                max_keepalive_connections=s.healthcheck_max_keepalive_connections,
            ),
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


SEARCH_ERROR_RESPONSES = {503: {"description": "Search dependency unavailable"}}


@app.post("/v1/search", responses=SEARCH_ERROR_RESPONSES)
@app.post("/search", responses=SEARCH_ERROR_RESPONSES)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        return await run_search(req, get_deps())
    except SearchDependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"dependency": exc.dependency, "reason": exc.reason},
        ) from exc


async def _check_url(name: str, url: str, headers: dict[str, str] | None = None) -> tuple[str, bool]:
    try:
        response = await get_health_client().get(url, headers=headers)
        return name, response.is_success
    except Exception:
        return name, False


async def _check_chunker(url: str) -> tuple[tuple[str, bool], tuple[str, bool]]:
    try:
        response = await get_health_client().get(url)
        if not response.is_success:
            return ("chunker", False), ("embedding", False)
        payload = response.json()
        return ("chunker", True), ("embedding", bool(payload.get("embedding", False)))
    except Exception:
        return ("chunker", False), ("embedding", False)


def _join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{normalized_path}"


@app.get("/livez")
async def livez():
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    s = get_settings()
    searxng, crawl4ai, chunker_pair, reranker = await asyncio.gather(
        _check_url(
            "searxng",
            f"{s.searxng_url.rstrip('/')}/healthz",
            SEARXNG_INTERNAL_HEADERS,
        ),
        _check_url("crawl4ai", f"{s.crawl4ai_url.rstrip('/')}/health"),
        _check_chunker(f"{s.chunker_url.rstrip('/')}/healthz"),
        _check_url("reranker", _join_url(s.reranker_endpoint, s.reranker_health_path)),
    )
    chunker, embedding = chunker_pair
    dependencies = {
        "searxng": searxng[1],
        "crawl4ai": crawl4ai[1],
        "chunker": chunker[1],
        "embedding": embedding[1],
        "reranker": reranker[1],
    }
    return {
        "status": "ok" if all(dependencies.values()) else "degraded",
        "dependencies": dependencies,
        "hard_failures": [
            name for name in ("searxng", "chunker") if not dependencies[name]
        ],
        "degraded_dependencies": [
            name for name in ("crawl4ai", "embedding", "reranker") if not dependencies[name]
        ],
    }

app.mount("/mcp", mcp_http_app)
