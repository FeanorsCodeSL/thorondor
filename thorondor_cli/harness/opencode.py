"""Wire OpenCode to Thorondor MCP."""

from __future__ import annotations

from pathlib import Path

from .common import MCP_COMMAND, MCP_NAME, DeliveryMode, entry_for_delivery, merge_json


def opencode_path(home: Path) -> Path:
    return Path(home).expanduser() / ".config" / "opencode" / "opencode.json"


def wire_opencode(home: Path, delivery: DeliveryMode = "stdio") -> Path:
    path = opencode_path(home)

    def mutate(data: dict) -> None:
        servers = data.setdefault("mcp", {})
        entry = entry_for_delivery(delivery)
        if delivery == "stdio":
            servers[MCP_NAME] = {
                "type": "local",
                "command": [MCP_COMMAND],
                "environment": entry.get("env", {}),
            }
        else:
            servers[MCP_NAME] = entry

    return merge_json(path, mutate)
