"""Textual application for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from .screens.dashboard import DashboardScreen


class ThorondorApp(App[None]):
    """Full-screen Thorondor configuration dashboard."""

    CSS_PATH = "thorondor.tcss"
    TITLE = "thorondor"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "dashboard", "Dashboard"),
    ]

    def __init__(self, project_dir: str | Path):
        super().__init__()
        self.project_dir = Path(project_dir).expanduser().resolve()

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(self.project_dir))

    def action_dashboard(self) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()
