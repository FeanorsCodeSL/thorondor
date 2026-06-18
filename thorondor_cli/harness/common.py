"""Shared helpers for harness writers."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

DeliveryMode = Literal["stdio", "http"]

MCP_NAME = "thorondor"
MCP_COMMAND = "thorondor-mcp"
DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_HTTP_URL = "http://localhost:8080/mcp"


def stdio_entry(base_url: str = DEFAULT_BASE_URL) -> dict:
    return {"command": MCP_COMMAND, "env": {"THORONDOR_BASE_URL": base_url}}


def http_entry(url: str = DEFAULT_HTTP_URL) -> dict:
    return {"type": "http", "url": url}


def entry_for_delivery(delivery: DeliveryMode) -> dict:
    return stdio_entry() if delivery == "stdio" else http_entry()


def backup(path: Path) -> Path | None:
    if path.exists():
        target = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, target)
        return target
    return None


def merge_json(path: Path, mutate: Callable[[dict], None]) -> Path:
    data: dict = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
        backup(path)
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
