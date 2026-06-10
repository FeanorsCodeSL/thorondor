"""Pure URL selection policy."""
from dataclasses import dataclass
import math
import re

from .normalize import canonical_host, host_for
from .types import DiscoveryResult


MAX_SELECTION_DIAGNOSTICS = 50
Candidate = tuple[DiscoveryResult, str, float]


@dataclass
class SelectionDecision:
    result: DiscoveryResult
    selected: bool
    selection_reason: str | None = None
    filtered_reason: str | None = None
    lexical_score: float = 0.0


@dataclass
class _SelectionState:
    selected: list[DiscoveryResult]
    selected_keys: set[str]
    host_counts: dict[str, int]
    seen_engines: set[str]
    decisions: list[SelectionDecision]


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


def _normalized_hosts(hosts: set[str] | None) -> set[str] | None:
    if hosts is None:
        return None
    normalized = {canonical_host(host) for host in hosts}
    normalized.discard("")
    return normalized


def _collect_candidates(
    results: list[DiscoveryResult],
    blocked: set[str],
    allowed: set[str] | None,
    query: str | None,
) -> tuple[list[Candidate], list[SelectionDecision]]:
    candidates: list[Candidate] = []
    decisions: list[SelectionDecision] = []
    for result in results:
        host = host_for(result.url)
        if not host:
            decisions.append(SelectionDecision(result, selected=False, filtered_reason="invalid_host"))
        elif any(_matches_host(host, item) for item in blocked):
            decisions.append(SelectionDecision(result, selected=False, filtered_reason="blocked_domain"))
        elif allowed is not None and not any(_matches_host(host, item) for item in allowed):
            decisions.append(SelectionDecision(result, selected=False, filtered_reason="outside_allowlist"))
        else:
            candidates.append((result, host, _lexical_score(query, result)))
    return candidates, decisions


def _ordered_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda item: (
            -(item[0].score + (item[2] * 0.25)),
            -item[0].score,
            item[0].url,
        ),
    )


def _candidate_limit(max_urls: int, allowed: set[str] | None, candidates: list[Candidate]) -> int:
    unique_hosts = {host for _result, host, _score in candidates}
    return max_urls if len(unique_hosts) <= 1 else _per_domain_limit(max_urls, allowed)


def _can_select(state: _SelectionState, result: DiscoveryResult, host: str, per_domain_limit: int) -> bool:
    return result.url not in state.selected_keys and state.host_counts.get(host, 0) < per_domain_limit


def _add_selection(state: _SelectionState, result: DiscoveryResult, host: str, reason: str) -> None:
    state.selected.append(result)
    state.selected_keys.add(result.url)
    state.host_counts[host] = state.host_counts.get(host, 0) + 1
    if result.engine:
        state.seen_engines.add(result.engine)
    state.decisions.append(SelectionDecision(result, selected=True, selection_reason=reason))


def _select_pass(
    ordered: list[Candidate],
    state: _SelectionState,
    max_urls: int,
    per_domain_limit: int,
    reason: str,
    predicate,
) -> None:
    for result, host, _score in ordered:
        if len(state.selected) >= max_urls:
            break
        if predicate(result, host, state) and _can_select(state, result, host, per_domain_limit):
            _add_selection(state, result, host, reason)


def _append_unselected(
    ordered: list[Candidate],
    state: _SelectionState,
    per_domain_limit: int,
) -> None:
    for result, host, lexical_score in ordered:
        if result.url in state.selected_keys:
            continue
        reason = "domain_cap" if state.host_counts.get(host, 0) >= per_domain_limit else "rank_cap"
        state.decisions.append(
            SelectionDecision(
                result,
                selected=False,
                filtered_reason=reason,
                lexical_score=lexical_score,
            )
        )


class SelectionPolicyImpl:
    def select_with_diagnostics(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
        query: str | None = None,
    ) -> tuple[list[DiscoveryResult], list[SelectionDecision]]:
        blocked = _normalized_hosts(blocklist) or set()
        allowed = _normalized_hosts(allowlist)
        candidates, decisions = _collect_candidates(results, blocked, allowed, query)
        ordered = _ordered_candidates(candidates)
        state = _SelectionState(
            selected=[],
            selected_keys=set(),
            host_counts={},
            seen_engines=set(),
            decisions=decisions,
        )
        per_domain_limit = _candidate_limit(max_urls, allowed, candidates)

        _select_pass(
            ordered,
            state,
            max_urls,
            per_domain_limit,
            "engine_diversity",
            lambda result, _host, current: bool(result.engine and result.engine not in current.seen_engines),
        )
        _select_pass(
            ordered,
            state,
            max_urls,
            per_domain_limit,
            "domain_diversity",
            lambda _result, host, current: host not in current.host_counts,
        )
        _select_pass(ordered, state, max_urls, per_domain_limit, "score", lambda _result, _host, _current: True)
        _append_unselected(ordered, state, per_domain_limit)

        return state.selected, state.decisions[:MAX_SELECTION_DIAGNOSTICS]

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
