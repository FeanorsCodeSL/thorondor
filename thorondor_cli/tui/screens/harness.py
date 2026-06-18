"""MCP harness wiring screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Static

from ...harness import wire_claude_code, wire_codex, wire_opencode


class HarnessScreen(Screen[None]):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        yield Static("MCP", classes="screen-title")
        yield Static("Wire harnesses to native stdio `thorondor-mcp` or Docker HTTP `/mcp`.")
        yield Button("Wire Codex stdio", id="codex-stdio")
        yield Button("Wire Claude Code stdio", id="claude-stdio")
        yield Button("Wire OpenCode stdio", id="opencode-stdio")
        yield Button("Wire Codex HTTP", id="codex-http")
        yield Static("", id="harness-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        home = Path.home()
        target = event.button.id or ""
        delivery = "http" if target.endswith("http") else "stdio"
        if target.startswith("codex"):
            path = wire_codex(home, delivery=delivery)  # type: ignore[arg-type]
        elif target.startswith("claude"):
            path = wire_claude_code(home, delivery=delivery)  # type: ignore[arg-type]
        else:
            path = wire_opencode(home, delivery=delivery)  # type: ignore[arg-type]
        self.query_one("#harness-status", Static).update(f"wrote {path}")

    def action_cancel(self) -> None:
        self.app.pop_screen()
