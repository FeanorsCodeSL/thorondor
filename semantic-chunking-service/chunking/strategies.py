"""Chunking strategy registry and version pinning."""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class StrategyParams:
    max_chunk_tokens: int
    min_chunk_tokens: int
    initial_segment_tokens: int


_STRATEGIES: Dict[str, StrategyParams] = {
    "cluster-semantic@1": StrategyParams(
        max_chunk_tokens=400,
        min_chunk_tokens=50,
        initial_segment_tokens=50,
    ),
}


def resolve_strategy(
    version: str,
    overrides: Optional[Dict[str, int]] = None,
) -> StrategyParams:
    """Resolve a strategy version to its params, applying optional overrides."""
    base = _STRATEGIES.get(version)
    if base is None:
        raise KeyError(f"Unknown strategy_version: {version!r}")
    if not overrides:
        params = base
    else:
        params = StrategyParams(
            max_chunk_tokens=overrides.get("max_chunk_tokens", base.max_chunk_tokens),
            min_chunk_tokens=overrides.get("min_chunk_tokens", base.min_chunk_tokens),
            initial_segment_tokens=overrides.get("initial_segment_tokens", base.initial_segment_tokens),
        )
    if params.initial_segment_tokens > params.max_chunk_tokens:
        raise ValueError("initial_segment_tokens must be <= max_chunk_tokens")
    if params.min_chunk_tokens > params.max_chunk_tokens:
        raise ValueError("min_chunk_tokens must be <= max_chunk_tokens")
    return params
