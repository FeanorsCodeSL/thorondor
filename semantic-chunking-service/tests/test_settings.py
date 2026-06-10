import pytest

from chunking.settings import load_settings, required_int_env, require_env


def test_require_env_raises_on_blank_var(monkeypatch):
    monkeypatch.setenv("THORONDOR_TEST_REQUIRED", "   ")

    with pytest.raises(RuntimeError):
        require_env("THORONDOR_TEST_REQUIRED")


def test_require_env_returns_stripped_value(monkeypatch):
    monkeypatch.setenv("THORONDOR_TEST_REQUIRED", "  value  ")

    assert require_env("THORONDOR_TEST_REQUIRED") == "value"


def test_required_int_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("THORONDOR_TEST_INT", raising=False)

    with pytest.raises(RuntimeError):
        required_int_env("THORONDOR_TEST_INT")


def test_required_int_env_parses_explicit_value(monkeypatch):
    monkeypatch.setenv("THORONDOR_TEST_INT", "42")

    assert required_int_env("THORONDOR_TEST_INT") == 42


def test_load_settings_requires_explicit_embedding_config(monkeypatch):
    for name in (
        "LOG_LEVEL",
        "CHUNKER_DEFAULT_STRATEGY_VERSION",
        "EMBEDDING_ENDPOINT",
        "EMBEDDING_MODEL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BATCH_SIZE",
        "EMBEDDING_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="LOG_LEVEL"):
        load_settings()


def test_load_settings_parses_explicit_embedding_config(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("CHUNKER_DEFAULT_STRATEGY_VERSION", "cluster-semantic@1")
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "http://embedding:80")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "32")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_S", "15.5")

    settings = load_settings()

    assert settings.log_level == "INFO"
    assert settings.default_strategy_version == "cluster-semantic@1"
    assert settings.embedding_endpoint == "http://embedding:80"
    assert settings.embedding_model == "bge"
    assert settings.embedding_api_key is None
    assert settings.embedding_batch_size == 32
    assert settings.embedding_timeout_s == 15.5
