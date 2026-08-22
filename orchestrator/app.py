"""FastAPI app for Thorondor."""
import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from mcp_types import HEADER_MISMATCH

from . import resource_policy as resource_policy_module
from .clients.searxng_client import SEARXNG_INTERNAL_HEADERS
from .crawl_job_store import (
    CrawlJobExpired,
    CrawlJobNotFound,
    CrawlJobQueueFull,
    CrawlJobResultTooLarge,
    IdempotencyConflict,
)
from .crawl_jobs import (
    CrawlJobApiCapacityUnavailable,
    CrawlJobsDisabled,
    CrawlJobWorkerUnavailable,
    InvalidCursor,
    InvalidIdempotencyKey,
    InvalidJobScope,
    InvalidPageLimit,
    RawHtmlPersistenceDisabled,
)
from .fetch_pipeline import run_fetch
from .mcp_server import mcp, mcp_http_app
from .models import (
    MAX_SITE_PAGES,
    CrawlJobCreateResponse,
    CrawlJobResultPage,
    CrawlJobStatus,
    CrawlRequest,
    CrawlResponse,
    FetchRequest,
    FetchResponse,
    MapRequest,
    MapResponse,
    SearchRequest,
    SearchResponse,
)
from .observability import configure_json_logging, new_request_id, reset_request_id, set_request_id
from .pipeline import SearchDependencyUnavailable, build_deps_from_settings, run_search
from .resource_policy import CapacityUnavailable, RouteDeadlineExceeded
from .settings import load_settings
from .site_pipeline import run_crawl, run_map

ResourcePolicy = resource_policy_module.ResourcePolicy
RuntimeAdmission = resource_policy_module.RuntimeAdmission


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime_deps = get_deps()
    try:
        await runtime_deps.start()
        async with mcp.session_manager.run():
            yield
    finally:
        await close_runtime_clients()


app = FastAPI(title="thorondor", lifespan=lifespan, redirect_slashes=False)
deps = None
settings = None
health_client: httpx.AsyncClient | None = None


