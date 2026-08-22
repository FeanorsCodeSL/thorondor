import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).parents[2]
SERVER_SCRIPT = ROOT / "scripts" / "mcp-conformance-server.py"
PROXY_CODE = "from thorondor_cli.mcp_proxy import main; main()"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"


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
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.2) as response:
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


def _meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "thorondor-stdio-wire-test",
            "version": "1",
        },
    }


def _request(method: str, request_id: int, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params if params is not None else {"_meta": _meta()},
    }


async def _start_proxy(base_url: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), env.get("PYTHONPATH", "")))
    env["THORONDOR_BASE_URL"] = base_url
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        PROXY_CODE,
        cwd=ROOT,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _send(proxy, message: dict) -> tuple[dict, bytes]:
    raw = (json.dumps(message, separators=(",", ":")) + "\n").encode()
    proxy.stdin.write(raw)
    await proxy.stdin.drain()
    line = await asyncio.wait_for(proxy.stdout.readline(), timeout=5)
    if not line:
        raise EOFError("stdio proxy closed before returning a response")
    assert line.endswith(b"\n")
    return json.loads(line), line


async def _send_notification(proxy, method: str, params: dict) -> None:
    raw = (
        json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    proxy.stdin.write(raw)
    await proxy.stdin.drain()


async def _send_without_read(proxy, message: dict) -> None:
    raw = (json.dumps(message, separators=(",", ":")) + "\n").encode()
    proxy.stdin.write(raw)
    await proxy.stdin.drain()


async def _wait_for_state(client, predicate):
    deadline = anyio_current_time() + 5
    while anyio_current_time() < deadline:
        state = (await client.get("/__conformance__/state")).json()
        if predicate(state):
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("conformance server state did not reach the expected value")


def anyio_current_time() -> float:
    return asyncio.get_running_loop().time()


def test_proxy_modern_stdio_framing_stdout_purity_and_removed_methods():
    async def exercise():
        server, base_url = _start_server()
        proxy = await _start_proxy(base_url)
        try:
            discover, discover_line = await _send(proxy, _request("server/discover", 51))
            assert discover_line.count(b"\n") == 1
            assert discover["id"] == 51
            assert discover["result"]["supportedVersions"] == ["2026-07-28"]
            assert discover["result"]["_meta"][SERVER_INFO_KEY]["name"] == "thorondor"

            tools, tools_line = await _send(proxy, _request("tools/list", 52))
            assert tools_line.count(b"\n") == 1
            assert tools["id"] == 52
            assert tools["result"]["resultType"] == "complete"
            assert [tool["name"] for tool in tools["result"]["tools"]] == [
                "web_search",
                "web_fetch",
                "web_map",
                "web_crawl",
            ]

            removed, removed_line = await _send(proxy, _request("ping", 53))
            assert removed_line.count(b"\n") == 1
            assert removed == {
                "jsonrpc": "2.0",
                "id": 53,
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": "ping",
                },
            }
        finally:
            proxy.stdin.close()
            await asyncio.wait_for(proxy.wait(), timeout=5)
            await proxy.stderr.read()
            _stop_server(server)

    asyncio.run(exercise())


def test_proxy_modern_stdio_cancellation_keeps_transport_usable():
    async def exercise():
        server, base_url = _start_server(block_fetch=True)
        proxy = await _start_proxy(base_url)
        send_task: asyncio.Task | None = None
        try:
            request = _request(
                "tools/call",
                61,
                {
                    "name": "web_fetch",
                    "arguments": {"urls": ["https://a.test/article"]},
                    "_meta": _meta(),
                },
            )
            send_task = asyncio.create_task(_send(proxy, request))
            async with httpx2.AsyncClient(base_url=base_url) as client:
                await _wait_for_state(client, lambda state: state["fetch_started"])
                await _send_notification(
                    proxy,
                    "notifications/cancelled",
                    {"requestId": 61},
                )
                state = await _wait_for_state(
                    client,
                    lambda value: value["fetch_started"],
                )
                await _send_without_read(proxy, _request("tools/list", 62))
                tools, _ = await asyncio.wait_for(send_task, timeout=5)
                assert tools["id"] == 62
                await client.post("/__conformance__/release")
                state = await _wait_for_state(
                    client, lambda value: value["active_fetches"] == 0
                )
                assert state["active_fetches"] == 0
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(proxy.stdout.readline(), timeout=0.5)
                tools, _ = await _send(proxy, _request("tools/list", 63))
                assert tools["id"] == 63
        finally:
            if send_task is not None and not send_task.done():
                send_task.cancel()
                with suppress(asyncio.CancelledError):
                    await send_task
            if proxy.returncode is None:
                proxy.terminate()
                await asyncio.wait_for(proxy.wait(), timeout=5)
            await proxy.stderr.read()
            _stop_server(server)

    asyncio.run(exercise())
