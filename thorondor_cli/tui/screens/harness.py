"""MCP harness wiring screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Select, Static

from ...harness import wire_claude_code, wire_codex, wire_opencode
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

DELIVERY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("stdio · native thorondor-mcp", "stdio"),
    ("http · Docker /mcp", "http"),
)


class HarnessScreen(ArrowNavigationMixin, Screen[None]):
    """Wire Claude Code / Codex / OpenCode to Thorondor's MCP entry."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    def __init__(
        self,
        project_dir: str | Path | None = None,
        *,
        dashboard: object | None = None,
    ) -> None:
        super().__init__()
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield Static("Wire MCP", id="harness-mcp-title", classes="brand")
        with Vertical(id="harness-mcp-form"):
            yield Static("claude-code", classes="field-label")
            yield Checkbox("Wire ~/.claude.json", id="harness-claude-code")
            yield Static("codex", classes="field-label")
            yield Checkbox("Wire ~/.codex/config.toml", id="harness-codex")
            yield Static("opencode", classes="field-label")
            yield Checkbox(
                "Wire ~/.config/opencode/opencode.json", id="harness-opencode"
            )
            yield Static("Delivery", classes="field-label")
            yield Select(
                list(DELIVERY_OPTIONS),
                value="stdio",
                allow_blank=False,
                id="harness-delivery",
            )
            with Horizontal(classes="form-row"):
                yield Button("Apply", id="apply-harnesses", variant="primary")
                yield Button("Cancel", id="cancel-harnesses")
            yield Static("", id="harness-mcp-status")

    def on_mount(self) -> None:
        status = self.app._detect_harnesses()  # noqa: SLF001 - mirrored from Imladris
        self.query_one("#harness-claude-code", Checkbox).value = status.get(
            "claude-code", False
        )
        self.query_one("#harness-codex", Checkbox).value = status.get("codex", False)
        self.query_one("#harness-opencode", Checkbox).value = status.get(
            "opencode", False
        )
        self.query_one("#harness-claude-code", Checkbox).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-harnesses":
            self.action_apply()
        elif event.button.id == "cancel-harnesses":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_apply(self) -> None:
        home = self.app.home
        delivery = self._delivery_value()
        try:
            if self.query_one("#harness-claude-code", Checkbox).value:
                wire_claude_code(home, "global", delivery=delivery)  # type: ignore[arg-type]
            if self.query_one("#harness-codex", Checkbox).value:
                wire_codex(home, delivery=delivery)  # type: ignore[arg-type]
            if self.query_one("#harness-opencode", Checkbox).value:
                wire_opencode(home, delivery=delivery)  # type: ignore[arg-type]
        except Exception as exc:
            self.query_one("#harness-mcp-status", Static).update(str(exc))
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _delivery_value(self) -> str:
        from textual.widgets import Select

        value = self.query_one("#harness-delivery", Select).value
        return "stdio" if value is Select.NULL else str(value)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
