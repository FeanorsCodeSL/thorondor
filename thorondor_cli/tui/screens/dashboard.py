"""Dashboard screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, OptionList, Static

from ...state import Draft, Issue, compute_issues, load_draft

BRAND_TITLE = "thorondor"
BRAND_SUBTITLE = "Search Stack Configurator"
BRAND_LEFT_MARK = "Fëanor's Code"
BRAND_RIGHT_MARK = "O(log n)"

NODE = "◆"
ACCENT = "✦"

WORDMARK: tuple[str, ...] = (
    " _   _                        _         ",
    "| |_| |_  ___ _ _ ___ _ _  __| |___ _ _ ",
    "|  _| ' \\/ _ \\ '_/ _ \\ ' \\/ _` / _ \\ '_|",
    " \\__|_||_\\___/_| \\___/_||_\\__,_\\___/_|  ",
)

DashboardAction = tuple[str, str]

SEL_HARNESS = "#harness"
SEL_COMPONENTS = "#components"
SEL_ACTION_MENU = "#action-menu"
SEL_DASHBOARD_MAIN = "#dashboard-main"
SEL_DASHBOARD_NAV = "#dashboard-nav"
SEL_BRAND = "#brand"
SEL_PATHS = "#paths"
SEL_ISSUES = "#issues"


class DashboardScreen(Screen[None]):
    """Read-only dashboard for the current project draft."""

    BINDINGS = [
        Binding("left", "focus_actions", show=False, priority=True),
        Binding("right", "focus_components", show=False, priority=True),
    ]

    def __init__(
        self,
        project_dir: str | Path,
        *,
        dashboard: object | None = None,
    ) -> None:
        super().__init__()
        self.project_dir = Path(project_dir)
        self._dashboard = dashboard
        self._action_menu_actions: list[DashboardAction] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            yield Static("", id="brand", classes="brand", markup=False)
            yield Static("", id="paths", classes="meta")
            with Horizontal(id="dashboard-body"):
                with Vertical(id="dashboard-nav"):
                    yield OptionList(id="action-menu", markup=False, compact=True)
                    yield Static("", id="harness", classes="meta")
                with Vertical(id="dashboard-main"):
                    yield Static("Components", id="components-title", classes="meta")
                    yield DataTable(id="components")
                    yield Static("", id="issues", classes="status")

    def on_mount(self) -> None:
        self._decorate_frames()
        self.refresh_dashboard()
        self._focus_default_widget()

    def on_screen_resume(self) -> None:
        self.refresh_dashboard()
        self._focus_default_widget()

    def _decorate_frames(self) -> None:
        outer = self.query_one("#dashboard", Vertical)
        outer.border_title = f"{ACCENT}  {BRAND_TITLE}  {ACCENT}"
        outer.border_subtitle = "↑↓ navigate · enter select"
        self.query_one(SEL_DASHBOARD_NAV, Vertical).border_title = f"{NODE} Actions {NODE}"
        self.query_one(SEL_DASHBOARD_MAIN, Vertical).border_title = f"{NODE} Stack {NODE}"

    def refresh_dashboard(self) -> None:
        self.app.refresh_dashboard_state()
        draft = self.app.draft
        if draft is None:
            return

        issues = self.app.issues
        width = self.size.width or 80
        self.query_one(SEL_BRAND, Static).update(self._brand(width))
        self.query_one(SEL_PATHS, Static).update(self._paths(draft))
        self._populate_components(draft, width)
        self.query_one(SEL_ISSUES, Static).update(self._issues_summary(issues))
        self.query_one(SEL_HARNESS, Static).update(self._harness_summary())
        self._populate_action_menu(draft)

    def _focus_default_widget(self) -> None:
        self.query_one(SEL_ACTION_MENU, OptionList).focus()

    def action_focus_actions(self) -> None:
        self.query_one(SEL_ACTION_MENU, OptionList).focus()

    def action_focus_components(self) -> None:
        self.query_one(SEL_COMPONENTS, DataTable).focus()

    def _brand(self, width: int) -> str:
        inner = max(20, width - 6)
        word_width = max(len(line) for line in WORDMARK)
        if inner < word_width + 2:
            return self._compact_brand(inner)

        offset = max(0, (inner - word_width) // 2)
        lines = [(" " * offset) + line for line in WORDMARK]
        lines.append(f"{ACCENT} {BRAND_SUBTITLE} {ACCENT}".center(inner))
        if width < 86:
            lines.append(self._inline_marks().center(inner))
        else:
            lines.append("")
            lines.extend(self._mark_boxes(inner))
        lines.append(self._divider(inner))
        return "\n".join(lines)

    def _compact_brand(self, inner: int) -> str:
        return "\n".join(
            [
                BRAND_TITLE.center(inner),
                f"{ACCENT} {BRAND_SUBTITLE} {ACCENT}".center(inner),
                self._inline_marks().center(inner),
                self._divider(inner),
            ]
        )

    def _inline_marks(self) -> str:
        left = f"{NODE} {BRAND_LEFT_MARK} {NODE}"
        right = f"{NODE} {BRAND_RIGHT_MARK} {NODE}"
        return f"{left}     {right}"

    def _mark_boxes(self, inner: int) -> list[str]:
        left = self._mark_box(BRAND_LEFT_MARK, 26)
        right = self._mark_box(BRAND_RIGHT_MARK, 20)
        gap = 8
        return [
            f"{lhs}{' ' * gap}{rhs}".center(inner) for lhs, rhs in zip(left, right, strict=True)
        ]

    def _mark_box(self, label: str, width: int) -> tuple[str, str, str]:
        inner_width = max(0, width - 2)
        bar = "═" * inner_width
        return (f"╔{bar}╗", f"║{label.center(inner_width)}║", f"╚{bar}╝")

    def _divider(self, inner: int) -> str:
        span = max(0, inner - 3)
        left = span // 2
        right = span - left
        return f"{ACCENT}{'─' * left}{NODE}{'─' * right}{ACCENT}"

    def _paths(self, draft: Draft) -> str:
        env_state = "present" if draft.env_exists else "missing"
        return (
            f"project: {draft.project_dir}   "
            f"env: .env ({env_state})   "
            f"mode: {draft.mode}   "
            f"profile: {draft.profile or '(none)'}"
        )

    def _populate_components(self, draft: Draft, width: int) -> None:
        table = self.query_one(SEL_COMPONENTS, DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("component", "endpoint", "model", "status", "notes")
        endpoint_width = 36 if width < 100 else 48
        model_width = 18 if width < 100 else 28
        rows = self._component_rows(draft, endpoint_width, model_width)
        for row in rows:
            table.add_row(*row)

    def _component_rows(
        self, draft: Draft, endpoint_width: int, model_width: int
    ) -> list[tuple[str, str, str, str, str]]:
        env = draft.env
        rows: list[tuple[str, str, str, str, str]] = []
        rows.append(
            (
                "searxng",
                self._truncate(env.get("SEARXNG_URL", ""), endpoint_width),
                "-",
                "configured",
                "internal",
            )
        )
        rows.append(
            (
                "crawl4ai",
                self._truncate(env.get("CRAWL4AI_URL", ""), endpoint_width),
                "-",
                "configured",
                "proxied",
            )
        )
        rows.append(
            (
                "chunker",
                self._truncate(env.get("CHUNKER_URL", ""), endpoint_width),
                "-",
                "configured",
                "internal",
            )
        )
        rows.append(
            (
                "embedding",
                self._truncate(env.get("EMBEDDING_ENDPOINT", ""), endpoint_width),
                self._truncate(env.get("EMBEDDING_MODEL", ""), model_width),
                "overlay" if draft.overlay_needed else "configured",
                "host rewritten" if draft.host_rewritten else "",
            )
        )
        rows.append(
            (
                "reranker",
                self._truncate(env.get("RERANKER_ENDPOINT", ""), endpoint_width),
                self._truncate(env.get("RERANKER_MODEL", ""), model_width),
                "configured",
                "host rewritten" if draft.host_rewritten else "",
            )
        )
        llm_endpoint = env.get("LLM_ENDPOINT", "")
        rows.append(
            (
                "llm planner",
                self._truncate(llm_endpoint or "—", endpoint_width),
                self._truncate(env.get("LLM_MODEL", ""), model_width),
                "enabled" if llm_endpoint else "disabled",
                "optional",
            )
        )
        return rows

    def _populate_action_menu(self, draft: Draft) -> None:
        actions = self._available_actions(draft)
        self._action_menu_actions = actions
        menu = self.query_one(SEL_ACTION_MENU, OptionList)
        menu.clear_options()
        menu.add_options(label for _, label in actions)
        menu.disabled = not actions
        if actions:
            menu.highlighted = 0

    def _available_actions(self, draft: Draft) -> list[DashboardAction]:
        if draft.missing_project_paths:
            return [("wire", "Wire MCP"), ("quit", "Quit")]

        return [
            ("mode", "Mode"),
            ("endpoints", "Endpoints"),
            ("search", "Search/crawl"),
            ("validate", "Validate"),
            ("deploy", "Deploy"),
            ("wire", "Wire MCP"),
            ("refresh", "Refresh state"),
            ("quit", "Quit"),
        ]

    def _issues_summary(self, issues: list[Issue]) -> str:
        missing_gguf = self._missing_gguf_summary()
        if not issues and not missing_gguf:
            draft = self.app.draft
            env_exists = bool(draft and draft.env_exists)
            env_state = "✓ ready" if env_exists else "✗ env missing"
            return f"{NODE} Status   {env_state}"
        ordered = sorted(issues, key=lambda issue: issue.action != "Manual repair")
        header = (
            f"{NODE} Status   {len(issues)} issue{'s' if len(issues) != 1 else ''}"
            if issues
            else f"{NODE} Status"
        )
        lines = [header]
        if missing_gguf:
            for line in missing_gguf:
                lines.append(f"  - {line}")
        for issue in ordered[: 4 - len(missing_gguf)]:
            lines.append(f"  - {issue.label} -> {issue.action}")
        if len(ordered) > 4 - len(missing_gguf):
            remaining = len(ordered) - (4 - len(missing_gguf))
            if remaining > 0:
                lines.append(f"  - +{remaining} more")
        return "\n".join(lines)

    def _missing_gguf_summary(self) -> list[str]:
        draft = self.app.draft
        if draft is None or draft.mode != "llamacpp":
            return []
        from ...models import llamacpp_models_status

        _present, missing = llamacpp_models_status(self.project_dir, draft.llamacpp_env)
        if not missing:
            return []
        joined = ", ".join(m.filename for m in missing)
        return [f"GGUF model files not downloaded: {joined} -> Mode to fetch"]

    def _harness_summary(self) -> str:
        status = self.app.harness_status
        return (
            f"{NODE} Harness  claude-code {self._yes_no(status.get('claude-code', False))}    "
            f"codex {self._yes_no(status.get('codex', False))}    "
            f"opencode {self._yes_no(status.get('opencode', False))}"
        )

    def _yes_no(self, value: bool) -> str:
        return "yes" if value else "no"

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: max(0, limit - 3)]}..."

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "action-menu":
            return
        try:
            action_id = self._action_menu_actions[event.option_index][0]
        except IndexError:
            return
        self._run_action_menu_item(action_id)

    def _run_action_menu_item(self, action_id: str) -> None:
        if action_id == "mode":
            self.action_mode()
        elif action_id == "endpoints":
            self.action_endpoints()
        elif action_id == "search":
            self.action_search()
        elif action_id == "validate":
            self.action_validate()
        elif action_id == "deploy":
            self.action_deploy()
        elif action_id == "wire":
            self.action_harness()
        elif action_id == "refresh":
            self.refresh_dashboard()
        elif action_id == "quit":
            self.app.exit(0)

    def action_mode(self) -> None:
        from .model_mode import ModelModeScreen

        self.app.push_screen(ModelModeScreen(self.project_dir, dashboard=self))

    def action_endpoints(self) -> None:
        from .endpoint_form import EndpointFormScreen

        self.app.push_screen(EndpointFormScreen(self.project_dir, dashboard=self))

    def action_search(self) -> None:
        from .search_settings import SearchSettingsScreen

        self.app.push_screen(SearchSettingsScreen(self.project_dir, dashboard=self))

    def action_validate(self) -> None:
        from .validate import ValidateScreen

        self.app.push_screen(ValidateScreen(self.project_dir, dashboard=self))

    def action_deploy(self) -> None:
        from .deploy import DeployScreen

        self.app.push_screen(DeployScreen(self.project_dir, dashboard=self))

    def action_harness(self) -> None:
        from .harness import HarnessScreen

        self.app.push_screen(HarnessScreen(dashboard=self))


def compute_dashboard_issues(
    draft: Draft, harness_status: dict[str, bool] | None = None
) -> list[Issue]:
    """Re-export of :func:`thorondor_cli.state.compute_issues` for parity with old tests."""
    return compute_issues(draft, harness_status)


def load_dashboard_draft(project_dir: str | Path) -> Draft:
    """Re-export of :func:`thorondor_cli.state.load_draft` for parity with old tests."""
    return load_draft(project_dir)
