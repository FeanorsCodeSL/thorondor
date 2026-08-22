"""Cross-platform Docker Compose deploy helpers for Thorondor."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from .envfile import read_env
from .state import HOST_ENDPOINTS_OVERLAY, Draft, compose_overlays, load_draft


class DeployError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComposePlan:
    project_dir: Path
    compose_files: list[str]
    env_files: list[str]
    profile: str


@dataclass(frozen=True)
class DeployProgress:
    step: str
    message: str


def compose_plan(project_dir: str | Path, draft: Draft | None = None) -> ComposePlan:
    root = Path(project_dir).expanduser().resolve()
    draft = draft or load_draft(root)
    compose_files = ["docker-compose.yml", *compose_overlays(draft)]
    env_files = [".env"]
    profile = draft.profile
    if draft.mode == "llamacpp":
        compose_files.append("docker-compose.llamacpp.yml")
        env_files.append(".env.llamacpp")
    return ComposePlan(
        project_dir=root,
        compose_files=compose_files,
        env_files=env_files,
        profile=profile,
    )


def compose_args(plan: ComposePlan) -> list[str]:
    args = ["compose"]
    for env_file in plan.env_files:
        args.extend(["--env-file", env_file])
    for compose_file in plan.compose_files:
        args.extend(["-f", compose_file])
    if plan.profile:
        args.extend(["--profile", plan.profile])
    return args


def run_compose(
    plan: ComposePlan,
    command: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    return runner(
        ["docker", *compose_args(plan), *command],
        cwd=plan.project_dir,
        text=True,
        capture_output=True,
        check=False,
    )


def orchestrator_url(env: dict[str, str], path: str) -> str:
    host = env.get("ORCHESTRATOR_HOST", "127.0.0.1")
    port = env.get("ORCHESTRATOR_PORT", "8080")
    return f"http://{host}:{port}{path}"


def wait_for_health(
    health_url: str,
    *,
    timeout_s: float = 120,
    interval_s: float = 2,
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(timeout=5)
    deadline = time.monotonic() + timeout_s
    last_payload: dict | None = None
    try:
        while time.monotonic() < deadline:
            try:
                response = client.get(health_url)
                if response.status_code == 200:
                    payload = response.json()
                    last_payload = payload if isinstance(payload, dict) else None
                    if (last_payload or {}).get("status") == "ok":
                        return last_payload or {}
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(interval_s)
    finally:
        if owns_client:
            client.close()
    raise DeployError(f"Timed out waiting for service health at {health_url}: {last_payload}")


def run_smoke_search(
    search_url: str,
    *,
    token_budget: int = 4000,
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(timeout=90)
    try:
        response = client.post(
            search_url,
            json={
                "query": "what changed in the EU AI Act timeline in 2025",
                "token_budget": token_budget,
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()
    if not payload.get("passages"):
        raise DeployError("Smoke search returned no passages")
    if payload.get("stats", {}).get("reranked") is not True:
        raise DeployError("Smoke search did not report stats.reranked=true")
    if payload.get("stats", {}).get("tokens_returned", 0) > token_budget:
        raise DeployError("Smoke search exceeded token budget")
    return payload


def deploy(
    project_dir: str | Path,
    *,
    skip_smoke: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Iterator[DeployProgress]:
    root = Path(project_dir).expanduser().resolve()
    draft = load_draft(root)
    plan = compose_plan(root, draft)
    if HOST_ENDPOINTS_OVERLAY in plan.compose_files and not (
        root / HOST_ENDPOINTS_OVERLAY
    ).exists():
        raise DeployError(f"Missing generated {HOST_ENDPOINTS_OVERLAY}; save configuration first")

    for step, command in (("config", ["config"]), ("build", ["build"]), ("up", ["up", "-d"])):
        yield DeployProgress(step, f"docker compose {' '.join(command)}")
        completed = run_compose(plan, command, runner=runner)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise DeployError(f"{step} failed: {detail}")

    env = read_env(root / ".env")
    health_url = orchestrator_url(env, "/health")
    yield DeployProgress("health", health_url)
    wait_for_health(health_url)
    if not skip_smoke:
        search_url = orchestrator_url(env, "/v1/search")
        yield DeployProgress("smoke", search_url)
        run_smoke_search(search_url, token_budget=int(env.get("DEFAULT_TOKEN_BUDGET", "4000")))
