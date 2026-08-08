import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from thorondor_contracts import TargetLocator, TargetState, TargetWatch

from .models import TargetWatchResult, TargetWatchSnapshot
from .page_cache import StoredTargetSnapshot

_SPACE = re.compile(r"\s+")
_CSS_TOKEN = re.compile(
    r"^(?P<tag>[A-Za-z][A-Za-z0-9-]*)?"
    r"(?P<id>#[A-Za-z_][A-Za-z0-9_-]*)?"
    r"(?P<classes>(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)"
    r"(?P<attr>\[[A-Za-z_:][A-Za-z0-9_.:-]*(?:=(?:\"[^\"]*\"|'[^']*'|[^\]]+))?\])?$"
)
_ATTRIBUTE = re.compile(
    r"^\[(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)(?:=(?P<value>.*))?\]$"
)


def normalize_text(value: str) -> str:
    return _SPACE.sub(" ", html.unescape(value)).strip()


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: "_Node | None"
    children: list["_Node"] = field(default_factory=list)
    content: list[object] = field(default_factory=list)
    resolved_text: str = ""

    def text(self) -> str:
        return self.resolved_text


class _TreeParser(HTMLParser):
    def __init__(self, max_nodes: int, max_depth: int):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, None)
        self.stack = [self.root]
        self.nodes: list[_Node] = []
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.truncated = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if len(self.nodes) >= self.max_nodes or len(self.stack) > self.max_depth:
            self.truncated = True
            return
        normalized_attrs = {
            str(name).casefold(): normalize_text(value or "")
            for name, value in attrs
            if name
        }
        node = _Node(tag.casefold(), normalized_attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack[-1].content.append(node)
        self.nodes.append(node)
        if tag.casefold() not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.casefold():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self.truncated:
            self.stack[-1].content.append(data)


def _role(node: _Node) -> str | None:
    declared = node.attrs.get("role")
    if declared:
        return declared.casefold()
    if node.tag == "button":
        return "button"
    if node.tag == "a" and "href" in node.attrs:
        return "link"
    if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "heading"
    if node.tag == "select":
        return "combobox"
    if node.tag == "textarea":
        return "textbox"
    if node.tag == "img":
        return "img"
    if node.tag == "input":
        input_type = node.attrs.get("type", "text").casefold()
        return {
            "button": "button",
            "submit": "button",
            "reset": "button",
            "checkbox": "checkbox",
            "radio": "radio",
        }.get(input_type, "textbox")
    return None


def _accessible_name(node: _Node) -> str:
    return normalize_text(
        node.attrs.get("aria-label")
        or node.attrs.get("alt")
        or node.attrs.get("title")
        or node.attrs.get("value")
        or node.text()
    )


def _inside_section(node: _Node, section: str | None) -> bool:
    if not section:
        return True
    wanted = normalize_text(section).casefold()
    current: _Node | None = node
    while current is not None:
        if current.tag in {"section", "article", "main", "aside", "div"}:
            label = normalize_text(
                current.attrs.get("aria-label")
                or current.attrs.get("data-section")
                or current.attrs.get("id")
                or ""
            ).casefold()
            if label == wanted:
                return True
            headings = [
                child.text().casefold()
                for child in current.children
                if child.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
            ]
            if wanted in headings:
                return True
        current = current.parent
    return False


def _attribute_matches(node: _Node, attribute: str | None) -> bool:
    if attribute is None:
        return True
    parsed = _ATTRIBUTE.fullmatch(attribute)
    if parsed is None:
        return False
    name = parsed.group("name").casefold()
    if name not in node.attrs:
        return False
    expected = parsed.group("value")
    if expected is None:
        return True
    return node.attrs[name] == expected.strip().strip("\"'")


def _css_matches(node: _Node, selector: str) -> bool:
    match = _CSS_TOKEN.fullmatch(selector.strip())
    if match is None:
        return False
    tag = match.group("tag")
    if tag and node.tag != tag.casefold():
        return False
    element_id = match.group("id")
    if element_id and node.attrs.get("id") != element_id[1:]:
        return False
    classes = [value for value in match.group("classes").split(".") if value]
    actual_classes = set(node.attrs.get("class", "").split())
    if any(value not in actual_classes for value in classes):
        return False
    attribute = match.group("attr")
    return _attribute_matches(node, attribute) and bool(tag or element_id or classes or attribute)


def _matches(node: _Node, locator: TargetLocator) -> bool:
    if not _inside_section(node, locator.section):
        return False
    if locator.css is not None:
        return _css_matches(node, locator.css)
    if locator.role is not None:
        if _role(node) != locator.role:
            return False
        if locator.name is not None:
            return _accessible_name(node).casefold() == normalize_text(locator.name).casefold()
        return True
    wanted = normalize_text(locator.text or "").casefold()
    return wanted in node.text().casefold()


def resolve_target(
    source_html: str | None,
    locator: TargetLocator,
    max_nodes: int = 10_000,
    max_depth: int = 128,
) -> StoredTargetSnapshot:
    if not source_html:
        return StoredTargetSnapshot("unsupported", None, {}, 0)
    parser = _TreeParser(max_nodes, max_depth)
    try:
        parser.feed(source_html)
        parser.close()
    except Exception:
        return StoredTargetSnapshot("unsupported", None, {}, 0)
    if parser.truncated:
        return StoredTargetSnapshot("unsupported", None, {}, 0)
    for node in reversed([parser.root, *parser.nodes]):
        node.resolved_text = normalize_text(
            " ".join(
                part if isinstance(part, str) else part.resolved_text
                for part in node.content
            )
        )
    matches = [node for node in parser.nodes if _matches(node, locator)]
    if locator.text is not None:
        matches = [
            node
            for node in matches
            if not any(_matches(child, locator) for child in node.children)
        ]
    if not matches:
        return StoredTargetSnapshot("missing", None, {}, 0)
    if len(matches) != 1:
        return StoredTargetSnapshot("ambiguous", None, {}, 0)
    node = matches[0]
    attributes = {
        name: value[:1024]
        for name, value in sorted(node.attrs.items())[:32]
    }
    return StoredTargetSnapshot("found", node.text()[:1024], attributes, 0)


def _public(snapshot: StoredTargetSnapshot | None) -> TargetWatchSnapshot | None:
    if snapshot is None or snapshot.resolution != "found":
        return None
    return TargetWatchSnapshot(text=snapshot.text or "", attributes=snapshot.attributes)


def _state_matches(snapshot: StoredTargetSnapshot | None, state: TargetState, mode: str) -> bool:
    if snapshot is None or snapshot.resolution != "found":
        return False
    if state.text is not None:
        actual = normalize_text(snapshot.text or "").casefold()
        expected = normalize_text(state.text).casefold()
    else:
        actual = normalize_text(
            snapshot.attributes.get((state.attribute or "").casefold(), "")
        ).casefold()
        expected = normalize_text(state.value or "").casefold()
    return actual == expected if mode == "exact" else expected in actual


def evaluate_watch(
    watch: TargetWatch,
    previous: StoredTargetSnapshot | None,
    current: StoredTargetSnapshot,
) -> TargetWatchResult:
    if current.resolution in {"ambiguous", "unsupported"}:
        return TargetWatchResult(
            resolution=current.resolution,
            state=None,
            previous=_public(previous),
            current=None,
            condition_met=False,
        )
    if current.resolution == "missing":
        state = "removed" if previous and previous.resolution == "found" else None
        return TargetWatchResult(
            resolution="missing",
            state=state,
            previous=_public(previous),
            current=None,
            condition_met=False,
        )
    if previous is None or previous.resolution != "found":
        state = "new"
    elif previous.text == current.text and previous.attributes == current.attributes:
        state = "same"
    else:
        state = "changed"
    previous_matches = (
        _state_matches(previous, watch.expected, watch.match)
        if watch.expected is not None
        else previous is not None
        and previous.resolution == "found"
        and not _state_matches(previous, watch.desired, watch.match)
    )
    condition_met = (
        state == "changed"
        and previous_matches
        and _state_matches(current, watch.desired, watch.match)
    )
    return TargetWatchResult(
        resolution="found",
        state=state,
        previous=_public(previous),
        current=_public(current),
        condition_met=condition_met,
    )
