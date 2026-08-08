"""Search and crawl settings screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from ...state import ConfigAnswers, load_draft, persist_env_changes
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin


class SearchSettingsScreen(ArrowNavigationMixin, Screen[None]):
    """Edit orchestrator and crawl settings for the Thorondor pipeline."""

    BINDINGS = [*ARROW_NAV_BINDINGS, ("escape", "cancel", "Cancel")]

    FIELDS: tuple[str, ...] = (
        "ORCHESTRATOR_HOST",
        "ORCHESTRATOR_PORT",
        "DEFAULT_TOKEN_BUDGET",
        "MAX_URLS",
        "CRAWL_CONCURRENCY",
        "CRAWL_PER_HOST_CONCURRENCY",
        "CRAWL_TIMEOUT_S",
        "CRAWL_RESPECT_ROBOTS_TXT",
        "CRAWLER_USER_AGENT",
        "CRAWLER_ROBOTS_USER_AGENT",
        "SITE_DEFAULT_DELAY_S",
        "SITE_MAX_JITTER_S",
        "SITE_MAX_COOLDOWN_S",
        "ROBOTS_CACHE_TTL_S",
        "MAX_ROBOTS_BYTES",
        "MAX_SITEMAP_BYTES",
        "MAX_SITEMAP_ENTRIES",
        "MAX_SITEMAP_DOCUMENTS",
        "DOMAIN_ALLOWLIST",
        "DOMAIN_BLOCKLIST",
        "ALLOWLIST_ONLY",
    )

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
        yield Static("Search/crawl", id="search-title", classes="brand")
        with Vertical(id="search-form"):
            for field in self.FIELDS:
                yield Static(field, classes="field-label")
                yield Input(
                    draft.env.get(field, ""),
                    id=field.lower(),
                    placeholder=field,
                )
            with Horizontal(classes="form-row"):
                yield Button("Save", id="save-search", variant="primary")
                yield Button("Cancel", id="cancel-search")
            yield Static("", id="search-status")

    def on_mount(self) -> None:
        self.query_one(f"#{self.FIELDS[0].lower()}", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-search":
            self.action_save()
        elif event.button.id == "cancel-search":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_save(self) -> None:
        try:
            persist_env_changes(self.project_dir, self._answers())
        except Exception as exc:
            self.query_one("#search-status", Static).update(f"Cannot save: {exc}")
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _answers(self) -> ConfigAnswers:
        draft = load_draft(self.project_dir)
        overrides = {field: self._value(field) for field in self.FIELDS}
        return ConfigAnswers(
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

    def _value(self, key: str) -> str:
        return self.query_one(f"#{key.lower()}", Input).value

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
