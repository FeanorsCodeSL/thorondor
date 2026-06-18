"""Endpoint editor screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from ...probe import probe_embedding, probe_reranker, rewrite_host_for_docker
from ...state import ConfigAnswers, load_draft, persist_env_changes


class EndpointFormScreen(Screen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)

    def compose(self) -> ComposeResult:
        draft = load_draft(self.project_dir)
        yield Static("Endpoints", classes="screen-title")
        yield Input(
            draft.env.get("EMBEDDING_ENDPOINT", ""),
            id="embedding_endpoint",
            placeholder="Embedding base URL",
        )
        yield Input(
            draft.env.get("EMBEDDING_MODEL", ""),
            id="embedding_model",
            placeholder="Embedding model",
        )
        yield Input(
            draft.env.get("EMBEDDING_API_KEY", ""),
            id="embedding_api_key",
            placeholder="Embedding token",
            password=True,
        )
        yield Input(
            draft.env.get("RERANKER_ENDPOINT", ""),
            id="reranker_endpoint",
            placeholder="Reranker base URL",
        )
        yield Input(
            draft.env.get("RERANKER_MODEL", ""),
            id="reranker_model",
            placeholder="Reranker model",
        )
        yield Input(
            draft.env.get("RERANKER_PATH", "/rerank"),
            id="reranker_path",
            placeholder="Rerank path",
        )
        yield Input(
            draft.env.get("RERANKER_HEALTH_PATH", "/health"),
            id="reranker_health_path",
            placeholder="Health path",
        )
        yield Input(
            draft.env.get("RERANKER_API_KEY", ""),
            id="reranker_api_key",
            placeholder="Reranker token",
            password=True,
        )
        yield Input(
            draft.env.get("LLM_ENDPOINT", ""),
            id="llm_endpoint",
            placeholder="Optional LLM base URL",
        )
        yield Input(
            draft.env.get("LLM_MODEL", ""),
            id="llm_model",
            placeholder="Optional LLM model",
        )
        yield Input(
            draft.env.get("LLM_API_KEY", ""),
            id="llm_api_key",
            placeholder="Optional LLM token",
            password=True,
        )
        yield Button("Test connection", id="test")
        yield Button("Save", id="save")
        yield Static("", id="endpoint-status")

    def _input(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value

    def _answers(self) -> ConfigAnswers:
        draft = load_draft(self.project_dir)
        return ConfigAnswers(
            mode="byo",
            embedding_endpoint=self._input("embedding_endpoint"),
            embedding_model=self._input("embedding_model"),
            embedding_api_key=self._input("embedding_api_key"),
            reranker_endpoint=self._input("reranker_endpoint"),
            reranker_model=self._input("reranker_model"),
            reranker_path=self._input("reranker_path"),
            reranker_health_path=self._input("reranker_health_path"),
            reranker_api_key=self._input("reranker_api_key"),
            llm_endpoint=self._input("llm_endpoint"),
            llm_model=self._input("llm_model"),
            llm_api_key=self._input("llm_api_key"),
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test":
            answers = self._answers()
            embedding = probe_embedding(
                answers.embedding_endpoint,
                answers.embedding_model,
                answers.embedding_api_key or None,
            )
            reranker = probe_reranker(
                answers.reranker_endpoint,
                answers.reranker_health_path,
                answers.reranker_path,
                answers.reranker_model,
                answers.reranker_api_key or None,
            )
            self.query_one("#endpoint-status", Static).update(
                f"{self._preview()}\n"
                f"embedding probe: {embedding.status}\n"
                f"reranker probe: {reranker.status}"
            )
            return
        if event.button.id == "save":
            persist_env_changes(self.project_dir, self._answers())
            self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()
