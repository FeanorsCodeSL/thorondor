import pytest
from thorondor_cli.envfile import read_env, seed_from_example
from thorondor_cli.project import ProjectError, resolve_project_dir
from thorondor_cli.state import (
    HOST_ENDPOINTS_OVERLAY,
    ConfigAnswers,
    build_env_values,
    compose_overlays,
    compute_issues,
    load_draft,
    persist_env_changes,
)


def make_project(tmp_path):
    root = tmp_path / "thorondor"
    root.mkdir()
    (root / "searxng").mkdir()
    (root / "searxng" / "settings.yml").write_text("use_default_settings: true\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker-compose.llamacpp.yml").write_text("services: {}\n", encoding="utf-8")
    return root


def test_project_validation_rejects_missing_compose(tmp_path):
    (tmp_path / "unrelated.txt").write_text("not thorondor\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        resolve_project_dir(tmp_path)


def test_load_draft_tolerates_missing_partial_and_malformed_env(tmp_path):
    root = make_project(tmp_path)
    missing = load_draft(root)
    assert missing.env
    assert missing.env_exists is False

    (root / ".env").write_text("ORCHESTRATOR_PORT=not-a-port\n", encoding="utf-8")
    partial = load_draft(root)
    assert "ORCHESTRATOR_PORT" in partial.invalid_values
    assert partial.missing_env_keys
    assert compute_issues(partial, {})


def test_build_env_values_is_complete_and_rewrites_host_endpoint():
    values = build_env_values(
        ConfigAnswers(
            mode="byo",
            embedding_endpoint="http://localhost:8082",
            embedding_model="bge",
            reranker_endpoint="http://127.0.0.1:8081",
            reranker_model="rank",
        )
    )
    assert set(seed_from_example()) <= set(values)
    assert values["EMBEDDING_ENDPOINT"] == "http://host.docker.internal:8082"
    assert values["RERANKER_ENDPOINT"] == "http://host.docker.internal:8081"
    assert values["SEARXNG_SECRET"]


def test_compose_overlays_only_for_external_embedding():
    assert compose_overlays(ConfigAnswers(mode="bundled-models")) == []
    assert compose_overlays(ConfigAnswers(mode="llamacpp")) == []
    assert compose_overlays(
        ConfigAnswers(mode="byo", embedding_endpoint="http://example.com:8082")
    ) == [HOST_ENDPOINTS_OVERLAY]


def test_persist_byo_writes_complete_env_and_host_overlay(tmp_path):
    root = make_project(tmp_path)
    draft = persist_env_changes(
        root,
        ConfigAnswers(
            mode="byo",
            embedding_endpoint="http://localhost:8082",
            embedding_model="bge",
            reranker_endpoint="http://localhost:8081",
            reranker_model="rank",
        ),
    )
    values = read_env(root / ".env")
    assert set(seed_from_example()) <= set(values)
    assert draft.overlay_needed is True
    overlay = (root / HOST_ENDPOINTS_OVERLAY).read_text(encoding="utf-8")
    assert "chunker:" in overlay
    assert "egress" in overlay
    assert "host.docker.internal:host-gateway" in overlay


def test_llamacpp_missing_gguf_does_not_write_half_valid_env(tmp_path):
    root = make_project(tmp_path)
    with pytest.raises(ValueError):
        persist_env_changes(root, ConfigAnswers(mode="llamacpp"))
    assert not (root / ".env").exists()


def test_write_complete_env_allows_bundled_mode(tmp_path):
    root = make_project(tmp_path)
    persist_env_changes(root, ConfigAnswers(mode="bundled-models"))
    values = read_env(root / ".env")
    assert values["EMBEDDING_ENDPOINT"] == "http://embedding:80"
    assert values["RERANKER_ENDPOINT"] == "http://reranker:80"
