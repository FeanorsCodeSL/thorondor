"""Local GGUF model fetch for the llamacpp profile.

The TUI and the ``thorondor download-models`` subcommand call
:func:`ensure_llamacpp_models` to download any missing GGUF files into
``<project>/models/``. The default Q8 sources are pinned in
:data:`LLAMACPP_MODEL_SOURCES` and resolve directly to HuggingFace
``resolve/main`` URLs so no HuggingFace token is required.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_LLAMACPP_EMBEDDING_URL = (
    "https://huggingface.co/BAAI/bge-m3-GGUF/resolve/main/bge-m3-q8_0.gguf"
)
DEFAULT_LLAMACPP_RERANKER_URL = (
    "https://huggingface.co/BAAI/bge-reranker-v2-m3-GGUF/resolve/main/"
    "bge-reranker-v2-m3-q8_0.gguf"
)

LLAMACPP_MODEL_SOURCES: dict[str, str] = {
    "bge-m3.gguf": DEFAULT_LLAMACPP_EMBEDDING_URL,
    "bge-reranker-v2-m3.gguf": DEFAULT_LLAMACPP_RERANKER_URL,
}

LLAMACPP_MODEL_LICENSES: dict[str, str] = {
    "bge-m3.gguf": "MIT (BAAI/bge-m3)",
    "bge-reranker-v2-m3.gguf": "Apache-2.0 (BAAI/bge-reranker-v2-m3)",
}

CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_S = 600.0


@dataclass(frozen=True)
class LlamaCppModel:
    """A single GGUF that the llamacpp profile expects on disk."""

    filename: str
    url: str
    target: Path
    license: str

    @property
    def present(self) -> bool:
        return self.target.exists() and self.target.stat().st_size > 0


ModelProgressCallback = Callable[[str, int, int | None], None]


def download_model(
    url: str,
    dest: Path,
    *,
    progress: ModelProgressCallback | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    client: httpx.Client | None = None,
) -> int:
    """Stream ``url`` to ``dest`` in fixed-size chunks.

    Returns the number of bytes written. Refuses to overwrite an existing
    non-empty file; the caller is expected to check first.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size

    dest.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        with http.stream("GET", url) as response:
            response.raise_for_status()
            total_header = response.headers.get("content-length")
            total = int(total_header) if total_header else None
            written = 0
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    written += len(chunk)
                    if progress is not None:
                        progress(dest.name, written, total)
            tmp.replace(dest)
    finally:
        if own_client:
            http.close()
    return written


def _default_progress(_name: str, _downloaded: int, _total: int | None) -> None:
    return None


def required_llamacpp_models(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
) -> list[LlamaCppModel]:
    """Return the llamacpp GGUF files the project is missing on disk."""
    root = Path(project_dir).expanduser().resolve()
    models_dir = root / "models"
    models: list[LlamaCppModel] = []
    for key in ("LLAMACPP_EMBEDDING_MODEL", "LLAMACPP_RERANKER_MODEL"):
        container_path = llamacpp_values.get(key, "")
        if not container_path.startswith("/models/"):
            continue
        filename = container_path.removeprefix("/models/")
        if "/" in filename:
            continue
        target = models_dir / filename
        license = LLAMACPP_MODEL_LICENSES.get(filename, "see HuggingFace")
        source = LLAMACPP_MODEL_SOURCES.get(filename, "")
        model = LlamaCppModel(filename, source, target, license)
        if not model.present:
            models.append(model)
    return models


def ensure_llamacpp_models(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
    *,
    progress: ModelProgressCallback | None = None,
    client: httpx.Client | None = None,
) -> list[str]:
    """Download any missing GGUF files. Returns the list of filenames fetched."""
    callback = progress or _default_progress
    downloaded: list[str] = []
    for model in required_llamacpp_models(project_dir, llamacpp_values):
        if not model.url:
            raise FileNotFoundError(
                f"No download URL configured for {model.filename}; "
                "place the file under models/ manually."
            )
        callback(model.filename, 0, None)
        download_model(
            model.url,
            model.target,
            progress=callback,
            client=client,
        )
        downloaded.append(model.filename)
    return downloaded


def llamacpp_models_status(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
) -> tuple[list[LlamaCppModel], list[LlamaCppModel]]:
    """Split the llamacpp models into ``(present, missing)`` lists."""
    root = Path(project_dir).expanduser().resolve()
    all_models: list[LlamaCppModel] = []
    for key in ("LLAMACPP_EMBEDDING_MODEL", "LLAMACPP_RERANKER_MODEL"):
        container_path = llamacpp_values.get(key, "")
        if not container_path.startswith("/models/"):
            continue
        filename = container_path.removeprefix("/models/")
        if "/" in filename:
            continue
        target = root / "models" / filename
        license = LLAMACPP_MODEL_LICENSES.get(filename, "see HuggingFace")
        source = LLAMACPP_MODEL_SOURCES.get(filename, "")
        all_models.append(LlamaCppModel(filename, source, target, license))
    present = [m for m in all_models if m.present]
    missing = [m for m in all_models if not m.present]
    return present, missing
