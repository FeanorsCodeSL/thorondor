"""Validation screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from ...deploy import compose_args, compose_plan
from ...state import compute_issues, load_draft


class ValidateScreen(Screen[None]):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        draft = load_draft(self.project_dir)
        issues = compute_issues(draft, {})
        plan = compose_plan(self.project_dir, draft)
        lines = ["Validate", "", f"compose: docker {' '.join(compose_args(plan))}"]
        if issues:
            lines.append("")
            lines.append("Issues")
            lines.extend(f"  - {issue.label} → {issue.action}" for issue in issues)
        else:
            lines.append("")
            lines.append(
                "Env completeness checks passed. "
                "Run Deploy to execute compose config/build/up."
            )
        yield Static("\n".join(lines))

    def action_cancel(self) -> None:
        self.app.pop_screen()
