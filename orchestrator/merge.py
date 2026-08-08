"""Merge and URL-deduplicate discovery result sets."""
from .normalize import normalize_url
from .types import DiscoveryContribution, DiscoveryResult


def _contributions(
    result: DiscoveryResult,
    subquery: str,
) -> tuple[DiscoveryContribution, ...]:
    if result.contributions:
        return result.contributions
    return (DiscoveryContribution(subquery, result.engine, None, result.score),)


def _merge_result(
    result: DiscoveryResult,
    subquery: str,
    best: dict[str, DiscoveryResult],
    contributions: dict[str, dict[tuple[str, str, int | None], DiscoveryContribution]],
) -> None:
    key = normalize_url(result.url)
    contribution_map = contributions.setdefault(key, {})
    for contribution in _contributions(result, subquery):
        contribution_key = (
            contribution.subquery,
            contribution.engine,
            contribution.position,
        )
        existing = contribution_map.get(contribution_key)
        if existing is None or contribution.score > existing.score:
            contribution_map[contribution_key] = contribution
    existing_result = best.get(key)
    if existing_result is None or result.score > existing_result.score:
        best[key] = result


def _merged_result(
    result: DiscoveryResult,
    contributions: dict[str, dict[tuple[str, str, int | None], DiscoveryContribution]],
) -> DiscoveryResult:
    ordered = sorted(
        contributions[normalize_url(result.url)].values(),
        key=lambda item: (
            item.subquery,
            item.position if item.position is not None else 2**31,
            item.engine,
        ),
    )
    return DiscoveryResult(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        engine=result.engine,
        score=result.score,
        published_at=result.published_at,
        contributions=tuple(ordered),
    )


def merge_dedup(
    result_sets: list[list[DiscoveryResult]],
    subqueries: list[str] | None = None,
) -> list[DiscoveryResult]:
    best: dict[str, DiscoveryResult] = {}
    contributions: dict[str, dict[tuple[str, str, int | None], DiscoveryContribution]] = {}
    for set_index, results in enumerate(result_sets):
        subquery = (
            subqueries[set_index]
            if subqueries is not None and set_index < len(subqueries)
            else f"result_set:{set_index}"
        )
        for result in results:
            _merge_result(result, subquery, best, contributions)
    merged = [_merged_result(result, contributions) for result in best.values()]
    return sorted(merged, key=lambda r: (-r.score, normalize_url(r.url)))
