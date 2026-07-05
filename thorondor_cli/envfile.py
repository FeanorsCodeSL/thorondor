"""Dotenv parsing and merge-not-clobber writing for the Thorondor CLI."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path

ENV_TEMPLATE = "env.example"
LLAMACPP_TEMPLATE = "env.llamacpp.example"
PRODUCTION_TEMPLATE = "env.production.example"
TEMPLATE_FILENAMES = {
    ENV_TEMPLATE: ".env.example",
    LLAMACPP_TEMPLATE: ".env.llamacpp.example",
    PRODUCTION_TEMPLATE: ".env.production.example",
}

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _template_repo_path(project_dir: Path | None, template_name: str) -> Path | None:
    if project_dir is None:
        return None
    filename = TEMPLATE_FILENAMES[template_name]
    candidate = Path(project_dir).expanduser().resolve() / filename
    return candidate if candidate.exists() else None


def template_text(template_name: str, project_dir: str | Path | None = None) -> str:
    """Return template content, preferring repo-root copies when present."""
    project_path = Path(project_dir).expanduser().resolve() if project_dir else None
    repo_path = _template_repo_path(project_path, template_name)
    if repo_path is not None:
        return repo_path.read_text(encoding="utf-8")
    return (
        resources.files("thorondor_cli")
        .joinpath("templates")
        .joinpath(template_name)
        .read_text(encoding="utf-8")
    )


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.strip()


def _unquote(value: str) -> str:
    value = _strip_inline_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            return bytes(inner, "utf-8").decode("unicode_escape")
        return inner
    return value


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        values[key] = _unquote(value.strip())
    return values


def read_env(path: str | Path) -> dict[str, str]:
    target = Path(path).expanduser()
    if not target.exists():
        return {}
    return parse_env_text(target.read_text(encoding="utf-8"))


def seed_from_example(
    example_path: str | Path | None = None,
    *,
    template_name: str = ENV_TEMPLATE,
    project_dir: str | Path | None = None,
) -> dict[str, str]:
    """Read every key/default from an env template."""
    if example_path is not None:
        return parse_env_text(Path(example_path).expanduser().read_text(encoding="utf-8"))
    return parse_env_text(template_text(template_name, project_dir=project_dir))


def required_keys(
    *,
    template_name: str = ENV_TEMPLATE,
    project_dir: str | Path | None = None,
) -> set[str]:
    return set(seed_from_example(template_name=template_name, project_dir=project_dir))


def _quote(value: str) -> str:
    if value == "":
        return ""
    needs_quoting = any(char.isspace() for char in value) or "#" in value or any(
        char in value for char in "\"'"
    )
    if not needs_quoting:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ordered_keys(
    template_values: Mapping[str, str] | None,
    existing: Mapping[str, str],
    merged: Mapping[str, str],
) -> list[str]:
    keys: list[str] = []
    if template_values is not None:
        keys.extend(template_values.keys())
    keys.extend(key for key in existing if key not in keys)
    keys.extend(key for key in merged if key not in keys)
    return keys


def write_env(
    path: str | Path,
    values: Mapping[str, str],
    *,
    mode: int = 0o600,
    template_values: Mapping[str, str] | None = None,
    preserve_existing_nonblank: Iterable[str] = (),
) -> Path:
    """Merge values into a dotenv file, preserving unrelated keys and setting 0600."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = read_env(target)
    merged = dict(existing)
    for key, value in values.items():
        if key in preserve_existing_nonblank and existing.get(key, "").strip():
            continue
        merged[key] = str(value)
    if template_values is not None:
        for key, value in template_values.items():
            merged.setdefault(key, value)

    lines = [
        f"{key}={_quote(merged.get(key, ''))}"
        for key in _ordered_keys(template_values, existing, merged)
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(target, mode)
    except OSError:
        pass
    return target
