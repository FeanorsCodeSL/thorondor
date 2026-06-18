"""Search and crawl settings screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from ...state import ConfigAnswers, load_draft, persist_env_changes


class SearchSettingsScreen(Screen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    FIELDS = (
        "ORCHESTRATOR_HOST",
        "ORCHESTRATOR_PORT",
        "DEFAULT_TOKEN_BUDGET",
        "MAX_URLS",
        "CRAWL_CONCURRENCY",
        "CRAWL_PER_HOST_CONCURRENCY",
        "CRAWL_TIMEOUT_S",
        "CRAWL_RESPECT_ROBOTS_TXT",
        "DOMAIN_ALLOWLIST",
        "DOMAIN_BLOCKLIST",
        "ALLOWLIST_ONLY",
    )

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        draft = load_draft(self.project_dir)
        yield Static("Search/crawl", classes="screen-title")
        for field in self.FIELDS:
            yield Input(draft.env.get(field, ""), id=field.lower(), placeholder=field)
        yield Button("Save", id="save")

    def _value(self, key: str) -> str:
        return self.query_one(f"#{key.lower()}", Input).value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            return
        draft = load_draft(self.project_dir)
        overrides = {field: self._value(field) for field in self.FIELDS}
        answers = ConfigAnswers(
            mode=draft.mode,
            embedding_endpoint=draft.env.get("EMBEDDING_ENDPOINT", ""),
            embedding_model=draft.env.get("EMBEDDING_MODEL", ""),
            embedding_api_key=draft.env.get("EMBEDDING_API_KEY", ""),
            reranker_endpoint=draft.env.get("RERANKER_ENDPOINT", ""),
            reranker_model=draft.env.get("RERANKER_MODEL", ""),
            reranker_path=draft.env.get("RERANKER_PATH", "/rerank"),
            reranker_health_path=draft.env.get("RERANKER_HEALTH_PATH", "/health"),
            reranker_api_key=draft.env.get("RERANKER_API_KEY", ""),
            llm_endpoint=draft.env.get("LLM_ENDPOINT", ""),
            llm_model=draft.env.get("LLM_MODEL", ""),
            llm_api_key=draft.env.get("LLM_API_KEY", ""),
            orchestrator_host=overrides["ORCHESTRATOR_HOST"],
            orchestrator_port=overrides["ORCHESTRATOR_PORT"],
            search_overrides=overrides,
            llamacpp_overrides=draft.llamacpp_env,
        )
        persist_env_changes(self.project_dir, answers)
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()
