import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx2
import pytest
from mcp_types import HEADER_MISMATCH
from thorondor_contracts import THORONDOR_VERSION

ROOT = Path(__file__).parents[2]
SERVER_SCRIPT = ROOT / "scripts" / "mcp-conformance-server.py"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(block_fetch: bool = False) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), env.get("PYTHONPATH", "")))
    command = [sys.executable, str(SERVER_SCRIPT), "--host", "127.0.0.1", "--port", str(port)]
    if block_fetch:
        command.append("--block-fetch")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("conformance server exited before readiness")
        try:
            with urllib.request.urlopen(f"{base_url}/livez", timeout=0.2) as response:
                if response.status == 200:
                    return process, base_url
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise TimeoutError("conformance server did not become ready")


def _stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="module")
def server_url():
    process, base_url = _start_server()
    try:
        yield base_url
    finally:
        _stop_server(process)


@pytest.fixture
def blocking_server_url():
    process, base_url = _start_server(block_fetch=True)
    try:
        yield base_url
    finally:
        _stop_server(process)


def _meta(version: str = "2026-07-28", include_client_info: bool = True) -> dict:
    result = {
        PROTOCOL_VERSION_KEY: version,
        CLIENT_CAPABILITIES_KEY: {},
    }
    if include_client_info:
        result[CLIENT_INFO_KEY] = {"name": "thorondor-wire-test", "version": "1"}
    return result


def _request(method: str, request_id: int, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params if params is not None else {"_meta": _meta()},
    }


def _headers(
    method: str,
    *,
    protocol_version: str | None = "2026-07-28",
    name: str | None = None,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Method": method,
    }
    if protocol_version is not None:
        headers["MCP-Protocol-Version"] = protocol_version
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


async def _post(client, method: str, request_id: int, params: dict | None = None, **kwargs):
    name = params.get("name") if method == "tools/call" and isinstance(params, dict) else None
    return await client.post(
        "/mcp",
        content=json.dumps(_request(method, request_id, params), separators=(",", ":")),
        headers=_headers(method, name=name, **kwargs),
    )


def _json(response: httpx2.Response) -> dict:
    return json.loads(response.content)


def _raw_post(base_url: str, path: str, body: dict, headers: dict[str, str]) -> tuple[int, dict]:
    target = urlsplit(base_url)
    payload = json.dumps(body, separators=(",", ":")).encode()
    request_lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {target.hostname}:{target.port}",
        f"Content-Length: {len(payload)}",
        "Connection: close",
        *(f"{name}: {value}" for name, value in headers.items()),
        "",
        "",
    ]
    with socket.create_connection((target.hostname, target.port), timeout=5) as connection:
        connection.sendall("\r\n".join(request_lines).encode() + payload)
        response = bytearray()
        while chunk := connection.recv(65536):
            response.extend(chunk)
    head, response_body = bytes(response).split(b"\r\n\r\n", 1)
    status_code = int(head.split(b"\r\n", 1)[0].split()[1])
    return status_code, json.loads(response_body)


def test_modern_http_contract_has_identity_routing_and_sessionless_results(server_url):
    async def exercise():
        async with httpx2.AsyncClient(base_url=server_url) as client:
            discover = await _post(client, "server/discover", 11)
            assert discover.status_code == 200
            discover_body = _json(discover)
            assert discover_body["id"] == 11
            discover_result = discover_body["result"]
            assert discover_result["resultType"] == "complete"
            assert discover_result["supportedVersions"] == ["2026-07-28"]
            assert discover_result["ttlMs"] == 0
            assert discover_result["cacheScope"] == "private"
            assert discover_result["_meta"][SERVER_INFO_KEY] == {
                "name": "thorondor",
                "version": THORONDOR_VERSION,
            }
            assert "mcp-session-id" not in discover.headers

            tools = await _post(client, "tools/list", 12)
            assert tools.status_code == 200
            tools_body = _json(tools)
            assert tools_body["id"] == 12
            tools_result = tools_body["result"]
            assert tools_result["resultType"] == "complete"
            assert tools_result["ttlMs"] == 0
            assert tools_result["cacheScope"] == "private"
            assert [tool["name"] for tool in tools_result["tools"]] == [
                "web_search",
                "web_fetch",
                "web_map",
                "web_crawl",
            ]
            assert tools_result["_meta"][SERVER_INFO_KEY]["version"] == THORONDOR_VERSION
            assert "mcp-session-id" not in tools.headers

            call_params = {
                "name": "web_search",
                "arguments": {"query": "wire"},
                "_meta": _meta(),
            }
            call = await _post(client, "tools/call", 13, call_params)
            assert call.status_code == 200
            call_body = _json(call)
            assert call_body["id"] == 13
            call_result = call_body["result"]
            assert call_result["resultType"] == "complete"
            assert call_result["isError"] is False
            assert call_result["structuredContent"]["schema_version"] == "thorondor.search.v1"
            assert call_result["_meta"][SERVER_INFO_KEY] == {
                "name": "thorondor",
                "version": THORONDOR_VERSION,
            }
            assert "mcp-session-id" not in call.headers

    anyio.run(exercise)


