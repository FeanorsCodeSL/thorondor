from thorondor_cli.envfile import read_env
from thorondor_cli.project import missing_project_paths, resolve_project_dir


def test_default_resolution_creates_managed_project(monkeypatch, tmp_path):
    managed = tmp_path / "thorondor-home"
    monkeypatch.setenv("THORONDOR_HOME", str(managed))

    project = resolve_project_dir()

    assert project.root == managed
    assert (managed / ".thorondor-managed").exists()
    assert (managed / "docker-compose.yml").exists()
    assert (managed / "docker-compose.llamacpp.yml").exists()
    assert (managed / "searxng" / "settings.yml").exists()
    assert (managed / "searxng" / "limiter.toml").exists()
    assert not missing_project_paths(managed)
    env = read_env(managed / ".env")
    assert env["THORONDOR_ORCHESTRATOR_IMAGE"].startswith(
        "ghcr.io/feanorscodesl/thorondor-orchestrator:"
    )
    assert env["THORONDOR_SEARXNG_CONFIG_DIR"] == "./searxng"


def test_explicit_empty_project_dir_is_initialized(tmp_path):
    project_dir = tmp_path / "custom"
    project_dir.mkdir()

    project = resolve_project_dir(project_dir)

    assert project.root == project_dir
    assert (project_dir / ".thorondor-managed").exists()
    assert (project_dir / ".env").exists()
