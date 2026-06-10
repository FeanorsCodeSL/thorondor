"""Pure URL selection policy."""
from dataclasses import dataclass
import math
import re

from .normalize import canonical_host, host_for
from .types import DiscoveryResult


MAX_SELECTION_DIAGNOSTICS = 50


@dataclass
class SelectionDecision:
    result: DiscoveryResult
    selected: bool
    selection_reason: str | None = None
    filtered_reason: str | None = None
    lexical_score: float = 0.0


def _matches_host(host: str, pattern: str) -> bool:
    return host == pattern or host.endswith(f".{pattern}")


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _lexical_score(query: str | None, result: DiscoveryResult) -> float:
    query_terms = _tokens(query or "")
    if not query_terms:
        return 0.0
    haystack_terms = _tokens(f"{result.title} {result.snippet} {result.url}")
    if not haystack_terms:
        return 0.0
    return len(query_terms & haystack_terms) / len(query_terms)


def _per_domain_limit(max_urls: int, allowed: set[str] | None) -> int:
    if allowed is not None and len(allowed) <= 1:
        return max_urls
    if max_urls <= 2:
        return 1
    return min(3, max(1, math.ceil(max_urls / 3)))


class SelectionPolicyImpl:
    def select_with_diagnostics(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
        query: str | None = None,
    ) -> tuple[list[DiscoveryResult], list[SelectionDecision]]:
        blocked = {canonical_host(host) for host in blocklist}
        blocked.discard("")
        allowed = {canonical_host(host) for host in allowlist} if allowlist is not None else None
        if allowed is not None:
            allowed.discard("")

        candidates: list[tuple[DiscoveryResult, str, float]] = []
        decisions: list[SelectionDecision] = []
        for result in results:
            host = host_for(result.url)
            if not host:
                decisions.append(SelectionDecision(result, selected=False, filtered_reason="invalid_host"))
                continue
            if any(_matches_host(host, item) for item in blocked):
                decisions.append(SelectionDecision(result, selected=False, filtered_reason="blocked_domain"))
                continue
            if allowed is not None and not any(_matches_host(host, item) for item in allowed):
                decisions.append(SelectionDecision(result, selected=False, filtered_reason="outside_allowlist"))
                continue
            candidates.append((result, host, _lexical_score(query, result)))

        ordered = sorted(
            candidates,
            key=lambda item: (
                -(item[0].score + (item[2] * 0.25)),
                -item[0].score,
                item[0].url,
            ),
        )
        selected: list[DiscoveryResult] = []
        selected_keys: set[str] = set()
        host_counts: dict[str, int] = {}
        seen_engines: set[str] = set()
        unique_hosts = {host for _result, host, _score in candidates}
        per_domain_limit = max_urls if len(unique_hosts) <= 1 else _per_domain_limit(max_urls, allowed)

        def can_select(result: DiscoveryResult, host: str) -> bool:
            return result.url not in selected_keys and host_counts.get(host, 0) < per_domain_limit

        def add(result: DiscoveryResult, host: str, reason: str) -> None:
            selected.append(result)
            selected_keys.add(result.url)
            host_counts[host] = host_counts.get(host, 0) + 1
            if result.engine:
                seen_engines.add(result.engine)
            decisions.append(SelectionDecision(result, selected=True, selection_reason=reason))

        for result, host, _score in ordered:
            if len(selected) >= max_urls:
                break
            if result.engine and result.engine not in seen_engines and can_select(result, host):
                add(result, host, "engine_diversity")

        for result, host, _score in ordered:
            if len(selected) >= max_urls:
                break
            if host not in host_counts and can_select(result, host):
                add(result, host, "domain_diversity")

        for result, host, _score in ordered:
            if len(selected) >= max_urls:
                break
            if can_select(result, host):
                add(result, host, "score")

        for result, host, lexical_score in ordered:
            if result.url in selected_keys:
                continue
            reason = "domain_cap" if host_counts.get(host, 0) >= per_domain_limit else "rank_cap"
            decisions.append(
                SelectionDecision(
                    result,
                    selected=False,
                    filtered_reason=reason,
                    lexical_score=lexical_score,
                )
            )

        return selected, decisions[:MAX_SELECTION_DIAGNOSTICS]

    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
        query: str | None = None,
    ) -> list[DiscoveryResult]:
        selected, _decisions = self.select_with_diagnostics(results, max_urls, blocklist, allowlist, query)
        return selected