def test_modern_http_errors_preserve_ids_and_status_mapping(server_url):
    async def exercise():
        async with httpx2.AsyncClient(base_url=server_url) as client:
            missing_header = await _post(client, "tools/list", 21, protocol_version=None)
            assert missing_header.status_code == 400
            assert _json(missing_header) == {
                "jsonrpc": "2.0",
                "id": 21,
                "error": {
                    "code": HEADER_MISMATCH,
                    "message": (
                        "mcp-protocol-version header does not match "
                        "the request envelope's protocol version"
                    ),
                },
            }

            missing_meta = await _post(
                client,
                "tools/list",
                22,
                params={},
            )
            assert missing_meta.status_code == 400
            assert _json(missing_meta)["id"] == 22
            assert _json(missing_meta)["error"]["code"] == -32602

            mismatched_method = await client.post(
                "/mcp",
                content=json.dumps(_request("tools/list", 23), separators=(",", ":")),
                headers={**_headers("ping"), "Mcp-Method": "ping"},
            )
            assert mismatched_method.status_code == 400
            assert _json(mismatched_method)["id"] == 23
            assert _json(mismatched_method)["error"]["code"] == HEADER_MISMATCH

            unsupported = await _post(
                client,
                "tools/list",
                24,
                params={"_meta": _meta("v999.0.0")},
                protocol_version="v999.0.0",
            )
            assert unsupported.status_code == 400
            unsupported_error = _json(unsupported)["error"]
            assert unsupported_error["code"] == -32022
            assert unsupported_error["data"] == {
                "supported": ["2026-07-28"],
                "requested": "v999.0.0",
            }

            unknown = await _post(client, "unknown/method", 25)
            assert unknown.status_code == 404
            assert _json(unknown) == {
                "jsonrpc": "2.0",
                "id": 25,
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": "unknown/method",
                },
            }

            removed = await _post(client, "ping", 26)
            assert removed.status_code == 404
            assert _json(removed)["error"]["code"] == -32601

    anyio.run(exercise)


def test_modern_http_accepts_optional_whitespace_in_mcp_header_values(server_url):
    params = {
        "name": "web_search",
        "arguments": {"query": "wire"},
        "_meta": _meta(),
    }
    headers = _headers("tools/call", name="web_search")
    headers["Mcp-Name"] = "  web_search  "

    status_code, body = _raw_post(
        server_url,
        "/mcp",
        _request("tools/call", 27, params),
        headers,
    )

    assert status_code == 200
    assert body["result"]["structuredContent"]["schema_version"] == "thorondor.search.v1"


def test_legacy_http_without_protocol_header_remains_valid(server_url):
    legacy_request = {
        "jsonrpc": "2.0",
        "id": 31,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "legacy-wire-test", "version": "1"},
        },
    }

    async def exercise():
        async with httpx2.AsyncClient(base_url=server_url) as client:
            response = await client.post(
                "/mcp",
                content=json.dumps(legacy_request, separators=(",", ":")),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        data_line = next(
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        result = json.loads(data_line)
        assert result["id"] == 31
        assert result["result"]["protocolVersion"] == "2025-11-25"

    anyio.run(exercise)


async def _wait_for_state(client, predicate):
    deadline = anyio.current_time() + 5
    while anyio.current_time() < deadline:
        state = (await client.get("/__conformance__/state")).json()
        if predicate(state):
            return state
        await anyio.sleep(0.01)
    raise AssertionError("conformance server state did not reach the expected value")


def test_http_disconnect_cancels_fetch_and_releases_admission(blocking_server_url):
    async def exercise():
        async with httpx2.AsyncClient(base_url=blocking_server_url) as client:
            task = asyncio.create_task(
                _post(
                    client,
                    "tools/call",
                    41,
                    {
                        "name": "web_fetch",
                        "arguments": {"urls": ["https://a.test/article"]},
                        "_meta": _meta(),
                    },
                )
            )
            await _wait_for_state(client, lambda state: state["fetch_started"])
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await _wait_for_state(
                client,
                lambda state: state["fetch_cancelled"] and state["active_fetches"] == 0,
            )

            response = await _post(
                client,
                "tools/call",
                42,
                {
                    "name": "web_fetch",
                    "arguments": {"urls": ["https://a.test/article"]},
                    "_meta": _meta(),
                },
            )
            assert response.status_code == 200
            assert (
                _json(response)["result"]["structuredContent"]["schema_version"]
                == "thorondor.fetch.v1"
            )
            state = (await client.get("/__conformance__/state")).json()
            assert state["active_fetches"] == 0

    anyio.run(exercise)
