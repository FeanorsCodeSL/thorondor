import json
import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .types import DocumentMetadata, MetadataCandidate, MetadataConflict, Page

MAX_HTML_METADATA_CHARS = 524_288
MAX_JSON_LD_CHARS = 65_536
MAX_CANDIDATES = 48
MAX_CANDIDATES_PER_FIELD = 8
MAX_TEXT_BYTES = 2_048
MAX_URL_BYTES = 8_192
UTC_OFFSET = "+00:00"

FIELDS = (
    "title",
    "description",
    "published_at",
    "modified_at",
    "author",
    "language",
    "declared_canonical_url",
)

SOURCE_PRIORITY = {
    "title": ("html_title", "json_ld", "html_meta", "page_metadata", "fetch_title"),
    "description": ("html_meta", "json_ld", "page_metadata"),
    "published_at": ("json_ld", "html_meta", "page_metadata", "discovery"),
    "modified_at": ("json_ld", "html_meta", "page_metadata", "sitemap"),
    "author": ("json_ld", "html_meta", "page_metadata"),
    "language": ("html_lang", "json_ld", "html_meta", "page_metadata"),
    "declared_canonical_url": ("html_canonical", "page_metadata", "html_meta", "json_ld"),
}

SOURCE_CONFIDENCE = {
    "html_title": "high",
    "html_canonical": "high",
    "html_lang": "high",
    "json_ld": "high",
    "html_meta": "high",
    "page_metadata": "medium",
    "discovery": "medium",
    "sitemap": "medium",
    "fetch_title": "low",
}

PAGE_KEYS = {
    "title": "title",
    "og:title": "title",
    "twitter:title": "title",
    "description": "description",
    "og:description": "description",
    "twitter:description": "description",
    "article:published_time": "published_at",
    "published_time": "published_at",
    "datepublished": "published_at",
    "publisheddate": "published_at",
    "publication_date": "published_at",
    "article:modified_time": "modified_at",
    "modified_time": "modified_at",
    "datemodified": "modified_at",
    "modification_date": "modified_at",
    "author": "author",
    "article:author": "author",
    "language": "language",
    "lang": "language",
    "inlanguage": "language",
    "canonical": "declared_canonical_url",
    "canonical_url": "declared_canonical_url",
    "og:url": "declared_canonical_url",
}


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.meta = []
        self.canonicals = []
        self.language = None
        self.json_ld = []
        self.in_json_ld = False
        self.script_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).lower(): value for key, value in attrs if value is not None}
        lowered = tag.lower()
        if lowered == "html" and self.language is None:
            self.language = attributes.get("lang")
        if lowered == "title":
            self.in_title = True
        if lowered == "meta":
            self._add_meta(attributes)
        if lowered == "link":
            self._add_canonical(attributes)
        if (
            lowered == "script"
            and (attributes.get("type") or "").lower() == "application/ld+json"
        ):
            self.in_json_ld = True
            self.script_parts = []

    def _add_meta(self, attributes: dict[str, str]) -> None:
        key = attributes.get("property") or attributes.get("name") or attributes.get("itemprop")
        content = attributes.get("content")
        if key and content and len(self.meta) < MAX_CANDIDATES:
            self.meta.append((key.lower(), content))

    def _add_canonical(self, attributes: dict[str, str]) -> None:
        rel = {part.lower() for part in (attributes.get("rel") or "").split()}
        href = attributes.get("href")
        if "canonical" in rel and href and len(self.canonicals) < MAX_CANDIDATES_PER_FIELD:
            self.canonicals.append(href)

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered == "title":
            self.in_title = False
        elif lowered == "script" and self.in_json_ld:
            value = "".join(self.script_parts)
            if value and len(self.json_ld) < MAX_CANDIDATES_PER_FIELD:
                self.json_ld.append(value[:MAX_JSON_LD_CHARS])
            self.in_json_ld = False
            self.script_parts = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_json_ld and sum(len(part) for part in self.script_parts) < MAX_JSON_LD_CHARS:
            self.script_parts.append(data)