@app.middleware("http")
async def route_mcp_without_redirect(request: Request, call_next):
    is_mcp_request = request.scope["path"] in {"/mcp", "/mcp/"}
    if is_mcp_request:
        request.scope["headers"] = [
            (name, value.strip(b" \t")) if name.lower().startswith(b"mcp-") else (name, value)
            for name, value in request.scope["headers"]
        ]
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
            if is_mcp_request:
                try:
                    payload = json.loads(body)
                except (ValueError, RecursionError):
                    payload = None
                params = payload.get("params") if isinstance(payload, dict) else None
                meta = params.get("_meta") if isinstance(params, dict) else None
                payload_id = payload.get("id") if isinstance(payload, dict) else None
                if (
                    isinstance(meta, dict)
                    and "io.modelcontextprotocol/protocolVersion" in meta
                    and "mcp-protocol-version" not in request.headers
                ):
                    response = JSONResponse(
                        status_code=400,
                        content={
                            "jsonrpc": "2.0",
                            "id": payload_id,
                            "error": {
                                "code": HEADER_MISMATCH,
                                "message": (
                                    "mcp-protocol-version header does not match "
                                    "the request envelope's protocol version"
                                ),
                            },
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
JOB_LOOKUP_ERROR_RESPONSES = {
    400: {"description": "Invalid scope, idempotency key, cursor, or page limit"},
    404: {"description": "Job not found in the supplied scope"},
    410: {"model": CrawlJobStatus, "description": "Job result retention expired"},
    429: {"description": "No job-store request slot became available"},
    503: {"description": "Durable crawl jobs are disabled or unavailable"},
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


@app.post("/v1/map", responses=TRANSPORT_ERROR_RESPONSES)
async def map_site(req: MapRequest, request: Request) -> MapResponse:
    return await _run_http_operation(
        request,
        run_map(req, get_deps()),
        MapResponse,
    )


@app.post("/v1/crawl", responses=TRANSPORT_ERROR_RESPONSES)
async def crawl_site(req: CrawlRequest, request: Request) -> CrawlResponse:
    return await _run_http_operation(
        request,
        run_crawl(req, get_deps()),
        CrawlResponse,
    )


def _expired_response(exc: CrawlJobExpired) -> JSONResponse:
    status = get_deps().crawl_jobs.status_from_record(exc.record)
    return JSONResponse(status_code=410, content=status.model_dump(mode="json"))


def _job_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CrawlJobsDisabled):
        return HTTPException(status_code=503, detail={"reason": "crawl_jobs_disabled"})
    if isinstance(exc, InvalidJobScope):
        return HTTPException(status_code=400, detail={"reason": "invalid_job_scope"})
    if isinstance(exc, CrawlJobNotFound):
        return HTTPException(status_code=404, detail={"reason": "crawl_job_not_found"})
    if isinstance(exc, InvalidIdempotencyKey):
        return HTTPException(status_code=400, detail={"reason": "invalid_idempotency_key"})
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(status_code=409, detail={"reason": "idempotency_conflict"})
    if isinstance(exc, RawHtmlPersistenceDisabled):
        return HTTPException(
            status_code=400,
            detail={"reason": "crawl_job_raw_html_disabled"},
        )
    if isinstance(exc, CrawlJobQueueFull):
        retry_after = get_deps().resource_policy.admission_retry_after_s
        return HTTPException(
            status_code=429,
            detail={"reason": "crawl_job_store_full"},
            headers={"Retry-After": str(retry_after)},
        )
    if isinstance(exc, CrawlJobApiCapacityUnavailable):
        return HTTPException(
            status_code=429,
            detail={"reason": "crawl_job_api_capacity_unavailable"},
            headers={"Retry-After": str(exc.retry_after_s)},
        )
    if isinstance(exc, CrawlJobWorkerUnavailable):
        return HTTPException(
            status_code=503,
            detail={"reason": "crawl_job_worker_unavailable"},
        )
    if isinstance(exc, InvalidCursor):
        return HTTPException(status_code=400, detail={"reason": "invalid_cursor"})
    if isinstance(exc, InvalidPageLimit):
        return HTTPException(status_code=400, detail={"reason": "invalid_page_limit"})
    if isinstance(exc, CrawlJobResultTooLarge):
        return HTTPException(
            status_code=413,
            detail={"reason": "result_exceeds_page_byte_limit"},
        )
    raise exc


@app.post(
    "/v1/crawl/jobs",
    status_code=202,
    response_model=CrawlJobCreateResponse,
    responses={
        200: {"model": CrawlJobCreateResponse, "description": "Idempotent replay"},
        400: {"description": "Invalid scope, idempotency key, or raw HTML policy"},
        409: {"description": "Idempotency key was used for another request"},
        413: {"description": "Request body exceeds its configured byte limit"},
        429: {"description": "Durable job storage is at its configured limit"},
        503: {"description": "Durable crawl jobs are disabled or unavailable"},
    },
)
async def create_crawl_job(
    req: CrawlRequest,
    job_scope: Annotated[str, Header(alias="X-Thorondor-Job-Scope")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CrawlJobCreateResponse | JSONResponse:
    try:
        response = await get_deps().crawl_jobs.create(
            req,
            scope=job_scope,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _job_error(exc) from exc
    if response.replayed:
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
    return response


@app.get("/v1/crawl/jobs/{job_id}", responses=JOB_LOOKUP_ERROR_RESPONSES)
async def crawl_job_status(
    job_id: str,
    job_scope: Annotated[str, Header(alias="X-Thorondor-Job-Scope")],
) -> CrawlJobStatus:
    try:
        return await get_deps().crawl_jobs.status(job_id, scope=job_scope)
    except CrawlJobExpired as exc:
        return _expired_response(exc)
    except Exception as exc:
        raise _job_error(exc) from exc


@app.get(
    "/v1/crawl/jobs/{job_id}/results",
    responses={
        **JOB_LOOKUP_ERROR_RESPONSES,
        413: {"description": "One retained result exceeds the requested byte cap"},
    },
)
async def crawl_job_results(
    job_id: str,
    job_scope: Annotated[str, Header(alias="X-Thorondor-Job-Scope")],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    max_items: Annotated[int | None, Query(ge=1, le=MAX_SITE_PAGES)] = None,
    max_bytes: Annotated[
        int | None,
        Query(ge=1, description="Byte cap up to the operator-configured maximum."),
    ] = None,
) -> CrawlJobResultPage:
    try:
        return await get_deps().crawl_jobs.results(
            job_id,
            scope=job_scope,
            cursor=cursor,
            max_items=max_items,
            max_bytes=max_bytes,
        )
    except CrawlJobExpired as exc:
        return _expired_response(exc)
    except Exception as exc:
        raise _job_error(exc) from exc


@app.post(
    "/v1/crawl/jobs/{job_id}/cancel",
    responses={
        **JOB_LOOKUP_ERROR_RESPONSES,
        413: {"description": "Request body exceeds its configured byte limit"},
    },
)
async def cancel_crawl_job(
    job_id: str,
    job_scope: Annotated[str, Header(alias="X-Thorondor-Job-Scope")],
) -> CrawlJobStatus:
    try:
        return await get_deps().crawl_jobs.cancel(job_id, scope=job_scope)
    except CrawlJobExpired as exc:
        return _expired_response(exc)
    except Exception as exc:
        raise _job_error(exc) from exc


async def _check_url(
    name: str,
    url: str,
    headers: dict[str, str] | None = None,
) -> tuple[str, bool]:
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
    if s.crawl_jobs_enabled:
        dependencies["crawl_jobs"] = get_deps().crawl_jobs.healthy
    hard_dependencies = ["searxng", "chunker"]
    if s.crawl_jobs_enabled:
        hard_dependencies.append("crawl_jobs")
    return {
        "status": "ok" if all(dependencies.values()) else "degraded",
        "dependencies": dependencies,
        "hard_failures": [
            name for name in hard_dependencies if not dependencies[name]
        ],
        "degraded_dependencies": [
            name for name in ("crawl4ai", "embedding", "reranker") if not dependencies[name]
        ],
    }

app.mount("/mcp", mcp_http_app)
