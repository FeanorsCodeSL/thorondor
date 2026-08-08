"""Deterministic structured extraction from bounded untrusted HTML."""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from thorondor_contracts import MAX_FETCH_URL_BYTES

from .models import (
    MAX_STRUCTURED_DOCUMENT_FIELDS,
    MAX_STRUCTURED_DOCUMENTS,
    MAX_STRUCTURED_LINKS,
    MAX_STRUCTURED_SOURCE_BYTES,
    MAX_STRUCTURED_TABLE_CELLS,
    MAX_STRUCTURED_TABLE_ROWS,
    MAX_STRUCTURED_TABLES,
    MAX_STRUCTURED_TEXT_CHARS,
    StructuredDocument,
    StructuredDocumentField,
    StructuredExtraction,
    StructuredFormat,
    StructuredLink,
    StructuredSourceReference,
    StructuredTable,
    StructuredTableCell,
)
from .url_identity import build_document_identity

MAX_JSON_LD_BLOCK_BYTES = 65_536
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)"
    r"(?:\s*!important)?\s*(?:;|$)",
    re.I,
)


@dataclass
class _LinkDraft:
    start: int
    href: str
    title: str | None
    rel: list[str]
    truncated: bool = False
    text: list[str] = field(default_factory=list)


@dataclass
class _CellDraft:
    start: int
    header: bool
    row_span: int
    column_span: int
    text: list[str] = field(default_factory=list)


@dataclass
class _TableDraft:
    start: int
    rows: list[list[StructuredTableCell]] = field(default_factory=list)
    row: list[StructuredTableCell] | None = None
    cell: _CellDraft | None = None
    caption: list[str] | None = None
    caption_open: bool = False


@dataclass
class _JsonLdDraft:
    start: int
    parts: list[str] = field(default_factory=list)
    retained_bytes: int = 0
    truncated: bool = False


def _positive_span(value: str | None) -> int:
    try:
        parsed = int(value or "1")
    except ValueError:
        return 1
    return min(100, max(1, parsed))


def _bounded_text(parts: list[str]) -> tuple[str, bool]:
    value = " ".join("".join(parts).split())
    if len(value) <= MAX_STRUCTURED_TEXT_CHARS:
        return value, False
    return value[:MAX_STRUCTURED_TEXT_CHARS], True


def _hidden(attrs: dict[str, str | None]) -> bool:
    return (
        "hidden" in attrs
        or (attrs.get("aria-hidden") or "").casefold() == "true"
        or _HIDDEN_STYLE.search(attrs.get("style") or "") is not None
    )


