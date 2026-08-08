import re
from dataclasses import dataclass

from .types import ScoredChunk

EVIDENCE_QUALITY_STRATEGY = "evidence-quality@1"
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_NAVIGATION_TERMS = {
    "home",
    "menu",
    "search",
    "login",
    "log in",
    "sign in",
    "sign up",
    "register",
    "skip to content",
}
_FOOTER_TERMS = (
    "all rights reserved",
    "copyright",
    "privacy policy",
    "terms of service",
    "cookie policy",
)
_GENERIC_LINK_LABELS = _NAVIGATION_TERMS | {
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "click here",
    "read more",
    "learn more",
    "next",
    "previous",
}


@dataclass(frozen=True)
class EvidenceQualityOutcome:
    scored: list[ScoredChunk]
    dropped_by_rule: dict[str, int]
    strategy: str = EVIDENCE_QUALITY_STRATEGY


def _navigation_boilerplate(text: str) -> bool:
    if len(text) > 300:
        return False
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    matches = sum(line in _NAVIGATION_TERMS for line in lines)
    return matches >= 3 and matches / len(lines) >= 0.6


def _footer_boilerplate(text: str) -> bool:
    if len(text) > 500:
        return False
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    matches = sum(any(term in line for term in _FOOTER_TERMS) for line in lines)
    return matches >= 2 and sum(map(len, lines)) / len(lines) < 60


def _link_dominated(text: str) -> bool:
    links = _LINK.findall(text)
    if len(links) < 6:
        return False
    outside = _LINK.sub(lambda match: match.group(1), text)
    generic = sum(label.strip().lower() in _GENERIC_LINK_LABELS for label in links)
    return len(outside.strip()) < 120 and generic / len(links) >= 0.8


def _drop_reason(text: str) -> str | None:
    if _navigation_boilerplate(text):
        return "navigation_boilerplate"
    if _footer_boilerplate(text):
        return "footer_boilerplate"
    if _link_dominated(text):
        return "link_dominated"
    return None


def filter_evidence_quality(scored: list[ScoredChunk]) -> EvidenceQualityOutcome:
    accepted = []
    dropped_by_rule: dict[str, int] = {}
    for item in scored:
        reason = _drop_reason(item.chunk.text)
        if reason is None:
            accepted.append(item)
        else:
            dropped_by_rule[reason] = dropped_by_rule.get(reason, 0) + 1
    return EvidenceQualityOutcome(accepted, dropped_by_rule)
