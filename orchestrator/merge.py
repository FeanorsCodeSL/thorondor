"""Merge and URL-deduplicate discovery result sets."""
from .normalize import normalize_url
from .types import DiscoveryResult


def merge_dedup(result_sets: list[list[DiscoveryResult]]) -> list[DiscoveryResult]:
    best: dict[str, DiscoveryResult] = {}
    counts: dict[str, int] = {}
    for results in result_sets:
        for result in results:
            key = normalize_url(result.url)
            counts[key] = counts.get(key, 0) + 1
            existing = best.get(key)
            if existing is None or result.score > existing.score:
                best[key] = result
    merged = [
        DiscoveryResult(
            result.title,
            result.url,
            result.snippet,
            result.engine,
            result.score + ((counts[normalize_url(result.url)] - 1) * 0.0001),
            result.published_at,
        )
        for result in best.values()
    ]
    return sorted(merged, key=lambda r: (-r.score, normalize_url(r.url)))
