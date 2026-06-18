"""Dashboard screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Static

from ...state import Draft, compute_issues, load_draft
from .deploy import DeployScreen
from .endpoint_form import EndpointFormScreen
from .harness import HarnessScreen
from .model_mode import ModelModeScreen
from .search_settings import SearchSettingsScreen
from .validate import ValidateScreen


class DashboardScreen(Screen[None]):
    BINDINGS = [
        Binding("m", "mode", "Mode"),
        Binding("e", "endpoints", "Endpoints"),
        Binding("s", "search", "Search/crawl"),
        Binding("v", "validate", "Validate"),
        Binding("d", "deploy", "Deploy"),
        Binding("w", "harness", "MCP"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        yield Container(
            Static("", id="brand-header"),
            Static("", id="dashboard-body"),
            Static(
                "❧ [1] [m] Mode  [2] [e] Endpoints  [3] [s] Search/crawl  "
                "[4] [v] Validate  [5] [d] Deploy  [6] [w] MCP  [q] Quit ☙",
                id="action-bar",
            ),
            Footer(),
            id="dashboard",
        )

    def on_mount(self) -> None:
        self.refresh_dashboard()

    def on_screen_resume(self) -> None:
        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        draft = load_draft(self.project_dir)
        width = max(72, min(self.app.size.width - 4, 112))
        self.query_one("#brand-header", Static).update(render_header(width))
        self.query_one("#dashboard-body", Static).update(render_dashboard(draft, width))

    def action_mode(self) -> None:
        self.app.push_screen(ModelModeScreen(self.project_dir))

    def action_endpoints(self) -> None:
        self.app.push_screen(EndpointFormScreen(self.project_dir))

    def action_search(self) -> None:
        self.app.push_screen(SearchSettingsScreen(self.project_dir))

    def action_validate(self) -> None:
        self.app.push_screen(ValidateScreen(self.project_dir))

    def action_deploy(self) -> None:
        self.app.push_screen(DeployScreen(self.project_dir))

    def action_harness(self) -> None:
        self.app.push_screen(HarnessScreen(self.project_dir))


def _short(value: str, width: int = 42) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _fit(value: str, width: int) -> str:
    if len(value) > width:
        return value[: width - 1] + "…"
    return value.ljust(width)


def _frame_line(value: str, width: int) -> str:
    return f"║ {_fit(value, width - 4)} ║"


def _frame_rule(width: int, glyph: str = "─") -> str:
    return f"╟─❖{glyph * (width - 6)}❖─╢"


def _frame_title(title: str, width: int) -> str:
    ornament = f"☙ {title} ❧"
    side = max(1, (width - len(ornament) - 2) // 2)
    line = f"{'─' * side}{ornament}{'─' * side}"
    return _frame_line(line[: width - 4], width)


def render_header(width: int) -> str:
    inner = width - 2
    top = f"╔═◈{'═' * (inner - 4)}◈═╗"
    bottom = f"╚═◈{'═' * (inner - 4)}◈═╝"
    narrow_flourish = "      ❧" + ("─" * 7) + " ◈  thorondor  ◈ " + ("─" * 7) + "❧"
    wide_top = "          ❧" + ("─" * 15) + " ◈ " + ("─" * 15) + "❧"
    wide_bottom = "          ☙" + ("─" * 15) + " ◆ " + ("─" * 15) + "☙"
    if width < 96:
        lines = [
            top,
            _frame_line(narrow_flourish, width),
            _frame_line("       Search Stack Configurator     ⟦ O(log n) ⟧", width),
            bottom,
        ]
        return "\n".join(lines)

    return "\n".join(
        [
            top,
            _frame_line(wide_top, width),
            _frame_line("                 thorondor", width),
            _frame_line(
                "      Search Stack Configurator     Fëanor's Code     ⟦ O(log n) ⟧",
                width,
            ),
            _frame_line(wide_bottom, width),
            bottom,
        ]
    )


def render_dashboard(draft: Draft, width: int = 100) -> str:
    issues = compute_issues(draft, {})
    endpoint_width = 38 if width >= 96 else 28
    model_width = 18 if width >= 96 else 12
    dependency_endpoint = endpoint_width - 10
    embedding_endpoint = _short(draft.env.get("EMBEDDING_ENDPOINT", ""), dependency_endpoint)
    reranker_endpoint = _short(draft.env.get("RERANKER_ENDPOINT", ""), dependency_endpoint)
    llm_endpoint = _short(draft.env.get("LLM_ENDPOINT", "") or "—", endpoint_width)
    lines = [
        f"╔═◈{'═' * (width - 6)}◈═╗",
        _frame_line(
            f"project: {_short(str(draft.project_dir), width - 44)}   "
            f"env: .env ({'present' if draft.env_exists else 'missing'})",
            width,
        ),
        _frame_line(f"mode: {draft.mode}   profile: {draft.profile or '(none)'}", width),
        _frame_rule(width),
        _frame_title("Components", width),
        _frame_line(
            "component      endpoint / model                      status      notes",
            width,
        ),
        _frame_line("─" * (width - 4), width),
        _frame_line(
            "searxng        "
            f"{_short(draft.env.get('SEARXNG_URL', ''), endpoint_width):<{endpoint_width}} "
            "configured  internal",
            width,
        ),
        _frame_line(
            "crawl4ai       "
            f"{_short(draft.env.get('CRAWL4AI_URL', ''), endpoint_width):<{endpoint_width}} "
            "configured  proxied",
            width,
        ),
        _frame_line(
            "chunker        "
            f"{_short(draft.env.get('CHUNKER_URL', ''), endpoint_width):<{endpoint_width}} "
            "configured  internal",
            width,
        ),
        _frame_line(
            "embedding      "
            f"{embedding_endpoint:<{dependency_endpoint}} "
            f"· {_short(draft.env.get('EMBEDDING_MODEL', ''), model_width):<{model_width}} "
            f"{'overlay' if draft.overlay_needed else 'configured'}",
            width,
        ),
        _frame_line(
            "reranker       "
            f"{reranker_endpoint:<{dependency_endpoint}} "
            f"· {_short(draft.env.get('RERANKER_MODEL', ''), model_width):<{model_width}} "
            "configured",
            width,
        ),
        _frame_line(
            "llm planner    "
            f"{llm_endpoint:<{endpoint_width}} "
            f"{'enabled' if draft.env.get('LLM_ENDPOINT') else 'disabled'}  optional",
            width,
        ),
        _frame_rule(width),
        _frame_title("Search", width),
        _frame_line(
            "default budget "
            f"{draft.env.get('DEFAULT_TOKEN_BUDGET', '')} · robots "
            f"{draft.env.get('CRAWL_RESPECT_ROBOTS_TXT', '')} · allowlist "
            f"{draft.env.get('ALLOWLIST_ONLY', '')} · max_urls {draft.env.get('MAX_URLS', '')}",
            width,
        ),
        _frame_rule(width),
    ]
    if issues:
        lines.append(_frame_title(f"Status · {len(issues)} issues", width))
        lines.extend(
            _frame_line(f"✦ {issue.label} → {issue.action}", width) for issue in issues[:8]
        )
    else:
        lines.append(
            _frame_line("Status   ✓ Ready · env complete · compose inputs valid", width)
        )
    lines.extend(
        [
            _frame_line(f"Deploy   {draft.last_deploy or 'not run in this session'}", width),
            _frame_line("MCP      stdio thorondor-mcp available    http /mcp available", width),
            f"╚═◈{'═' * (width - 6)}◈═╝",
        ]
    )
    return "\n".join(lines)
