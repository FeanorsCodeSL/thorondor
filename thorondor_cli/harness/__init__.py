"""Harness config writers and detection for Thorondor MCP delivery."""

from __future__ import annotations

from pathlib import Path

from .claude_code import claude_code_path, wire_claude_code
from .codex import codex_path, wire_codex
from .opencode import opencode_path, wire_opencode

__all__ = [
    "claude_code_path",
    "codex_path",
    "detect_wired_harnesses",
    "opencode_path",
    "wire_claude_code",
    "wire_codex",
    "wire_opencode",
]


def _has_thorondor_entry(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return "thorondor" in path.read_text(encoding="utf-8")
    except OSError:
        return False


def detect_wired_harnesses(home: Path) -> dict[str, bool]:
    """Best-effort detection of which harnesses already carry a thorondor entry."""
    home = Path(home).expanduser()
    return {
        "claude-code": _has_thorondor_entry(claude_code_path(home, "global")),
        "codex": _has_thorondor_entry(codex_path(home)),
        "opencode": _has_thorondor_entry(opencode_path(home)),
    }
