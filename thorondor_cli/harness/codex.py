"""Wire Codex to Thorondor MCP in ``~/.codex/config.toml``."""

from __future__ import annotations

from pathlib import Path

from .common import DEFAULT_BASE_URL, DEFAULT_HTTP_URL, DeliveryMode, MCP_COMMAND, backup

_HEADER = "[mcp_servers.thorondor]"


def codex_path(home: Path) -> Path:
    return Path(home).expanduser() / ".codex" / "config.toml"


def _block(delivery: DeliveryMode) -> list[str]:
    if delivery == "http":
        return [_HEADER, 'type = "http"', f'url = "{DEFAULT_HTTP_URL}"']
    return [
        _HEADER,
        f'command = "{MCP_COMMAND}"',
        f'env = {{ THORONDOR_BASE_URL = "{DEFAULT_BASE_URL}" }}',
    ]


def wire_codex(home: Path, delivery: DeliveryMode = "stdio") -> Path:
    path = codex_path(home)
    block = _block(delivery)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(block) + "\n", encoding="utf-8")
        return path
    backup(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == _HEADER), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
    else:
        end = start + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        lines[start:end] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
