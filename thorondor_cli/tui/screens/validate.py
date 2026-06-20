"""Validation screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from ...deploy import compose_args, compose_plan
from ...state import compute_issues, load_draft
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin


class ValidateScreen(ArrowNavigationMixin, Screen[None]):
    """Run env-completeness checks and show the resolved compose command."""

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
        yield Static("Validate", id="validate-title", classes="brand")
        with Vertical(id="validate-body"):
            yield Static("", id="validate-summary", classes="status")
            yield Static("", id="validate-issues", classes="status")
            yield Static("", id="validate-compose", classes="status")
            with Horizontal(classes="form-row"):
                yield Button("Refresh", id="refresh-validate")
                yield Button("Back", id="back-validate")

    def on_mount(self) -> None:
        self._refresh_content()
        self.query_one("#refresh-validate", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-validate":
            self._refresh_content()
        elif event.button.id == "back-validate":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _refresh_content(self) -> None:
        draft = load_draft(self.project_dir)
        issues = compute_issues(draft, {})
        plan = compose_plan(self.project_dir, draft)
        compose_text = " ".join(["docker", *compose_args(plan)])

        if issues:
            summary = f"{len(issues)} issue{'s' if len(issues) != 1 else ''} found"
            issue_lines = "\n".join(f"  - {issue.label} -> {issue.action}" for issue in issues)
        else:
            summary = "✓ env completeness checks passed"
            issue_lines = "Run Deploy to execute compose config/build/up."

        self.query_one("#validate-summary", Static).update(summary)
        self.query_one("#validate-issues", Static).update(issue_lines)
        self.query_one("#validate-compose", Static).update(f"compose: {compose_text}")
