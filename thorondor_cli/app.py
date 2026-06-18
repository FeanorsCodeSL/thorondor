"""The ``thorondor`` console script."""

from __future__ import annotations

import sys
import subprocess
import shutil
from collections.abc import Callable
from pathlib import Path

from .deploy import ComposePlan, run_compose
from .project import ProjectError, default_project_dir, remove_managed_project, resolve_project_dir
from .state import compute_issues, load_draft

HELP = """thorondor - configure and deploy the Thorondor search stack.

Usage:
  thorondor                               Launch the Textual configurator dashboard.
  thorondor doctor                        Show project/env status without launching the TUI.
  thorondor uninstall [--keep-tool]       Stop and remove the managed deployment.
  thorondor --project-dir PATH            Use an explicit Thorondor project directory.
  thorondor --help                        Show this help.

Installed commands:
  thorondor       Textual stack configurator and deployer.
  thorondor-mcp   Native stdio MCP proxy for a running Thorondor stack.

By default, Thorondor stores its managed deployment under ~/.thorondor.
"""


def _extract_project_dir(args: list[str]) -> tuple[Path | None, list[str]]:
    remaining: list[str] = []
    project_dir: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--project-dir":
            if index + 1 >= len(args):
                raise ValueError("--project-dir requires a path")
            project_dir = Path(args[index + 1])
            index += 2
            continue
        if arg.startswith("--project-dir="):
            project_dir = Path(arg.partition("=")[2])
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return project_dir, remaining


def doctor(project_dir: str | Path | None = None) -> int:
    try:
        project = resolve_project_dir(project_dir)
    except ProjectError as exc:
        print(f"Thorondor project check failed: {exc}")
        return 1
    draft = load_draft(project.root)
    issues = compute_issues(draft, {})
    print("Thorondor configuration")
    print("======================")
    print(f"project: {draft.project_dir}")
    print(f"env: {draft.env_path} ({'present' if draft.env_exists else 'missing'})")
    print(f"mode: {draft.mode}")
    print(f"profile: {draft.profile or '(none)'}")
    print(
        "embedding: "
        f"{draft.env.get('EMBEDDING_ENDPOINT', '')} · {draft.env.get('EMBEDDING_MODEL', '')}"
    )
    print(
        "reranker: "
        f"{draft.env.get('RERANKER_ENDPOINT', '')} · {draft.env.get('RERANKER_MODEL', '')}"
    )
    print(f"host overlay: {'yes' if draft.overlay_needed else 'no'}")
    print("")
    if not issues:
        print("status: ready")
        return 0
    print("issues:")
    for issue in issues:
        print(f"  - [{issue.action}] {issue.label}")
    return 1 if any(issue.severity == "error" for issue in issues) else 0


def run_tui(project_dir: str | Path | None = None) -> int:  # pragma: no cover - terminal UI
    project = resolve_project_dir(project_dir)
    try:
        from .tui.app import ThorondorApp
    except ImportError as exc:
        print(f"Textual is not installed: {exc}")
        print("Install the package with `uv tool install .` or `pipx install .` first.")
        return 1
    ThorondorApp(project.root).run()
    return 0


def _stop_managed_stack(
    project_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[bool, str]:
    if not shutil.which("docker") or not (project_dir / "docker-compose.yml").exists():
        return False, "Docker not found or compose file missing; skipped stack shutdown."
    env_files = [".env"]
    if (project_dir / ".env.production").exists():
        env_files.append(".env.production")
    plan = ComposePlan(
        project_dir=project_dir,
        compose_files=["docker-compose.yml"],
        env_files=env_files,
        profile="",
    )
    completed = run_compose(
        plan,
        ["down", "--volumes", "--remove-orphans"],
        runner=runner,
    )
    if completed.returncode == 0:
        return True, "Stopped Thorondor containers and removed compose volumes."
    detail = completed.stderr.strip() or completed.stdout.strip()
    return False, f"Stack shutdown failed; removing files anyway. {detail}"


def uninstall(
    project_dir: str | Path | None = None,
    *,
    keep_tool: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    root = Path(project_dir).expanduser().resolve() if project_dir else default_project_dir()
    stopped, message = _stop_managed_stack(root, runner=runner)
    print(message)
    try:
        removed = remove_managed_project(root)
    except ProjectError as exc:
        print(f"Thorondor uninstall failed: {exc}", file=sys.stderr)
        return 1
    print(f"Removed Thorondor managed files: {removed}")
    if keep_tool:
        print("Kept the thorondor command.")
        return 0
    if not shutil.which("uv"):
        print("uv not found; remove the thorondor tool manually if needed.")
        return 0
    completed = runner(
        ["uv", "tool", "uninstall", "thorondor"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        print(f"uv tool uninstall failed: {detail}", file=sys.stderr)
        return 1
    if stopped:
        print("Removed the installed thorondor command.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(HELP)
        return 0
    try:
        project_dir, remaining = _extract_project_dir(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if remaining and remaining[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if remaining and remaining[0] == "doctor":
        if len(remaining) > 1:
            print(f"Unknown arguments for doctor: {' '.join(remaining[1:])}", file=sys.stderr)
            return 2
        return doctor(project_dir)
    if remaining and remaining[0] == "uninstall":
        keep_tool = "--keep-tool" in remaining[1:]
        unknown = [arg for arg in remaining[1:] if arg != "--keep-tool"]
        if unknown:
            print(f"Unknown arguments for uninstall: {' '.join(unknown)}", file=sys.stderr)
            return 2
        return uninstall(project_dir, keep_tool=keep_tool)
    if remaining:
        print(f"Unknown subcommand: {remaining[0]}", file=sys.stderr)
        print("Run `thorondor --help` for usage.", file=sys.stderr)
        return 2
    try:
        return run_tui(project_dir)
    except ProjectError as exc:
        print(f"Thorondor project check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
