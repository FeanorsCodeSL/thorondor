"""Merge and URL-deduplicate discovery result sets."""
from .normalize import normalize_url
from .types import DiscoveryResult


def merge_dedup(result_sets: list[list[DiscoveryResult]]) -> list[DiscoveryResult]:
    best: dict[str, DiscoveryResult] = {}
    for results in result_sets:
        for result in results:
            key = normalize_url(result.url)
            existing = best.get(key)
            if existing is None or result.score > existing.score:
                best[key] = result
    return sorted(best.values(), key=lambda r: (-r.score, normalize_url(r.url)))
