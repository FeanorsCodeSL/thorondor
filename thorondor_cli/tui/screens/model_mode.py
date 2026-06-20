"""Mode selection screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from ...state import ConfigAnswers, load_draft, persist_env_changes
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin


class ModelModeScreen(ArrowNavigationMixin, Screen[None]):
    """Choose the model delivery mode (BYO / bundled / llama.cpp)."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

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
        draft = load_draft(self.project_dir)
        yield Static("Mode", id="mode-title", classes="brand")
        with Vertical(id="mode-form"):
            yield Static(
                f"Current mode: {draft.mode}\n"
                "Choose the model delivery mode to write complete env values.",
                classes="status",
            )
            with Horizontal(classes="form-row"):
                yield Button("BYO endpoints", id="byo", variant="primary")
                yield Button("bundled-models (TEI)", id="bundled")
                yield Button("llamacpp (GGUF)", id="llamacpp")
            with Horizontal(classes="form-row"):
                yield Button("Apply", id="apply-mode")
                yield Button("Cancel", id="cancel-mode")
            yield Static("", id="mode-status")

    def on_mount(self) -> None:
        self.query_one("#byo", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-mode":
            self.action_apply_mode()
        elif event.button.id == "cancel-mode":
            self.action_cancel()
        elif event.button.id in {"byo", "bundled", "llamacpp"}:
            self._save(self._mode_for_button(event.button.id or ""))

    def action_apply_mode(self) -> None:
        self._save("byo")

    def _mode_for_button(self, button_id: str) -> str:
        return {"byo": "byo", "bundled": "bundled-models", "llamacpp": "llamacpp"}.get(
            button_id, "byo"
        )

    def _save(self, mode: str) -> None:
        try:
            persist_env_changes(self.project_dir, self._answers(mode))
        except Exception as exc:
            self.query_one("#mode-status", Static).update(f"Cannot save: {exc}")
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _answers(self, mode: str) -> ConfigAnswers:
        draft = load_draft(self.project_dir)
        return ConfigAnswers(
            mode=mode,  # type: ignore[arg-type]
            embedding_endpoint=draft.env.get("EMBEDDING_ENDPOINT", "http://embedding:80"),
            embedding_model=draft.env.get("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_api_key=draft.env.get("EMBEDDING_API_KEY", ""),
            reranker_endpoint=draft.env.get("RERANKER_ENDPOINT", "http://reranker:80"),
            reranker_model=draft.env.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
            reranker_path=draft.env.get("RERANKER_PATH", "/rerank"),
            reranker_health_path=draft.env.get("RERANKER_HEALTH_PATH", "/health"),
            reranker_api_key=draft.env.get("RERANKER_API_KEY", ""),
            llm_endpoint=draft.env.get("LLM_ENDPOINT", ""),
            llm_model=draft.env.get("LLM_MODEL", ""),
            llm_api_key=draft.env.get("LLM_API_KEY", ""),
            orchestrator_host=draft.env.get("ORCHESTRATOR_HOST", "127.0.0.1"),
            orchestrator_port=draft.env.get("ORCHESTRATOR_PORT", "8080"),
            llamacpp_overrides=draft.llamacpp_env,
        )

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
