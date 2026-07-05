"""Project directory resolution for the Thorondor configurator."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .envfile import ENV_TEMPLATE, LLAMACPP_TEMPLATE, PRODUCTION_TEMPLATE, template_text

REQUIRED_PROJECT_PATHS = (
    "docker-compose.yml",
    "docker-compose.llamacpp.yml",
    "searxng/settings.yml",
)
MANAGED_MARKER = ".thorondor-managed"
MANAGED_ASSETS = {
    "docker-compose.yml": ("assets", "docker-compose.yml"),
    "docker-compose.llamacpp.yml": ("assets", "docker-compose.llamacpp.yml"),
    "searxng/settings.yml": ("assets", "searxng", "settings.yml"),
    "searxng/limiter.toml": ("assets", "searxng", "limiter.toml"),
}
MANAGED_TEMPLATES = {
    ".env": ENV_TEMPLATE,
    ".env.llamacpp": LLAMACPP_TEMPLATE,
    ".env.production": PRODUCTION_TEMPLATE,
}


class ProjectError(ValueError):
    """Raised when a directory is not a usable Thorondor project."""


@dataclass(frozen=True)
class Project:
    root: Path

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    @property
    def llamacpp_env_path(self) -> Path:
        return self.root / ".env.llamacpp"


def missing_project_paths(path: Path) -> list[str]:
    root = Path(path).expanduser().resolve()
    return [relative for relative in REQUIRED_PROJECT_PATHS if not (root / relative).exists()]


def default_project_dir() -> Path:
    return Path(os.environ.get("THORONDOR_HOME", "~/.thorondor")).expanduser().resolve()


def is_managed_project(path: str | Path) -> bool:
    return (Path(path).expanduser().resolve() / MANAGED_MARKER).exists()


def _copy_package_asset(relative_parts: tuple[str, ...], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source = resources.files("thorondor_cli").joinpath(*relative_parts)
    target.write_bytes(source.read_bytes())


def _is_empty_directory(path: Path) -> bool:
    return path.exists() and path.is_dir() and not any(path.iterdir())


def ensure_managed_project(project_dir: str | Path | None = None) -> Project:
    root = Path(project_dir).expanduser().resolve() if project_dir else default_project_dir()
    if root.exists() and not root.is_dir():
        raise ProjectError(f"{root} exists and is not a directory")
    root.mkdir(parents=True, exist_ok=True)

    for relative, asset_parts in MANAGED_ASSETS.items():
        target = root / relative
        if not target.exists():
            _copy_package_asset(asset_parts, target)
    for relative, template_name in MANAGED_TEMPLATES.items():
        target = root / relative
        if not target.exists():
            target.write_text(template_text(template_name), encoding="utf-8")
    marker = root / MANAGED_MARKER
    if not marker.exists():
        marker.write_text("Managed by the Thorondor CLI.\n", encoding="utf-8")
    missing = missing_project_paths(root)
    if missing:
        raise ProjectError(f"{root} is missing packaged assets: {', '.join(missing)}")
    return Project(root=root)


def resolve_project_dir(project_dir: str | Path | None = None) -> Project:
    """Resolve and validate a Thorondor project directory."""
    if project_dir is None:
        return ensure_managed_project()
    root = Path(project_dir).expanduser().resolve()
    if not root.exists() or _is_empty_directory(root) or is_managed_project(root):
        return ensure_managed_project(root)
    missing = missing_project_paths(root)
    if missing:
        raise ProjectError(
            f"{root} is not a Thorondor project; missing: {', '.join(missing)}"
        )
    return Project(root=root)


def remove_managed_project(project_dir: str | Path | None = None) -> Path:
    root = Path(project_dir).expanduser().resolve() if project_dir else default_project_dir()
    if not is_managed_project(root):
        raise ProjectError(f"{root} is not a Thorondor managed project")
    shutil.rmtree(root)
    return root
