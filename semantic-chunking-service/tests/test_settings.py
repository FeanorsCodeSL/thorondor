import pytest

from chunking.settings import _int_env, require_env


def test_require_env_raises_on_blank_var(monkeypatch):
    monkeypatch.setenv("THORONDOR_TEST_REQUIRED", "   ")

    with pytest.raises(RuntimeError):
        require_env("THORONDOR_TEST_REQUIRED")


def test_require_env_returns_stripped_value(monkeypatch):
    monkeypatch.setenv("THORONDOR_TEST_REQUIRED", "  value  ")

    assert require_env("THORONDOR_TEST_REQUIRED") == "value"


def test_int_env_honors_default(monkeypatch):
    monkeypatch.delenv("THORONDOR_TEST_INT", raising=False)

    assert _int_env("THORONDOR_TEST_INT", 42) == 42
