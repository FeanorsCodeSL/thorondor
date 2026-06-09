import pytest

from orchestrator.settings import load_settings


REQUIRED = ["SEARXNG_URL", "CRAWL4AI_URL", "CHUNKER_URL", "RERANKER_ENDPOINT", "RERANKER_MODEL"]


def test_missing_required_var_raises(monkeypatch):
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError):
        load_settings()


def test_domain_blocklist_parses_and_defaults_apply(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")
    monkeypatch.setenv("CRAWL4AI_URL", "http://crawl4ai:11235")
    monkeypatch.setenv("CHUNKER_URL", "http://chunker:8000")
    monkeypatch.setenv("RERANKER_ENDPOINT", "http://reranker:80")
    monkeypatch.setenv("RERANKER_MODEL", "rerank")
    monkeypatch.setenv("DOMAIN_BLOCKLIST", "a.com, b.com")
    for name in ["MAX_URLS", "CRAWL_CONCURRENCY", "CRAWL_TIMEOUT_S", "DEFAULT_TOKEN_BUDGET"]:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.domain_blocklist == {"a.com", "b.com"}
    assert settings.max_urls == 6
    assert settings.crawl_concurrency == 4
    assert settings.crawl_timeout_s == 15
    assert settings.default_token_budget == 4000
    assert settings.reranker_path == "/rerank"
    assert settings.reranker_health_path == "/healthz"
