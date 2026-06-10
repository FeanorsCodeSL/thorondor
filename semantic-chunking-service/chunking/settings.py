"""Environment-driven configuration for the semantic chunking service."""
from dataclasses import dataclass
import os


def require_env(name: str) -> str:
    """Return a required env var, raising if missing/blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def required_int_env(name: str) -> int:
    return int(require_env(name))


def required_float_env(name: str) -> float:
    return float(require_env(name))


def configured_optional_env(name: str) -> str | None:
    if name not in os.environ:
        raise RuntimeError(f"Required environment variable {name} is not set")
    value = os.environ[name]
    return value.strip() if value.strip() else None


@dataclass(frozen=True)
class Settings:
    log_level: str
    default_strategy_version: str
    embedding_endpoint: str
    embedding_model: str
    embedding_api_key: str | None
    embedding_batch_size: int
    embedding_timeout_s: float

    def __post_init__(self) -> None:
        if self.embedding_batch_size < 1:
            raise RuntimeError("EMBEDDING_BATCH_SIZE must be >= 1")
        if self.embedding_timeout_s <= 0:
            raise RuntimeError("EMBEDDING_TIMEOUT_S must be > 0")


def load_settings() -> Settings:
    return Settings(
        log_level=require_env("LOG_LEVEL").upper(),
        default_strategy_version=require_env("CHUNKER_DEFAULT_STRATEGY_VERSION"),
        embedding_endpoint=require_env("EMBEDDING_ENDPOINT"),
        embedding_model=require_env("EMBEDDING_MODEL"),
        embedding_api_key=configured_optional_env("EMBEDDING_API_KEY"),
        embedding_batch_size=required_int_env("EMBEDDING_BATCH_SIZE"),
        embedding_timeout_s=required_float_env("EMBEDDING_TIMEOUT_S"),
    )


# OOM guard: above this segment count, fall back to the O(N) greedy semantic
# path instead of building the O(N^2) similarity matrix. See cluster_semantic.py.
CHUNKER_MAX_SEGMENTS_DP = required_int_env("CHUNKER_MAX_SEGMENTS_DP")

# Bound on the DP reward LRU cache to keep memory predictable on huge inputs.
REWARD_CACHE_MAX_SIZE = required_int_env("REWARD_CACHE_MAX_SIZE")
