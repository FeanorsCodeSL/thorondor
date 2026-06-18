"""Deploy progress screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Static

from ...deploy import DeployError, deploy


class DeployScreen(Screen[None]):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        yield Static("Deploy", classes="screen-title")
        yield Static("config → build → up -d → /healthz → smoke")
        yield Button("Start deploy", id="start")
        yield Static("", id="deploy-log")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "start":
            return
        log = self.query_one("#deploy-log", Static)
        lines: list[str] = []
        try:
            for progress in deploy(self.project_dir):
                lines.append(f"{progress.step}: {progress.message}")
                log.update("\n".join(lines))
        except DeployError as exc:
            lines.append(f"failed: {exc}")
            lines.append("Inspect `docker compose ps` and service logs from the project root.")
            log.update("\n".join(lines))
            return
        lines.append("deploy complete")
        log.update("\n".join(lines))

    def action_cancel(self) -> None:
        self.app.pop_screen()
