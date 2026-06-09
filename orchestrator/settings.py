"""Environment-driven configuration for the orchestrator."""
from dataclasses import dataclass
import os

MAX_CRAWL_CONCURRENCY = 20


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


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    domain_allowlist: set[str] = None  # type: ignore[assignment]
    allowlist_only: bool = False
    crawl_respect_robots_txt: bool = True
    crawl_per_host_concurrency: int = 1
    searxng_api_key: str | None = None
    crawl4ai_api_key: str | None = None
    chunker_api_key: str | None = None
    reranker_api_key: str | None = None
    llm_api_key: str | None = None

    def __post_init__(self) -> None:
        if self.domain_blocklist is None:
            object.__setattr__(self, "domain_blocklist", set())
        if self.domain_allowlist is None:
            object.__setattr__(self, "domain_allowlist", set())
        if not 1 <= self.crawl_concurrency <= MAX_CRAWL_CONCURRENCY:
            raise RuntimeError(f"CRAWL_CONCURRENCY must be between 1 and {MAX_CRAWL_CONCURRENCY}")
        if self.crawl_per_host_concurrency < 1:
            raise RuntimeError("CRAWL_PER_HOST_CONCURRENCY must be >= 1")


def load_settings() -> Settings:
    return Settings(
        searxng_url=_required("SEARXNG_URL"),
        crawl4ai_url=_required("CRAWL4AI_URL"),
        chunker_url=_required("CHUNKER_URL"),
        reranker_endpoint=_required("RERANKER_ENDPOINT"),
        reranker_model=_required("RERANKER_MODEL"),
        reranker_path=os.environ.get("RERANKER_PATH", "/rerank").strip() or "/rerank",
        reranker_health_path=os.environ.get("RERANKER_HEALTH_PATH", "/health").strip() or "/health",
        llm_endpoint=_optional("LLM_ENDPOINT"),
        llm_model=_optional("LLM_MODEL"),
        max_urls=_int_env("MAX_URLS", 6),
        crawl_concurrency=_int_env("CRAWL_CONCURRENCY", 4),
        crawl_timeout_s=_int_env("CRAWL_TIMEOUT_S", 15),
        default_token_budget=_int_env("DEFAULT_TOKEN_BUDGET", 4000),
        cache_backend=os.environ.get("CACHE_BACKEND", "memory").strip() or "memory",
        domain_blocklist=_set_env("DOMAIN_BLOCKLIST"),
        domain_allowlist=_set_env("DOMAIN_ALLOWLIST"),
        allowlist_only=_bool_env("ALLOWLIST_ONLY", False),
        crawl_respect_robots_txt=_bool_env("CRAWL_RESPECT_ROBOTS_TXT", True),
        crawl_per_host_concurrency=_int_env("CRAWL_PER_HOST_CONCURRENCY", 1),
        searxng_api_key=_optional("SEARXNG_API_KEY"),
        crawl4ai_api_key=_optional("CRAWL4AI_API_KEY"),
        chunker_api_key=_optional("CHUNKER_API_KEY"),
        reranker_api_key=_optional("RERANKER_API_KEY"),
        llm_api_key=_optional("LLM_API_KEY"),
    )