def _origin(url: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return "", "", None
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (hostname or "").casefold(), port


class _StructuredParser(HTMLParser):
    def __init__(
        self,
        source: str,
        final_url: str,
        document_id: str,
        cleaned_markdown_sha256: str,
        source_html_sha256: str,
    ):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.final_url = final_url
        self.document_id = document_id
        self.cleaned_markdown_sha256 = cleaned_markdown_sha256
        self.source_html_sha256 = source_html_sha256
        self.line_offsets = [0]
        self.line_offsets.extend(match.end() for match in re.finditer("\n", source))
        self.stack: list[tuple[str, bool]] = []
        self.active_links: list[_LinkDraft] = []
        self.active_tables: list[_TableDraft] = []
        self.links: list[StructuredLink] = []
        self.tables: list[StructuredTable] = []
        self.omitted_links = 0
        self.omitted_tables = 0
        self.links_truncated = False
        self.tables_truncated = False
        self.cells = 0

    def _index(self) -> int:
        line, column = self.getpos()
        if line <= len(self.line_offsets):
            return min(len(self.source), self.line_offsets[line - 1] + column)
        return len(self.source)

    def _tag_end(self, start: int) -> int:
        end = self.source.find(">", start)
        return len(self.source) if end < 0 else end + 1

    def _is_hidden(self) -> bool:
        return bool(self.stack and self.stack[-1][1])

    def _reference(self, start: int, end: int) -> StructuredSourceReference:
        if end <= start:
            end = min(len(self.source), start + 1)
        return StructuredSourceReference(
            document_id=self.document_id,
            cleaned_markdown_sha256=self.cleaned_markdown_sha256,
            source_html_sha256=self.source_html_sha256,
            final_url=self.final_url,
            start_index=start,
            end_index=end,
        )

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attrs = {name.casefold(): value for name, value in attrs_list}
        parent_hidden = self._is_hidden()
        hidden = parent_hidden or tag in _IGNORED_TAGS or _hidden(attrs)
        start = self._index()
        if tag not in _VOID_TAGS:
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag == "a" and attrs.get("href"):
            raw_rel = attrs.get("rel") or ""
            rel_parts = raw_rel.split(None, 16)
            rel = [value[:128] for value in rel_parts[:16]]
            self.active_links.append(
                _LinkDraft(
                    start=start,
                    href=attrs["href"] or "",
                    title=attrs.get("title"),
                    rel=rel,
                    truncated=(
                        len(rel_parts) > 16
                        or any(len(value) > 128 for value in rel_parts[:16])
                    ),
                )
            )
        if tag == "table":
            self.active_tables.append(_TableDraft(start=start))
            return
        if not self.active_tables:
            return
        table = self.active_tables[-1]
        if tag == "caption":
            table.caption = []
            table.caption_open = True
        elif tag == "tr":
            self._finish_row(table, start)
            table.row = []
        elif tag in {"td", "th"}:
            if table.row is None:
                table.row = []
            self._finish_cell(table, start)
            table.cell = _CellDraft(
                start=start,
                header=tag == "th",
                row_span=_positive_span(attrs.get("rowspan")),
                column_span=_positive_span(attrs.get("colspan")),
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        stack_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index][0] == tag),
            None,
        )
        ending_hidden = stack_index is not None and self.stack[stack_index][1]
        end = self._tag_end(self._index())
        if not ending_hidden and tag == "a" and self.active_links:
            self._finish_link(end)
        if not ending_hidden and self.active_tables:
            table = self.active_tables[-1]
            if tag in {"td", "th"}:
                self._finish_cell(table, end)
            elif tag == "tr":
                self._finish_row(table, end)
            elif tag == "caption":
                table.caption_open = False
            elif tag == "table":
                self._finish_table(end)
        if stack_index is not None:
            del self.stack[stack_index:]

    def handle_data(self, data: str) -> None:
        if self._is_hidden():
            return
        if self.active_links:
            self.active_links[-1].text.append(data)
        if not self.active_tables:
            return
        table = self.active_tables[-1]
        if table.cell is not None:
            table.cell.text.append(data)
        elif table.caption_open and table.caption is not None:
            table.caption.append(data)

    def _finish_link(self, end: int) -> None:
        draft = self.active_links.pop()
        try:
            url = urljoin(self.final_url, draft.href)
            parsed = urlsplit(url)
        except (TypeError, ValueError):
            if draft.href.casefold().startswith(("http://", "https://", "//")):
                self._omit_link()
            return
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return
        candidate_origin = _origin(url)
        if not candidate_origin[1]:
            self._omit_link()
            return
        if len(url.encode("utf-8", "replace")) > MAX_FETCH_URL_BYTES:
            self._omit_link()
            return
        text, text_truncated = _bounded_text(draft.text)
        title, title_truncated = _bounded_text([draft.title]) if draft.title else (None, False)
        if len(self.links) >= MAX_STRUCTURED_LINKS:
            self._omit_link()
            return
        self.links.append(
            StructuredLink(
                url=url,
                text=text,
                title=title,
                rel=draft.rel,
                kind=(
                    "internal" if candidate_origin == _origin(self.final_url) else "external"
                ),
                source=self._reference(draft.start, end),
            )
        )
        self.links_truncated = (
            self.links_truncated or draft.truncated or text_truncated or title_truncated
        )

    def _omit_link(self) -> None:
        self.omitted_links += 1
        self.links_truncated = True

    def _finish_cell(self, table: _TableDraft, end: int) -> None:
        if table.cell is None:
            return
        draft = table.cell
        table.cell = None
        text, text_truncated = _bounded_text(draft.text)
        if self.cells >= MAX_STRUCTURED_TABLE_CELLS:
            self.omitted_tables += 1
            self.tables_truncated = True
            return
        if table.row is None:
            table.row = []
        table.row.append(
            StructuredTableCell(
                text=text,
                header=draft.header,
                row_span=draft.row_span,
                column_span=draft.column_span,
                source=self._reference(draft.start, end),
            )
        )
        self.cells += 1
        self.tables_truncated = self.tables_truncated or text_truncated

    def _finish_row(self, table: _TableDraft, end: int) -> None:
        self._finish_cell(table, end)
        if table.row is None:
            return
        if table.row:
            if len(table.rows) < MAX_STRUCTURED_TABLE_ROWS:
                table.rows.append(table.row)
            else:
                self.omitted_tables += len(table.row)
                self.tables_truncated = True
                self.cells -= len(table.row)
        table.row = None

    def _finish_table(self, end: int) -> None:
        table = self.active_tables.pop()
        self._finish_row(table, end)
        caption, caption_truncated = _bounded_text(table.caption or [])
        if not table.rows:
            return
        if len(self.tables) >= MAX_STRUCTURED_TABLES:
            self.omitted_tables += max(
                1,
                sum(len(row) for row in table.rows),
            )
            self.tables_truncated = True
            self.cells -= sum(len(row) for row in table.rows)
            return
        self.tables.append(
            StructuredTable(
                caption=caption or None,
                rows=table.rows,
                source=self._reference(table.start, end),
            )
        )
        self.tables_truncated = self.tables_truncated or caption_truncated

    def finish(self) -> None:
        end = len(self.source)
        while self.active_links:
            self._finish_link(end)
        while self.active_tables:
            self._finish_table(end)


