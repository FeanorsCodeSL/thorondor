"""Heuristic content deduplication."""
import hashlib
import re

from .types import Page

_SPACE = re.compile(r"\s+")


def _fingerprint(markdown: str) -> str:
    normalized = _SPACE.sub(" ", markdown).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_dedup(pages: list[Page]) -> list[Page]:
    seen: set[str] = set()
    out: list[Page] = []
    for page in pages:
        key = _fingerprint(page.markdown)
        if key in seen:
            continue
        seen.add(key)
        out.append(page)
    return out
