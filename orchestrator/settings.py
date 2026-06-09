"""Environment-driven configuration for the orchestrator."""
from dataclasses import dataclass
import os


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def _optional(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _set_env(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


@dataclass(frozen=True)
class Settings:
    searxng_url: str
    crawl4ai_url: str
    chunker_url: str
    reranker_endpoint: str
    reranker_model: str
    reranker_path: str
    reranker_health_path: str
    llm_endpoint: str | None
    llm_model: str | None
    max_urls: int = 6
    crawl_concurrency: int = 4
    crawl_timeout_s: int = 15
    default_token_budget: int = 4000
    cache_backend: str = "memory"
    domain_blocklist: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.domain_blocklist is None:
            object.__setattr__(self, "domain_blocklist", set())


def load_settings() -> Settings:
    return Settings(
        searxng_url=_required("SEARXNG_URL"),
        crawl4ai_url=_required("CRAWL4AI_URL"),
        chunker_url=_required("CHUNKER_URL"),
        reranker_endpoint=_required("RERANKER_ENDPOINT"),
        reranker_model=_required("RERANKER_MODEL"),
        reranker_path=os.environ.get("RERANKER_PATH", "/rerank").strip() or "/rerank",
        reranker_health_path=os.environ.get("RERANKER_HEALTH_PATH", "/healthz").strip() or "/healthz",
        llm_endpoint=_optional("LLM_ENDPOINT"),
        llm_model=_optional("LLM_MODEL"),
        max_urls=_int_env("MAX_URLS", 6),
        crawl_concurrency=_int_env("CRAWL_CONCURRENCY", 4),
        crawl_timeout_s=_int_env("CRAWL_TIMEOUT_S", 15),
        default_token_budget=_int_env("DEFAULT_TOKEN_BUDGET", 4000),
        cache_backend=os.environ.get("CACHE_BACKEND", "memory").strip() or "memory",
        domain_blocklist=_set_env("DOMAIN_BLOCKLIST"),
    )
