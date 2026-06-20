"""Wire Claude Code to Thorondor MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .common import MCP_NAME, DeliveryMode, entry_for_delivery, merge_json

Scope = Literal["global", "workspace"]


def claude_code_path(base: Path, scope: Scope) -> Path:
    base = Path(base).expanduser()
    return base / ".claude.json" if scope == "global" else base / ".mcp.json"


def wire_claude_code(base: Path, scope: Scope = "global", delivery: DeliveryMode = "stdio") -> Path:
    path = claude_code_path(base, scope)

    def mutate(data: dict) -> None:
        servers = data.setdefault("mcpServers", {})
        servers[MCP_NAME] = entry_for_delivery(delivery)

    return merge_json(path, mutate)
