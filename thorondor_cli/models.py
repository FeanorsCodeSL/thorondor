"""Local GGUF model fetch for the llamacpp profile.

The TUI and the ``thorondor download-models`` subcommand call
:func:`ensure_llamacpp_models` to download any missing GGUF files into
``<project>/models/``. The default Q8 sources are pinned in
:data:`LLAMACPP_MODEL_SOURCES` to immutable HuggingFace revisions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_LLAMACPP_EMBEDDING_URL = (
    "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/"
    "2d48f1737679ad900d5c26c5aad5410e9c70fdca/bge-m3-Q8_0.gguf"
)
DEFAULT_LLAMACPP_RERANKER_URL = (
    "https://huggingface.co/gpustack/bge-reranker-v2-m3-GGUF/resolve/"
    "3093af03b1a635e67b084b1d8c03c5f5e020fd05/bge-reranker-v2-m3-Q8_0.gguf"
)

LLAMACPP_MODEL_SOURCES: dict[str, str] = {
    "bge-m3.gguf": DEFAULT_LLAMACPP_EMBEDDING_URL,
    "bge-reranker-v2-m3.gguf": DEFAULT_LLAMACPP_RERANKER_URL,
}

LLAMACPP_MODEL_LICENSES: dict[str, str] = {
    "bge-m3.gguf": "MIT (BAAI/bge-m3)",
    "bge-reranker-v2-m3.gguf": "Apache-2.0 (BAAI/bge-reranker-v2-m3)",
}

LLAMACPP_MODEL_SHA256: dict[str, str] = {
    "bge-m3.gguf": "950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167",
    "bge-reranker-v2-m3.gguf": "a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3",
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
    sha256: str

    @property
    def present(self) -> bool:
        return self.target.exists() and self.target.stat().st_size > 0


ModelProgressCallback = Callable[[str, int, int | None], None]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(
    url: str,
    dest: Path,
    *,
    progress: ModelProgressCallback | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    client: httpx.Client | None = None,
    expected_sha256: str = "",
) -> int:
    """Stream ``url`` to ``dest`` in fixed-size chunks.

    Returns the number of bytes written. Refuses to overwrite an existing
    non-empty file; the caller is expected to check first.
    """
    if dest.exists() and dest.stat().st_size > 0:
        actual_sha256 = _file_sha256(dest) if expected_sha256 else ""
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise ValueError(
                f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, "
                f"got {actual_sha256}"
            )
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
            digest = hashlib.sha256()
            try:
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        if progress is not None:
                            progress(dest.name, written, total)
                actual_sha256 = digest.hexdigest()
                if expected_sha256 and actual_sha256 != expected_sha256:
                    raise ValueError(
                        f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, "
                        f"got {actual_sha256}"
                    )
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(dest)
    finally:
        if own_client:
            http.close()
    return written


def _default_progress(_name: str, _downloaded: int, _total: int | None) -> None:
    return None


def _llamacpp_models(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
) -> list[LlamaCppModel]:
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
        sha256 = LLAMACPP_MODEL_SHA256.get(filename, "")
        models.append(LlamaCppModel(filename, source, target, license, sha256))
    return models


def required_llamacpp_models(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
) -> list[LlamaCppModel]:
    """Return the llamacpp GGUF files the project is missing on disk."""
    return [model for model in _llamacpp_models(project_dir, llamacpp_values) if not model.present]


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
    for model in _llamacpp_models(project_dir, llamacpp_values):
        was_present = model.present
        if not model.url:
            if was_present:
                continue
            raise FileNotFoundError(
                f"No download URL configured for {model.filename}; "
                "place the file under models/ manually."
            )
        if not was_present:
            callback(model.filename, 0, None)
        download_model(
            model.url,
            model.target,
            progress=callback,
            client=client,
            expected_sha256=model.sha256,
        )
        if not was_present:
            downloaded.append(model.filename)
    return downloaded


def llamacpp_models_status(
    project_dir: str | Path,
    llamacpp_values: Mapping[str, str],
) -> tuple[list[LlamaCppModel], list[LlamaCppModel]]:
    """Split the llamacpp models into ``(present, missing)`` lists."""
    all_models = _llamacpp_models(project_dir, llamacpp_values)
    present = [m for m in all_models if m.present]
    missing = [m for m in all_models if not m.present]
    return present, missing
