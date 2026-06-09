"""Environment-driven configuration for the semantic chunking service."""
import os


def require_env(name: str) -> str:
    """Return a required env var, raising if missing/blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


# OOM guard: above this segment count, fall back to the O(N) greedy semantic
# path instead of building the O(N^2) similarity matrix. See cluster_semantic.py.
CHUNKER_MAX_SEGMENTS_DP = _int_env("CHUNKER_MAX_SEGMENTS_DP", 10_000)

# Bound on the DP reward LRU cache to keep memory predictable on huge inputs.
REWARD_CACHE_MAX_SIZE = _int_env("REWARD_CACHE_MAX_SIZE", 100_000)