def normalize_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        try:
            parsed = datetime.combine(
                date.fromisoformat(candidate),
                datetime.min.time(),
                UTC,
            )
        except ValueError:
            return None
        return parsed.isoformat(timespec="seconds").replace(UTC_OFFSET, "Z")
    try:
        normalized_candidate = (
            candidate[:-1] + UTC_OFFSET
            if candidate.endswith(("Z", "z"))
            else candidate
        )
        parsed = datetime.fromisoformat(normalized_candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None
    normalized = parsed.astimezone(UTC)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace(UTC_OFFSET, "Z")


def _bounded_text(value: object, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized.encode("utf-8")[:max_bytes].decode("utf-8", "ignore") or None


def _canonical_url(value: object, base_url: str | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = " ".join(value.split())
    if not candidate or len(candidate.encode("utf-8")) > MAX_URL_BYTES:
        return None
    try:
        if base_url is not None:
            candidate = urljoin(base_url, candidate)
        if len(candidate.encode("utf-8")) > MAX_URL_BYTES:
            return None
        parsed = urlparse(candidate)
    except Exception:
        return None
    return candidate if parsed.scheme in {"http", "https"} and parsed.hostname else None


def _language(value: object) -> str | None:
    candidate = _bounded_text(value, 64)
    if candidate is None or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", candidate):
        return None
    return candidate.lower()


def _author_values(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    authors = []
    for item in values[:MAX_CANDIDATES_PER_FIELD]:
        raw = item.get("name") if isinstance(item, dict) else item
        author = _bounded_text(raw, 512)
        if author:
            authors.append(author)
    return authors


def _json_ld_documents(value: object) -> list[dict]:
    if isinstance(value, str):
        if len(value) > MAX_JSON_LD_CHARS:
            return []
        try:
            value = json.loads(value)
        except (TypeError, ValueError, RecursionError):
            return []
    values = value if isinstance(value, list) else [value]
    documents = []
    for item in values[:MAX_CANDIDATES_PER_FIELD]:
        if not isinstance(item, dict):
            continue
        documents.append(item)
        graph = item.get("@graph")
        if isinstance(graph, list):
            documents.extend(
                node
                for node in graph[:MAX_CANDIDATES_PER_FIELD]
                if isinstance(node, dict)
            )
    return documents[:MAX_CANDIDATES]


def _json_ld_url(value: object, base_url: str | None = None) -> str | None:
    if isinstance(value, dict):
        value = value.get("@id") or value.get("url")
    return _canonical_url(value, base_url)


def _add_candidate(
    candidates: list[MetadataCandidate],
    counts: dict[str, int],
    field: str,
    value: object,
    source: str,
    canonical_base_url: str | None = None,
) -> None:
    if field not in FIELDS or len(candidates) >= MAX_CANDIDATES:
        return
    if counts.get(field, 0) >= MAX_CANDIDATES_PER_FIELD:
        return
    if field in {"published_at", "modified_at"}:
        normalized = normalize_timestamp(value)
    elif field == "declared_canonical_url":
        normalized = _canonical_url(value, canonical_base_url)
    elif field == "language":
        normalized = _language(value)
    else:
        normalized = _bounded_text(value)
    if normalized is None:
        return
    candidate = MetadataCandidate(field, normalized, source, SOURCE_CONFIDENCE[source])
    if candidate in candidates:
        return
    candidates.append(candidate)
    counts[field] = counts.get(field, 0) + 1


def _add_json_ld(
    candidates: list[MetadataCandidate],
    counts: dict[str, int],
    value: object,
    canonical_base_url: str | None = None,
) -> None:
    for document in _json_ld_documents(value):
        _add_candidate(
            candidates,
            counts,
            "title",
            document.get("headline") or document.get("name"),
            "json_ld",
        )
        _add_candidate(candidates, counts, "description", document.get("description"), "json_ld")
        _add_candidate(candidates, counts, "published_at", document.get("datePublished"), "json_ld")
        _add_candidate(candidates, counts, "modified_at", document.get("dateModified"), "json_ld")
        for author in _author_values(document.get("author")):
            _add_candidate(candidates, counts, "author", author, "json_ld")
        _add_candidate(candidates, counts, "language", document.get("inLanguage"), "json_ld")
        canonical = document.get("mainEntityOfPage") or document.get("url")
        _add_candidate(
            candidates,
            counts,
            "declared_canonical_url",
            _json_ld_url(canonical, canonical_base_url),
            "json_ld",
            canonical_base_url,
        )


def _priority(candidate: MetadataCandidate) -> tuple[int, str, str]:
    sources = SOURCE_PRIORITY[candidate.field]
    try:
        rank = sources.index(candidate.source)
    except ValueError:
        rank = len(sources)
    return rank, candidate.source, candidate.value


def _finalize(candidates: list[MetadataCandidate]) -> DocumentMetadata:
    selected = []
    conflicts = []
    for field in FIELDS:
        field_candidates = sorted(
            (candidate for candidate in candidates if candidate.field == field),
            key=_priority,
        )
        if not field_candidates:
            continue
        selected.append(field_candidates[0])
        if len({candidate.value for candidate in field_candidates}) > 1:
            conflicts.append(MetadataConflict(field, tuple(field_candidates)))
    return DocumentMetadata(tuple(selected), tuple(conflicts))


def _add_page_metadata(
    page: Page,
    candidates: list[MetadataCandidate],
    counts: dict[str, int],
    canonical_base_url: str,
) -> None:
    metadata_items = sorted(
        ((str(key).lower(), value) for key, value in page.metadata.items()),
        key=lambda item: item[0],
    )
    for key, value in metadata_items:
        field = PAGE_KEYS.get(key)
        if field == "author":
            for author in _author_values(value):
                _add_candidate(candidates, counts, field, author, "page_metadata")
        elif field:
            _add_candidate(
                candidates,
                counts,
                field,
                value,
                "page_metadata",
                canonical_base_url,
            )
        if key in {"json_ld", "json-ld", "jsonld"}:
            _add_json_ld(candidates, counts, value, canonical_base_url)


def _metadata_parser(source_html: str | None) -> _MetadataParser:
    parser = _MetadataParser()
    if not source_html:
        return parser
    try:
        parser.feed(source_html[:MAX_HTML_METADATA_CHARS])
        return parser
    except Exception:
        return _MetadataParser()


def _add_html_metadata(
    parser: _MetadataParser,
    candidates: list[MetadataCandidate],
    counts: dict[str, int],
    canonical_base_url: str,
) -> None:
    _add_candidate(candidates, counts, "title", "".join(parser.title_parts), "html_title")
    _add_candidate(candidates, counts, "language", parser.language, "html_lang")
    for key, value in parser.meta:
        field = PAGE_KEYS.get(key)
        if field:
            _add_candidate(
                candidates,
                counts,
                field,
                value,
                "html_meta",
                canonical_base_url,
            )
    for canonical in parser.canonicals:
        _add_candidate(
            candidates,
            counts,
            "declared_canonical_url",
            canonical,
            "html_canonical",
            canonical_base_url,
        )
    for json_ld in parser.json_ld:
        _add_json_ld(candidates, counts, json_ld, canonical_base_url)


def extract_document_metadata(
    page: Page,
    sitemap_lastmod: str | None = None,
) -> DocumentMetadata:
    candidates = []
    counts = {}
    canonical_base_url = page.final_url or page.requested_url or page.url
    _add_candidate(candidates, counts, "title", page.title, "fetch_title")
    _add_page_metadata(page, candidates, counts, canonical_base_url)
    _add_html_metadata(_metadata_parser(page.html), candidates, counts, canonical_base_url)
    _add_candidate(candidates, counts, "published_at", page.discovery_published_at, "discovery")
    _add_candidate(candidates, counts, "modified_at", sitemap_lastmod, "sitemap")
    return _finalize(candidates)
