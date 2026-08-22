#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "tools" / "mcp-conformance" / "node_modules" / ".bin" / "conformance"
SERVER = ROOT / "scripts" / "mcp-conformance-server.py"

sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the pinned Thorondor MCP conformance profile")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--readiness-timeout", type=float, default=20)
    parser.add_argument("--runner-timeout", type=float, default=60)
    parser.add_argument("--scenario", action="append")
    return parser.parse_args()


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_ready(process: subprocess.Popen, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"conformance server exited with status {process.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/livez", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"conformance server did not become ready within {timeout}s")


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _scenario_result_directories(output_dir: Path, scenario: str) -> list[Path]:
    return sorted(
        path.parent
        for path in output_dir.glob(f"server-{scenario}-*/checks.json")
    )


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        raise SystemExit(f"output directory must be empty: {args.output_dir}")
    args.output_dir = args.output_dir.resolve()
    from orchestrator.mcp_conformance_profile import load_profile

    profile = load_profile()
    scenarios = args.scenario or [entry["id"] for entry in profile["required_scenarios"]]
    if len(scenarios) != len(set(scenarios)):
        raise SystemExit("scenario ids must be unique")
    valid_scenarios = {entry["id"] for entry in profile["required_scenarios"]}
    unknown_scenarios = set(scenarios) - valid_scenarios
    if unknown_scenarios:
        raise SystemExit(
            "unknown required scenario ids: " + ", ".join(sorted(unknown_scenarios))
        )
    if not RUNNER.is_file():
        raise FileNotFoundError(f"pinned conformance runner is missing: {RUNNER}")

    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    server_log_path = args.output_dir / "server.log"
    server_command = [
        sys.executable,
        str(SERVER),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT), environment.get("PYTHONPATH", "")))
    results = []
    with server_log_path.open("w", encoding="utf-8") as server_log:
        server = subprocess.Popen(
            server_command,
            cwd=ROOT,
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_ready(server, url.removesuffix("/mcp"), args.readiness_timeout)
            for scenario in scenarios:
                log_path = args.output_dir / f"runner-{scenario}.log"
                command = [
                    str(RUNNER),
                    "server",
                    "--url",
                    url,
                    "--scenario",
                    scenario,
                    "--spec-version",
                    profile["protocol_version"],
                    "--output-dir",
                    str(args.output_dir),
                ]
                try:
                    with log_path.open("w", encoding="utf-8") as runner_log:
                        completed = subprocess.run(
                            command,
                            cwd=ROOT,
                            stdout=runner_log,
                            stderr=subprocess.STDOUT,
                            timeout=args.runner_timeout,
                            check=False,
                        )
                    result = {
                        "scenario": scenario,
                        "command": command,
                        "returncode": completed.returncode,
                        "log": str(log_path),
                        "result_directories": [
                            str(path.relative_to(args.output_dir))
                            for path in _scenario_result_directories(args.output_dir, scenario)
                        ],
                    }
                except subprocess.TimeoutExpired:
                    result = {
                        "scenario": scenario,
                        "command": command,
                        "returncode": None,
                        "timed_out": True,
                        "log": str(log_path),
                        "result_directories": [
                            str(path.relative_to(args.output_dir))
                            for path in _scenario_result_directories(args.output_dir, scenario)
                        ],
                    }
                results.append(result)
        finally:
            _terminate(server)

    manifest = {
        "claim": profile["claim"],
        "protocol_version": profile["protocol_version"],
        "server_url": url,
        "server_command": server_command,
        "output_dir": str(args.output_dir),
        "scenarios": results,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evaluation_command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate-mcp-conformance.py"),
        str(args.output_dir),
        "--manifest",
        str(manifest_path),
    ]
    evaluation_path = args.output_dir / "evaluation.json"
    with evaluation_path.open("w", encoding="utf-8") as evaluation_log:
        evaluation = subprocess.run(
            evaluation_command,
            cwd=ROOT,
            env=environment,
            stdout=evaluation_log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return evaluation.returncode


if __name__ == "__main__":
    raise SystemExit(main())
