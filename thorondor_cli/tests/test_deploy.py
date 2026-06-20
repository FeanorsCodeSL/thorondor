import subprocess

import pytest
from thorondor_cli.deploy import (
    DeployError,
    compose_args,
    compose_plan,
    deploy,
    run_smoke_search,
    wait_for_health,
)
from thorondor_cli.envfile import write_env
from thorondor_cli.state import HOST_ENDPOINTS_OVERLAY, ConfigAnswers, persist_env_changes


def make_project(tmp_path):
    root = tmp_path / "thorondor"
    root.mkdir()
    (root / "searxng").mkdir()
    (root / "searxng" / "settings.yml").write_text("use_default_settings: true\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker-compose.llamacpp.yml").write_text("services: {}\n", encoding="utf-8")
    return root


def test_compose_args_include_env_files_profiles_and_overlays(tmp_path):
    root = make_project(tmp_path)
    persist_env_changes(
        root,
        ConfigAnswers(
            mode="byo",
            embedding_endpoint="http://localhost:8082",
            embedding_model="bge",
            reranker_endpoint="http://localhost:8081",
            reranker_model="rank",
        ),
    )
    plan = compose_plan(root)
    args = compose_args(plan)
    assert args == [
        "compose",
        "--env-file",
        ".env",
        "-f",
        "docker-compose.yml",
        "-f",
        HOST_ENDPOINTS_OVERLAY,
    ]


def test_deploy_runs_config_build_up_sequence(monkeypatch, tmp_path):
    root = make_project(tmp_path)
    write_env(
        root / ".env",
        {
            "ORCHESTRATOR_HOST": "127.0.0.1",
            "ORCHESTRATOR_PORT": "8080",
            "DEFAULT_TOKEN_BUDGET": "4000",
        },
    )
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "thorondor_cli.deploy.wait_for_health",
        lambda url: {"dependencies": {"x": True}},
    )
    monkeypatch.setattr(
        "thorondor_cli.deploy.run_smoke_search",
        lambda url, token_budget: {"passages": [1]},
    )

    list(deploy(root, runner=runner))

    assert [call[-1] for call in calls] == ["config", "build", "-d"]
    assert calls[0][:2] == ["docker", "compose"]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.last = self.responses[-1] if self.responses else FakeResponse()

    def get(self, url):
        if self.responses:
            self.last = self.responses.pop(0)
        return self.last

    def post(self, url, json):
        if self.responses:
            self.last = self.responses.pop(0)
        return self.last

    def close(self):
        pass


def test_wait_for_health_passes_only_when_all_dependencies_true():
    payload = {"dependencies": {"searxng": True, "chunker": True}}
    assert wait_for_health(
        "http://x/healthz",
        timeout_s=0.1,
        interval_s=0,
        client=FakeClient([FakeResponse(payload=payload)]),
    ) == payload

    with pytest.raises(DeployError):
        wait_for_health(
            "http://x/healthz",
            timeout_s=0.01,
            interval_s=0,
            client=FakeClient([FakeResponse(payload={"dependencies": {"x": False}})]),
        )


def test_smoke_search_asserts_response_shape():
    good = {
        "passages": [{"text": "x"}],
        "stats": {"reranked": True, "tokens_returned": 10},
    }
    assert (
        run_smoke_search(
            "http://x/search",
            token_budget=20,
            client=FakeClient([FakeResponse(payload=good)]),
        )
        == good
    )

    bad = {"passages": [], "stats": {"reranked": True, "tokens_returned": 0}}
    with pytest.raises(DeployError):
        run_smoke_search("http://x/search", client=FakeClient([FakeResponse(payload=bad)]))
