"""Tests for the GGUF model download helper."""

from __future__ import annotations

import httpx
import pytest
import respx
from thorondor_cli.models import (
    CHUNK_BYTES,
    LLAMACPP_MODEL_SOURCES,
    ensure_llamacpp_models,
    llamacpp_models_status,
    required_llamacpp_models,
)
from thorondor_cli.state import ConfigAnswers, build_llamacpp_env_values

LLAMACPP_VALUES = build_llamacpp_env_values(ConfigAnswers(mode="llamacpp"))


def _make_project(tmp_path):
    root = tmp_path / "thorondor"
    root.mkdir()
    (root / "searxng").mkdir()
    (root / "searxng" / "settings.yml").write_text(
        "use_default_settings: true\n", encoding="utf-8"
    )
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker-compose.llamacpp.yml").write_text("services: {}\n", encoding="utf-8")
    return root


def test_required_llamacpp_models_lists_expected_filenames(tmp_path):
    root = _make_project(tmp_path)
    required = required_llamacpp_models(root, LLAMACPP_VALUES)
    names = [m.filename for m in required]
    assert names == ["bge-m3.gguf", "bge-reranker-v2-m3.gguf"]
    for model in required:
        assert model.target == root / "models" / model.filename
        assert model.url == LLAMACPP_MODEL_SOURCES[model.filename]
        assert "MIT" in model.license or "Apache" in model.license


def test_required_llamacpp_models_skips_already_present(tmp_path):
    root = _make_project(tmp_path)
    (root / "models").mkdir()
    (root / "models" / "bge-m3.gguf").write_bytes(b"\x00" * 8)
    required = required_llamacpp_models(root, LLAMACPP_VALUES)
    assert [m.filename for m in required] == ["bge-reranker-v2-m3.gguf"]


def test_llamacpp_models_status_splits_present_and_missing(tmp_path):
    root = _make_project(tmp_path)
    (root / "models").mkdir()
    (root / "models" / "bge-m3.gguf").write_bytes(b"\x00" * 8)
    present, missing = llamacpp_models_status(root, LLAMACPP_VALUES)
    assert [m.filename for m in present] == ["bge-m3.gguf"]
    assert [m.filename for m in missing] == ["bge-reranker-v2-m3.gguf"]


def test_ensure_llamacpp_models_writes_files_and_reports_progress(tmp_path):
    root = _make_project(tmp_path)
    payload = b"GGUF\x00" * (CHUNK_BYTES // 2 + 7)
    with respx.mock(assert_all_called=True) as router:
        router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
            return_value=httpx.Response(200, content=payload)
        )
        router.get(LLAMACPP_MODEL_SOURCES["bge-reranker-v2-m3.gguf"]).mock(
            return_value=httpx.Response(200, content=payload)
        )
        progress: list[tuple[str, int, int | None]] = []
        downloaded = ensure_llamacpp_models(
            root, LLAMACPP_VALUES, progress=lambda *args: progress.append(args)
        )
    assert downloaded == ["bge-m3.gguf", "bge-reranker-v2-m3.gguf"]
    for filename in downloaded:
        target = root / "models" / filename
        assert target.exists()
        assert target.read_bytes() == payload
    assert progress
    assert all(name in ("bge-m3.gguf", "bge-reranker-v2-m3.gguf") for name, _, _ in progress)


def test_ensure_llamacpp_models_skips_present_files(tmp_path):
    root = _make_project(tmp_path)
    (root / "models").mkdir()
    existing = root / "models" / "bge-m3.gguf"
    existing.write_bytes(b"existing")
    with respx.mock(assert_all_called=True) as router:
        router.get(LLAMACPP_MODEL_SOURCES["bge-reranker-v2-m3.gguf"]).mock(
            return_value=httpx.Response(200, content=b"reranker")
        )
        downloaded = ensure_llamacpp_models(root, LLAMACPP_VALUES)
    assert downloaded == ["bge-reranker-v2-m3.gguf"]
    assert existing.read_bytes() == b"existing"
    assert (root / "models" / "bge-reranker-v2-m3.gguf").read_bytes() == b"reranker"


def test_ensure_llamacpp_models_raises_on_http_error(tmp_path):
    root = _make_project(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
            return_value=httpx.Response(500, text="upstream down")
        )
        with pytest.raises(httpx.HTTPStatusError):
            ensure_llamacpp_models(root, LLAMACPP_VALUES)
    assert not (root / "models" / "bge-m3.gguf").exists()


def test_ensure_llamacpp_models_writes_atomically(tmp_path):
    root = _make_project(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
            return_value=httpx.Response(200, content=b"embedding")
        )
        router.get(LLAMACPP_MODEL_SOURCES["bge-reranker-v2-m3.gguf"]).mock(
            return_value=httpx.Response(200, content=b"reranker")
        )
        ensure_llamacpp_models(root, LLAMACPP_VALUES)
    assert not (root / "models" / "bge-m3.gguf.part").exists()
    assert not (root / "models" / "bge-reranker-v2-m3.gguf.part").exists()