class _JsonLdParser(HTMLParser):
    def __init__(
        self,
        source: str,
        final_url: str,
        document_id: str,
        cleaned_markdown_sha256: str,
        source_html_sha256: str,
    ):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.final_url = final_url
        self.document_id = document_id
        self.cleaned_markdown_sha256 = cleaned_markdown_sha256
        self.source_html_sha256 = source_html_sha256
        self.line_offsets = [0]
        self.line_offsets.extend(match.end() for match in re.finditer("\n", source))
        self.active: _JsonLdDraft | None = None
        self.documents: list[StructuredDocument] = []
        self.omitted = 0
        self.truncated = False
        self.blocks_seen = 0

    def _index(self) -> int:
        line, column = self.getpos()
        if line <= len(self.line_offsets):
            return min(len(self.source), self.line_offsets[line - 1] + column)
        return len(self.source)

    def _tag_end(self, start: int) -> int:
        end = self.source.find(">", start)
        return len(self.source) if end < 0 else end + 1

    def _reference(self, start: int, end: int) -> StructuredSourceReference:
        return StructuredSourceReference(
            document_id=self.document_id,
            cleaned_markdown_sha256=self.cleaned_markdown_sha256,
            source_html_sha256=self.source_html_sha256,
            final_url=self.final_url,
            start_index=start,
            end_index=max(start + 1, end),
        )

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script" or self.active is not None:
            return
        attrs = {name.casefold(): value for name, value in attrs_list}
        if (attrs.get("type") or "").casefold() == "application/ld+json":
            self.blocks_seen += 1
            self.active = _JsonLdDraft(
                start=self._index(),
                truncated=self.blocks_seen > MAX_STRUCTURED_DOCUMENTS,
            )

    def handle_data(self, data: str) -> None:
        if self.active is None:
            return
        remaining = MAX_JSON_LD_BLOCK_BYTES - self.active.retained_bytes
        if remaining <= 0:
            self.active.truncated = True
            return
        encoded = data.encode("utf-8", "replace")
        retained = encoded[:remaining].decode("utf-8", "ignore")
        self.active.parts.append(retained)
        self.active.retained_bytes += len(retained.encode("utf-8"))
        if len(encoded) > remaining:
            self.active.truncated = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self.active is None:
            return
        draft = self.active
        self.active = None
        end = self._tag_end(self._index())
        if draft.truncated:
            self.omitted += 1
            self.truncated = True
            return
        try:
            value = json.loads(
                "".join(draft.parts),
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, RecursionError):
            self.omitted += 1
            self.truncated = True
            return
        source = self._reference(draft.start, end)
        for node in _json_ld_nodes(value):
            document, document_truncated = _structured_document(
                node,
                source,
                self.final_url,
            )
            if document_truncated:
                self.omitted += 1
                self.truncated = True
            if document is None:
                continue
            if len(self.documents) >= MAX_STRUCTURED_DOCUMENTS:
                self.omitted += 1
                self.truncated = True
                continue
            self.documents.append(document)

    def finish(self) -> None:
        if self.active is not None:
            self.omitted += 1
            self.truncated = True
            self.active = None


