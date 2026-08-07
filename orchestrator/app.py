"""FastAPI app for Thorondor."""
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx

from .clients.searxng_client import SEARXNG_INTERNAL_HEADERS
from .fetch_pipeline import run_fetch
from .models import FetchRequest, FetchResponse, SearchRequest, SearchResponse
from .mcp_server import mcp, mcp_http_app
from .observability import configure_json_logging, new_request_id, reset_request_id, set_request_id
from .pipeline import SearchDependencyUnavailable, build_deps_from_settings, run_search
from .resource_policy import (
    CapacityUnavailable,
    ResourcePolicy,
    RouteDeadlineExceeded,
    RuntimeAdmission,
)
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
        if request.method in {"POST", "PUT", "PATCH"}:
            max_body_bytes = get_deps().resource_policy.max_request_body_bytes
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    exceeds_limit = int(content_length) > max_body_bytes
                except ValueError:
                    exceeds_limit = False
                if exceeds_limit:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "detail": {
                                "reason": "request_body_too_large",
                                "max_bytes": max_body_bytes,
                            }
                        },
                    )
                    response.headers["X-Request-ID"] = request_id
                    return response
            body = await request.body()
            if len(body) > max_body_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "detail": {
                            "reason": "request_body_too_large",
                            "max_bytes": max_body_bytes,
                        }
                    },
                )
                response.headers["X-Request-ID"] = request_id
                return response
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


TRANSPORT_ERROR_RESPONSES = {
    413: {"description": "Request body exceeds the configured byte limit"},
    429: {"description": "No process-wide request slot became available"},
    500: {"description": "Response exceeds the configured byte limit"},
    504: {"description": "The configured route deadline expired"},
}
SEARCH_ERROR_RESPONSES = {
    **TRANSPORT_ERROR_RESPONSES,
    503: {"description": "Search dependency unavailable"},
}


async def _wait_for_disconnect(request: Request) -> None:
    while True:
        disconnected = await request.is_disconnected()
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        if disconnected:
            return
        await asyncio.sleep(0.05)


async def _run_http_operation(request: Request, operation, response_type):
    task = asyncio.create_task(operation)
    disconnect = asyncio.create_task(_wait_for_disconnect(request))
    try:
        done, _pending = await asyncio.wait(
            {task, disconnect},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            response = await task
        else:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise HTTPException(
                status_code=499,
                detail={"reason": "client_disconnected"},
            )
    except CapacityUnavailable as exc:
        raise HTTPException(
            status_code=429,
            detail={"reason": "capacity_unavailable", "route": exc.route},
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from exc
    except RouteDeadlineExceeded as exc:
        raise HTTPException(
            status_code=504,
            detail={"reason": exc.reason, "route": exc.route},
        ) from exc
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        disconnect.cancel()
        with suppress(asyncio.CancelledError):
            await disconnect
    max_response_bytes = get_deps().resource_policy.max_response_body_bytes
    if len(response.model_dump_json().encode("utf-8")) > max_response_bytes:
        raise HTTPException(
            status_code=500,
            detail={"reason": "response_body_too_large"},
        )
    return response_type.model_validate(response)


@app.post("/v1/search", responses=SEARCH_ERROR_RESPONSES)
@app.post("/search", responses=SEARCH_ERROR_RESPONSES)
async def search(req: SearchRequest, request: Request) -> SearchResponse:
    try:
        return await _run_http_operation(
            request,
            run_search(req, get_deps()),
            SearchResponse,
        )
    except SearchDependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"dependency": exc.dependency, "reason": exc.reason},
        ) from exc


@app.post("/v1/fetch", responses=TRANSPORT_ERROR_RESPONSES)
async def fetch(req: FetchRequest, request: Request) -> FetchResponse:
    return await _run_http_operation(
        request,
        run_fetch(req, get_deps()),
        FetchResponse,
    )


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
