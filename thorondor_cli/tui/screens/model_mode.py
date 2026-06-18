"""Mode selection screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Static

from ...state import ConfigAnswers, load_draft, persist_env_changes


class ModelModeScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("b", "save_bundled", "Bundled"),
        Binding("l", "save_llamacpp", "llama.cpp"),
        Binding("y", "save_byo", "BYO"),
    ]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        draft = load_draft(self.project_dir)
        yield Static("Mode", classes="screen-title")
        yield Static(
            f"Current mode: {draft.mode}\n"
            "Choose the model delivery mode to write complete env values."
        )
        yield Button("BYO endpoints", id="byo")
        yield Button("bundled-models (TEI containers)", id="bundled")
        yield Button("llamacpp (GGUF under models/)", id="llamacpp")
        yield Static("", id="mode-status")

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

    def _save(self, mode: str) -> None:
        try:
            persist_env_changes(self.project_dir, self._answers(mode))
        except Exception as exc:
            self.query_one("#mode-status", Static).update(f"Cannot save: {exc}")
            return
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mode_by_button = {"byo": "byo", "bundled": "bundled-models", "llamacpp": "llamacpp"}
        self._save(mode_by_button.get(event.button.id or "", "byo"))

    def action_save_bundled(self) -> None:
        self._save("bundled-models")

    def action_save_llamacpp(self) -> None:
        self._save("llamacpp")

    def action_save_byo(self) -> None:
        self._save("byo")

    def action_cancel(self) -> None:
        self.app.pop_screen()
