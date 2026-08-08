from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from xml.etree import ElementTree


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    modified_at: str | None
    priority: float | None
    order: int


@dataclass(frozen=True)
class SitemapDocument:
    kind: str | None
    entries: tuple[SitemapEntry, ...]
    truncated: bool
    reason: str | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name and child.text:
            value = child.text.strip()
            return value or None
    return None


def _valid_datetime(value: str | None) -> tuple[str | None, datetime | None]:
    if not value:
        return None, None
    try:
        if len(value) == 10:
            parsed_date = date.fromisoformat(value)
            return value, datetime.combine(parsed_date, time.min, tzinfo=UTC)
    except ValueError:
        return None, None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        return None, None
    return value, parsed


def _valid_priority(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        priority = float(value)
    except ValueError:
        return None
    return priority if 0 <= priority <= 1 else None


def _root_fragment(content: str) -> str | None:
    lowered = content.casefold()
    matches: list[tuple[int, str]] = []
    for name in ("urlset", "sitemapindex"):
        offset = 0
        while True:
            start = lowered.find(f"<{name}", offset)
            if start < 0:
                break
            boundary = start + len(name) + 1
            if boundary < len(lowered) and (
                lowered[boundary].isspace() or lowered[boundary] in {">", "/"}
            ):
                matches.append((start, name))
                break
            offset = boundary
    if not matches:
        return None
    start, name = min(matches)
    end_tag = f"</{name}>"
    end = lowered.find(end_tag, start)
    if end < 0:
        return None
    return content[start : end + len(end_tag)]


def _sort_key(entry: SitemapEntry):
    _value, parsed = _valid_datetime(entry.modified_at)
    timestamp = parsed.timestamp() if parsed is not None else float("-inf")
    priority = entry.priority if entry.priority is not None else float("-inf")
    return (
        parsed is not None,
        timestamp,
        entry.priority is not None,
        priority,
        -entry.order,
    )


def _parse_root(content: str) -> tuple[ElementTree.Element | None, str | None]:
    try:
        return ElementTree.fromstring(content), None
    except ElementTree.ParseError:
        fragment = _root_fragment(content)
    if fragment is None:
        return None, "malformed_xml"
    try:
        return ElementTree.fromstring(fragment), None
    except ElementTree.ParseError:
        return None, "malformed_xml"


def _supported_root(root: ElementTree.Element) -> tuple[ElementTree.Element | None, str | None]:
    root_name = _local_name(root.tag)
    if root_name in {"urlset", "sitemapindex"}:
        return root, root_name
    nested = next(
        (
            element
            for element in root.iter()
            if _local_name(element.tag) in {"urlset", "sitemapindex"}
        ),
        None,
    )
    return (nested, _local_name(nested.tag)) if nested is not None else (None, None)


def _sitemap_entries(root: ElementTree.Element, child_name: str) -> list[SitemapEntry]:
    entries = []
    for child in root:
        if _local_name(child.tag) != child_name:
            continue
        url = _child_text(child, "loc")
        if not url:
            continue
        modified_at, _parsed = _valid_datetime(_child_text(child, "lastmod"))
        priority = _valid_priority(_child_text(child, "priority"))
        entries.append(SitemapEntry(url, modified_at, priority, len(entries)))
    return entries


def parse_sitemap(content: str, *, max_bytes: int, max_entries: int) -> SitemapDocument:
    if len(content.encode("utf-8")) > max_bytes:
        return SitemapDocument(None, (), False, "body_too_large")
    upper = content.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        return SitemapDocument(None, (), False, "unsafe_xml")
    root, error = _parse_root(content)
    if error is not None:
        return SitemapDocument(None, (), False, error)
    root, root_name = _supported_root(root)
    if root is None or root_name is None:
        return SitemapDocument(None, (), False, "unsupported_root")
    child_name = "url" if root_name == "urlset" else "sitemap"
    entries = _sitemap_entries(root, child_name)
    entries.sort(key=_sort_key, reverse=True)
    truncated = len(entries) > max_entries
    return SitemapDocument(
        "urlset" if root_name == "urlset" else "index",
        tuple(entries[:max_entries]),
        truncated,
    )
