"""Mode selection screen for the Thorondor configurator."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static
from textual.worker import Worker, WorkerState

from ...models import (
    ModelProgressCallback,
    ensure_llamacpp_models,
    llamacpp_models_status,
)
from ...state import (
    ConfigAnswers,
    MissingLlamaCppModelsError,
    build_llamacpp_env_values,
    load_draft,
    persist_env_changes,
)
from .navigation import ARROW_NAV_BINDINGS, ArrowNavigationMixin

DOWNLOAD_WORKER = "llamacpp-download"


class ModelModeScreen(ArrowNavigationMixin, Screen[None]):
    """Choose the model delivery mode (BYO / bundled / llama.cpp).

    Selecting ``llamacpp`` auto-downloads any missing GGUF files into
    ``<project>/models/`` before writing the env, with a visible progress
    indicator. The ``Download models`` button triggers the same download
    without changing the mode.
    """

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
        self._download_worker: Worker[list[str]] | None = None
        self._download_active = False
        self._pending_mode_after_download: str | None = None

    def compose(self) -> ComposeResult:
        draft = load_draft(self.project_dir)
        yield Static("Mode", id="mode-title", classes="brand")
        with Vertical(id="mode-form"):
            yield Static(
                f"Current mode: {draft.mode}\n"
                "Choose the model delivery mode to write complete env values.\n"
                "llamacpp auto-downloads missing GGUF model files.",
                classes="status",
            )
            with Horizontal(classes="form-row"):
                yield Button("BYO endpoints", id="byo", variant="primary")
                yield Button("bundled-models (TEI)", id="bundled")
                yield Button("llamacpp (GGUF)", id="llamacpp")
            with Horizontal(classes="form-row"):
                yield Button("Download models", id="download-models")
                yield Button("Cancel download", id="cancel-download", disabled=True)
            with Horizontal(classes="form-row"):
                yield Button("Apply", id="apply-mode")
                yield Button("Cancel", id="cancel-mode")
            yield Static("", id="mode-status")
            yield Static("", id="download-progress", classes="status")

    def on_mount(self) -> None:
        self.query_one("#byo", Button).focus()
        self._refresh_status_for_mode(draft_mode=load_draft(self.project_dir).mode)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "byo" | "bundled" | "llamacpp":
                self._save(self._mode_for_button(event.button.id or ""))
            case "apply-mode":
                self.action_apply_mode()
            case "cancel-mode":
                self.action_cancel()
            case "download-models":
                self.action_download_models()
            case "cancel-download":
                self.action_cancel_download()

    def action_apply_mode(self) -> None:
        self._save("byo")

    def action_download_models(self) -> None:
        if self._download_active:
            return
        self._pending_mode_after_download = None
        self._start_download()

    def action_cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
        self._set_status("Download cancelled.")

    def action_cancel(self) -> None:
        if self._download_active and self._download_worker is not None:
            self._download_worker.cancel()
        self.app.pop_screen()

    def _mode_for_button(self, button_id: str) -> str:
        return {
            "byo": "byo",
            "bundled": "bundled-models",
            "llamacpp": "llamacpp",
        }.get(button_id, "byo")

    def _save(self, mode: str) -> None:
        try:
            persist_env_changes(self.project_dir, self._answers(mode))
        except MissingLlamaCppModelsError:
            self._set_status("GGUF model files missing — downloading…")
            self._pending_mode_after_download = mode
            self._start_download()
            return
        except Exception as exc:
            self._set_status(f"Cannot save: {exc}")
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

    def _start_download(self) -> None:
        try:
            llamacpp_values = build_llamacpp_env_values(
                self._answers("llamacpp")
            )
        except Exception as exc:
            self._set_status(f"Cannot read llamacpp template: {exc}")
            return
        _present, missing = llamacpp_models_status(self.project_dir, llamacpp_values)
        if not missing:
            self._set_status("All llamacpp model files are already present.")
            self._finish_pending_save()
            return
        self._set_download_active(True)
        for model in missing:
            self._render_progress(
                f"Queued: {model.filename} ({model.license})", 0, None
            )
        progress: ModelProgressCallback = self._on_download_progress
        self._download_worker = self.run_worker(
            lambda _values=llamacpp_values, _cb=progress: ensure_llamacpp_models(
                self.project_dir, _values, progress=_cb
            ),
            name=DOWNLOAD_WORKER,
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def _on_download_progress(
        self, filename: str, downloaded: int, total: int | None
    ) -> None:
        self.app.call_from_thread(self._render_progress, filename, downloaded, total)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != DOWNLOAD_WORKER:
            return
        self._handle_download_state(event.worker, event.state)

    def _handle_download_state(
        self, worker: Worker[list[str]], state: WorkerState
    ) -> None:
        if state == WorkerState.SUCCESS:
            downloaded = list(worker.result or [])
            self._set_download_active(False)
            if downloaded:
                self._set_status(
                    f"Downloaded: {', '.join(downloaded)}. Continuing save…"
                )
            else:
                self._set_status("All llamacpp model files were already present.")
            self._render_progress("Idle.", 0, None)
            self._finish_pending_save()
        elif state == WorkerState.ERROR:
            self._set_download_active(False)
            error = worker.error or RuntimeError("Unknown download error")
            self._set_status(f"Download failed: {error}")
            self._render_progress("Download failed.", 0, None)
        elif state == WorkerState.CANCELLED:
            self._set_download_active(False)
            self._set_status("Download cancelled.")
            self._render_progress("Cancelled.", 0, None)

    def _finish_pending_save(self) -> None:
        pending = self._pending_mode_after_download
        self._pending_mode_after_download = None
        if pending is None:
            return
        self._save(pending)

    def _set_download_active(self, active: bool) -> None:
        self._download_active = active
        self.query_one("#download-models", Button).disabled = active
        self.query_one("#cancel-download", Button).disabled = not active
        for button_id in ("byo", "bundled", "llamacpp", "apply-mode"):
            self.query_one(f"#{button_id}", Button).disabled = active

    def _render_progress(
        self, filename: str, downloaded: int, total: int | None
    ) -> None:
        text = Text()
        text.append(filename, style="bold")
        if total and total > 0:
            pct = min(100, int(downloaded * 100 / total))
            text.append(f"  {pct}%  ({_human_bytes(downloaded)} / {_human_bytes(total)})")
        elif downloaded > 0 and total is None:
            text.append(f"  {_human_bytes(downloaded)} downloaded")
        self.query_one("#download-progress", Static).update(text)

    def _refresh_status_for_mode(self, draft_mode: str) -> None:
        llamacpp_values = load_draft(self.project_dir).llamacpp_env
        present, missing = llamacpp_models_status(self.project_dir, llamacpp_values)
        if draft_mode == "llamacpp" and missing:
            joined = ", ".join(m.filename for m in missing)
            self._set_status(
                f"llamacpp mode is active but GGUF files are missing: {joined}. "
                "Press Apply or Download models to fetch them."
            )
        elif missing:
            joined = ", ".join(m.filename for m in missing)
            self._set_status(
                f"llamacpp model files not yet on disk: {joined}. "
                "Pick llamacpp to auto-download, or press Download models."
            )
        else:
            self._set_status("All llamacpp model files are present.")

    def _set_status(self, message: str) -> None:
        self.query_one("#mode-status", Static).update(message)

    def _refresh_dashboard_widget(self) -> None:
        refresh = getattr(self._dashboard, "refresh_dashboard", None)
        if refresh is not None:
            refresh()


def _human_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