def _json_ld_nodes(value: object) -> list[dict[str, object]]:
    roots = value if isinstance(value, list) else [value]
    nodes: list[dict[str, object]] = []
    for root in roots[:MAX_STRUCTURED_DOCUMENTS]:
        if not isinstance(root, dict):
            continue
        nodes.append(root)
        graph = root.get("@graph")
        if isinstance(graph, list):
            nodes.extend(
                item
                for item in graph[:MAX_STRUCTURED_DOCUMENTS]
                if isinstance(item, dict)
            )
    return nodes[: MAX_STRUCTURED_DOCUMENTS * 2]


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _document_kind(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    normalized = {item.casefold() for item in values if isinstance(item, str)}
    article = bool(normalized & {"article", "newsarticle", "blogposting"})
    product = "product" in normalized
    if article == product:
        return None
    if article:
        return "article"
    if product:
        return "product"
    return None


def _document_text(value: object, depth: int = 0) -> tuple[str | None, bool]:
    if depth > 4:
        return None, True
    if isinstance(value, dict):
        nested = value.get("name") or value.get("@value") or value.get("@id")
        if isinstance(nested, (dict, list)):
            return _document_text(nested, depth + 1)
        value = nested
    if isinstance(value, list):
        item_count = len(value)
        items = value[:4]
        values = [_document_text(item, depth + 1) for item in items]
        value = ", ".join(item for item, _truncated in values if item)
        list_truncated = len(items) < item_count or any(
            truncated for _item, truncated in values
        )
    else:
        list_truncated = False
    if (
        not isinstance(value, (str, int, float))
        or isinstance(value, bool)
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        return None, list_truncated
    normalized = " ".join(str(value).split())
    truncated = list_truncated or len(normalized) > MAX_STRUCTURED_TEXT_CHARS
    return normalized[:MAX_STRUCTURED_TEXT_CHARS] or None, truncated


def _document_url(value: object, final_url: str) -> tuple[str | None, bool]:
    if isinstance(value, dict):
        value = value.get("@id") or value.get("url")
    if not isinstance(value, str):
        return None, False
    try:
        candidate = urljoin(final_url, value)
        parsed = urlsplit(candidate)
        _validated_port = parsed.port
    except (TypeError, ValueError):
        return None, False
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None, False
    if len(candidate.encode("utf-8", "replace")) > MAX_FETCH_URL_BYTES:
        return None, True
    return candidate, False


def _product_offers(node: dict[str, object]) -> dict[str, object]:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = next((item for item in offers[:4] if isinstance(item, dict)), None)
    return offers if isinstance(offers, dict) else {}


def _document_fields(kind: str, node: dict[str, object]) -> list[tuple[str, object]]:
    if kind == "article":
        return [
            ("name", node.get("headline") or node.get("name")),
            ("description", node.get("description")),
            ("author", node.get("author")),
            ("date_published", node.get("datePublished")),
            ("date_modified", node.get("dateModified")),
            ("url", node.get("mainEntityOfPage") or node.get("url")),
        ]
    offers = _product_offers(node)
    return [
        ("name", node.get("name")),
        ("description", node.get("description")),
        ("sku", node.get("sku")),
        ("brand", node.get("brand")),
        ("availability", offers.get("availability")),
        ("price", offers.get("price")),
        ("price_currency", offers.get("priceCurrency")),
        ("url", offers.get("url") or node.get("url")),
    ]


def _normalized_document_fields(
    raw_fields: list[tuple[str, object]],
    source: StructuredSourceReference,
    final_url: str,
) -> tuple[list[StructuredDocumentField], bool]:
    fields = []
    truncated = False
    for name, value in raw_fields[:MAX_STRUCTURED_DOCUMENT_FIELDS]:
        if name == "url":
            normalized, field_truncated = _document_url(value, final_url)
        else:
            normalized, field_truncated = _document_text(value)
        truncated = truncated or field_truncated
        if normalized is not None:
            fields.append(StructuredDocumentField(name=name, value=normalized, source=source))
    return fields, truncated


def _structured_document(
    node: dict[str, object],
    source: StructuredSourceReference,
    final_url: str,
) -> tuple[StructuredDocument | None, bool]:
    kind = _document_kind(node.get("@type"))
    if kind is None:
        return None, False
    fields, truncated = _normalized_document_fields(
        _document_fields(kind, node),
        source,
        final_url,
    )
    if not fields:
        return None, truncated
    return StructuredDocument(kind=kind, fields=fields, source=source), truncated


def _source_prefix(source_html: str) -> tuple[str, int, str]:
    encoded = source_html.encode("utf-8", "replace")
    digest = hashlib.sha256(encoded).hexdigest()
    if len(encoded) <= MAX_STRUCTURED_SOURCE_BYTES:
        return source_html, 0, digest
    prefix = encoded[:MAX_STRUCTURED_SOURCE_BYTES].decode("utf-8", "ignore")
    retained = len(prefix.encode("utf-8"))
    return prefix, len(encoded) - retained, digest


def _parsed_structured_source(
    source: str,
    final_url: str,
    document_id: str,
    markdown_sha256: str,
    source_digest: str,
) -> tuple[_StructuredParser, bool]:
    parser = _StructuredParser(
        source,
        final_url,
        document_id,
        markdown_sha256,
        source_digest,
    )
    malformed = False
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        malformed = True
    parser.finish()
    return parser, malformed


def _parsed_json_ld_source(
    source: str,
    final_url: str,
    document_id: str,
    markdown_sha256: str,
    source_digest: str,
    enabled: bool,
) -> _JsonLdParser:
    parser = _JsonLdParser(
        source,
        final_url,
        document_id,
        markdown_sha256,
        source_digest,
    )
    if not enabled:
        return parser
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        parser.truncated = True
    parser.finish()
    return parser


def _collection_status(truncated: bool, populated: bool) -> str:
    if truncated:
        return "truncated"
    return "ok" if populated else "empty"


def _structured_result(
    format_name: StructuredFormat,
    parser: _StructuredParser,
    json_ld_parser: _JsonLdParser,
    document_id: str,
    omitted_source_bytes: int,
    malformed: bool,
) -> StructuredExtraction:
    if format_name == "links":
        truncated = omitted_source_bytes > 0 or parser.links_truncated or malformed
        return StructuredExtraction(
            format="links",
            status=_collection_status(truncated, bool(parser.links)),
            document_id=document_id,
            links=parser.links,
            omitted_items=parser.omitted_links,
            omitted_source_bytes=omitted_source_bytes,
        )
    if format_name == "tables":
        truncated = omitted_source_bytes > 0 or parser.tables_truncated or malformed
        return StructuredExtraction(
            format="tables",
            status=_collection_status(truncated, bool(parser.tables)),
            document_id=document_id,
            tables=parser.tables,
            omitted_items=parser.omitted_tables,
            omitted_source_bytes=omitted_source_bytes,
        )
    if format_name == "json_ld":
        truncated = omitted_source_bytes > 0 or json_ld_parser.truncated
        return StructuredExtraction(
            format="json_ld",
            status=_collection_status(truncated, bool(json_ld_parser.documents)),
            document_id=document_id,
            documents=json_ld_parser.documents,
            omitted_items=json_ld_parser.omitted,
            omitted_source_bytes=omitted_source_bytes,
        )
    return StructuredExtraction(format="json_schema", status="unsupported")


def extract_structured(
    source_html: str,
    final_url: str,
    cleaned_markdown: str,
    formats: list[StructuredFormat] | tuple[StructuredFormat, ...],
) -> list[StructuredExtraction]:
    source, omitted_source_bytes, source_digest = _source_prefix(source_html)
    identity = build_document_identity(final_url, cleaned_markdown)
    parser, malformed = _parsed_structured_source(
        source,
        str(identity.final_url),
        identity.document_id,
        identity.cleaned_markdown_sha256,
        source_digest,
    )
    json_ld_parser = _parsed_json_ld_source(
        source,
        str(identity.final_url),
        identity.document_id,
        identity.cleaned_markdown_sha256,
        source_digest,
        "json_ld" in formats,
    )
    return [
        _structured_result(
            format_name,
            parser,
            json_ld_parser,
            identity.document_id,
            omitted_source_bytes,
            malformed,
        )
        for format_name in formats
    ]
