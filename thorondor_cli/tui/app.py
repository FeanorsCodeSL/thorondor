"""Textual application for the Thorondor configurator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual.app import App

from ..harness import detect_wired_harnesses
from ..state import Draft, Issue, compute_issues, load_draft
from .screens.dashboard import DashboardScreen

HarnessStatus = dict[str, bool]
DraftLoader = Callable[..., Draft]


class ThorondorApp(App[int]):
    """Full-screen Thorondor configuration dashboard."""

    CSS_PATH = "thorondor.tcss"
    TITLE = "thorondor"
    SUB_TITLE = "Search Stack Configurator"

    def __init__(
        self,
        project_dir: str | Path,
        *,
        home: str | Path | None = None,
        harness_status: HarnessStatus | None = None,
        draft_loader: DraftLoader = load_draft,
    ) -> None:
        super().__init__()
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self._harness_status = harness_status
        self._draft_loader = draft_loader
        self.draft: Draft | None = None
        self.issues: list[Issue] = []
        self.harness_status: HarnessStatus = {
            "claude-code": False,
            "codex": False,
            "opencode": False,
        }

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(self.project_dir, dashboard=self))

    def refresh_dashboard_state(self) -> None:
        self.draft = self._draft_loader(self.project_dir)
        self.harness_status = self._detect_harnesses()
        self.issues = compute_issues(self.draft, self.harness_status)

    def _detect_harnesses(self) -> HarnessStatus:
        if self._harness_status is not None:
            return dict(self._harness_status)
        return detect_wired_harnesses(self.home)
