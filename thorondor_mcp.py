from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from typing import Any, Protocol, cast

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    SERVER_INFO_META_KEY,
    CallToolResult,
    TextContent,
    ToolAnnotations,
)
from thorondor_contracts import THORONDOR_VERSION

MCP_TOOL_ANNOTATIONS = {
    "web_search": ToolAnnotations(read_only_hint=True, open_world_hint=True),
    "web_fetch": ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    "web_map": ToolAnnotations(read_only_hint=True, open_world_hint=True),
    "web_crawl": ToolAnnotations(read_only_hint=True, open_world_hint=True),
}


class ToolListProvider(Protocol):
    async def __call__(self) -> list[Any]: ...


class MCPRequestValidationError(Exception):
    pass


def tool_annotations(name: str) -> ToolAnnotations:
    return MCP_TOOL_ANNOTATIONS[name].model_copy(deep=True)


def unknown_tool_middleware(list_tools: ToolListProvider) -> ServerMiddleware[Any]:
    async def middleware(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method == "tools/call" and isinstance(ctx.params, Mapping):
            name = ctx.params.get("name")
            if isinstance(name, str) and not any(tool.name == name for tool in await list_tools()):
                raise MCPError(code=INVALID_PARAMS, message="Unknown tool", data=name)
        return await call_next(ctx)

    return middleware


def wrap_mcp_tool(
    fn: Callable[..., Awaitable[dict[str, Any]]],
    max_bytes: Callable[[], int],
) -> Callable[..., Awaitable[CallToolResult]]:
    @wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            response_limit = max_bytes()
            validate_mcp_response_limit(response_limit)
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(code=INTERNAL_ERROR, message="Internal error") from exc
        try:
            payload = await fn(*args, **kwargs)
            if not isinstance(payload, dict):
                raise TypeError("MCP tool returned a non-object payload")
            return bounded_call_tool_result(payload, response_limit)
        except MCPRequestValidationError:
            return bounded_call_tool_result(
                {"error": "invalid_request", "status_code": 422}, response_limit
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(code=INTERNAL_ERROR, message="Internal error") from exc

    cast(Any, wrapped).__signature__ = inspect.signature(fn).replace(
        return_annotation=CallToolResult
    )
    return wrapped


def bounded_call_tool_result(payload: dict[str, Any], max_bytes: int) -> CallToolResult:
    validate_mcp_response_limit(max_bytes)
    result = _call_tool_result(payload, is_error="error" in payload)
    if _serialized_size(result) <= max_bytes:
        return result

    bounded_payload = {
        "error": "response_body_too_large",
        "status_code": 500,
        "max_bytes": max_bytes,
    }
    bounded_result = _call_tool_result(bounded_payload, is_error=True)
    if _serialized_size(bounded_result) <= max_bytes:
        return bounded_result
    return CallToolResult(
        content=[],
        is_error=True,
        _meta={SERVER_INFO_META_KEY: {"name": "thorondor", "version": THORONDOR_VERSION}},
    )


def validate_mcp_response_limit(max_bytes: int) -> None:
    if max_bytes < minimum_mcp_response_bytes():
        raise MCPError(
            code=INTERNAL_ERROR,
            message="MCP response limit is below the minimum identity-bearing result size",
        )


def minimum_mcp_response_bytes() -> int:
    return _serialized_size(
        CallToolResult(
            content=[],
            is_error=True,
            _meta={
                SERVER_INFO_META_KEY: {
                    "name": "thorondor",
                    "version": THORONDOR_VERSION,
                }
            },
        )
    )


def _call_tool_result(payload: dict[str, Any], is_error: bool) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            )
        ],
        structured_content=payload,
        is_error=is_error,
        _meta={SERVER_INFO_META_KEY: {"name": "thorondor", "version": THORONDOR_VERSION}},
    )


def _serialized_size(result: CallToolResult) -> int:
    body = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    return len(json.dumps(body, separators=(",", ":")).encode("utf-8"))
