"""Endpoint editor screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from ...probe import probe_embedding, probe_reranker, rewrite_host_for_docker
from ...state import ConfigAnswers, load_draft, persist_env_changes
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

ENDPOINT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("embedding_endpoint", "Embedding base URL", ""),
    ("embedding_model", "Embedding model", ""),
    ("embedding_api_key", "Embedding token", "password"),
    ("reranker_endpoint", "Reranker base URL", ""),
    ("reranker_model", "Reranker model", ""),
    ("reranker_path", "Rerank path", ""),
    ("reranker_api_key", "Reranker token", "password"),
    ("llm_endpoint", "Optional LLM base URL", ""),
    ("llm_model", "Optional LLM model", ""),
    ("llm_api_key", "Optional LLM token", "password"),
)


class EndpointFormScreen(ArrowNavigationMixin, Screen[None]):
    """Edit embedding, reranker, and LLM endpoints."""

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
        yield Static("Endpoints", id="endpoints-title", classes="brand")
        with Vertical(id="endpoints-form"):
            for field_id, label, kind in ENDPOINT_FIELDS:
                yield Static(label, classes="field-label")
                kwargs: dict[str, object] = {
                    "id": field_id,
                    "placeholder": label,
                    "value": draft.env.get(field_id.upper(), ""),
                }
                if kind == "password":
                    kwargs["password"] = True
                yield Input(**kwargs)  # type: ignore[arg-type]
            with Horizontal(classes="form-row"):
                yield Button("Test connection", id="test-endpoints")
                yield Button("Save", id="save-endpoints", variant="primary")
                yield Button("Cancel", id="cancel-endpoints")
            yield Static("", id="endpoints-status")

    def on_mount(self) -> None:
        self.query_one(f"#{ENDPOINT_FIELDS[0][0]}", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-endpoints":
            self.action_test_connection()
        elif event.button.id == "save-endpoints":
            self.action_save()
        elif event.button.id == "cancel-endpoints":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_test_connection(self) -> None:
        answers = self._answers()
        embedding = probe_embedding(
            answers.embedding_endpoint,
            answers.embedding_model,
            answers.embedding_api_key or None,
        )
        reranker = probe_reranker(
            answers.reranker_endpoint,
            answers.reranker_path,
            answers.reranker_model,
            answers.reranker_api_key or None,
        )
        self.query_one("#endpoints-status", Static).update(
            f"{self._preview()}\nembedding probe: {embedding.status}\n"
            f"reranker probe: {reranker.status}"
        )

    def action_save(self) -> None:
        try:
            persist_env_changes(self.project_dir, self._answers())
        except Exception as exc:
            self.query_one("#endpoints-status", Static).update(f"Cannot save: {exc}")
            return
        self.app.refresh_dashboard_state()
        self._refresh_dashboard_widget()
        self.app.pop_screen()

    def _answers(self) -> ConfigAnswers:
        draft = load_draft(self.project_dir)

        def _value(field_id: str) -> str:
            return self.query_one(f"#{field_id}", Input).value

        return ConfigAnswers(
            mode="byo",
            embedding_endpoint=_value("embedding_endpoint"),
            embedding_model=_value("embedding_model"),
            embedding_api_key=_value("embedding_api_key"),
            reranker_endpoint=_value("reranker_endpoint"),
            reranker_model=_value("reranker_model"),
            reranker_path=_value("reranker_path") or "/rerank",
            reranker_api_key=_value("reranker_api_key"),
            llm_endpoint=_value("llm_endpoint"),
            llm_model=_value("llm_model"),
            llm_api_key=_value("llm_api_key"),
            orchestrator_host=draft.env.get("ORCHESTRATOR_HOST", "127.0.0.1"),
            orchestrator_port=draft.env.get("ORCHESTRATOR_PORT", "8080"),
        )

    def _preview(self) -> str:
        answers = self._answers()
        embedding, embedding_rewritten = rewrite_host_for_docker(answers.embedding_endpoint)
        reranker, reranker_rewritten = rewrite_host_for_docker(answers.reranker_endpoint)
        lines = [f"embedding: {embedding}", f"reranker: {reranker}"]
        if embedding_rewritten or reranker_rewritten:
            lines.append("host-gateway overlay will be generated for Docker on Linux")
        return "\n".join(lines)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()
