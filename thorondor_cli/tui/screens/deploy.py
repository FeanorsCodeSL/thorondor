"""Deploy progress screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from ...deploy import DeployError, deploy
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin


class DeployScreen(ArrowNavigationMixin, Screen[None]):
    """Run the Compose config/build/up/health/smoke sequence."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Back")]

    def __init__(
        self,
        project_dir: str | Path,
        *,
        dashboard: object | None = None,
    ) -> None:
        super().__init__()
        self.project_dir = Path(project_dir)
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static("Deploy", id="deploy-title", classes="brand")
        with Vertical(id="deploy-body"):
            yield Static("config → build → up -d → /health → smoke", classes="status")
            yield Static("", id="deploy-log", classes="status")
            with Horizontal(classes="form-row"):
                yield Button("Start deploy", id="start-deploy", variant="primary")
                yield Button("Back", id="back-deploy")

    def on_mount(self) -> None:
        self.query_one("#start-deploy", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-deploy":
            self.action_run()
        elif event.button.id == "back-deploy":
            self.action_cancel()

    def action_run(self) -> None:
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
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
