from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_FETCH_URLS = 4
MAX_FETCH_URL_BYTES = 8192
FETCH_CAPABILITIES = (
    "markdown",
    "javascript",
    "links",
    "metadata",
    "raw_html",
    "pdf",
    "document",
)
FetchCapability = Literal[
    "markdown",
    "javascript",
    "links",
    "metadata",
    "raw_html",
    "pdf",
    "document",
]
StructuredFormat = Literal["links", "tables", "json_ld", "json_schema"]
DEFAULT_FETCH_CAPABILITIES = ("markdown", "javascript", "links", "metadata")
MAX_TARGET_SELECTOR_CHARS = 256
MAX_TARGET_TEXT_CHARS = 1024


class TargetLocator(BaseModel):
    css: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_SELECTOR_CHARS)
    role: Literal[
        "button",
        "link",
        "heading",
        "checkbox",
        "radio",
        "textbox",
        "combobox",
        "img",
    ] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_TEXT_CHARS)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_TEXT_CHARS)
    section: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_TEXT_CHARS)

    @model_validator(mode="after")
    def validate_locator(self) -> "TargetLocator":
        modes = sum(value is not None for value in (self.css, self.role, self.text))
        if modes != 1:
            raise ValueError("target requires exactly one of css, role, or text")
        if self.name is not None and self.role is None:
            raise ValueError("target name requires role")
        if self.text is not None and self.section is None:
            raise ValueError("text targets require a named section")
        if self.css is not None and any(
            token in self.css for token in (",", ":", ">", "+", "~", "*", " ")
        ):
            raise ValueError("css target must be one bounded compound selector")
        return self


class TargetState(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_TEXT_CHARS)
    attribute: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z_:][A-Za-z0-9_.:-]*$",
    )
    value: str | None = Field(default=None, max_length=MAX_TARGET_TEXT_CHARS)

    @model_validator(mode="after")
    def validate_state(self) -> "TargetState":
        if self.text is not None and (self.attribute is not None or self.value is not None):
            raise ValueError("target state must use text or attribute/value")
        if self.text is None and (self.attribute is None or self.value is None):
            raise ValueError("target state requires text or attribute/value")
        return self


class TargetWatch(BaseModel):
    target: TargetLocator
    desired: TargetState
    expected: TargetState | None = None
    match: Literal["exact", "contains"] = "exact"

    @model_validator(mode="after")
    def validate_contains_state(self) -> "TargetWatch":
        if self.match == "contains":
            for state in (self.expected, self.desired):
                if state is not None and state.attribute is not None and not state.value.strip():
                    raise ValueError("contains attribute states require a non-blank value")
        return self
DEFAULT_MAX_REQUEST_BODY_BYTES = 32768
DEFAULT_MAX_RESPONSE_BODY_BYTES = 2097152
DEFAULT_SEARCH_ROUTE_DEADLINE_S = 120.0
DEFAULT_FETCH_ROUTE_DEADLINE_S = 60.0
DEFAULT_MAP_ROUTE_DEADLINE_S = 90.0
DEFAULT_SITE_CRAWL_ROUTE_DEADLINE_S = 120.0
DEFAULT_DISCOVERY_STAGE_DEADLINE_S = 20.0
DEFAULT_CRAWL_STAGE_DEADLINE_S = 45.0
DEFAULT_CHUNK_STAGE_DEADLINE_S = 45.0
DEFAULT_RERANK_STAGE_DEADLINE_S = 30.0
DEFAULT_MAX_INFLIGHT_SEARCHES = 4
DEFAULT_MAX_INFLIGHT_FETCHES = 8
DEFAULT_MAX_INFLIGHT_MAPS = 4
DEFAULT_MAX_INFLIGHT_CRAWLS = 2
DEFAULT_ADMISSION_WAIT_S = 0.05
DEFAULT_ADMISSION_RETRY_AFTER_S = 1
DEFAULT_MAX_INTERNAL_FANOUT = 20
DEFAULT_MAX_CONTENT_BYTES = 262144
DEFAULT_CHUNK_CONCURRENCY = 4
RESOURCE_POLICY_SUMMARY = (
    "Server-configured shared byte limits, route and stage deadlines, process-wide "
    "admission slots, per-host crawl limits, and internal fan-out caps apply."
)
SEARCH_TOOL_DESCRIPTION = (
    "Search the live web and return source-cited evidence passages. Use for current or "
    "external information that requires verification. search_profile selects quick, "
    "research, or deep bounded search. decompose controls query expansion. "
    "include_raw_markdown adds source Markdown when exact source context is needed."
)
FETCH_TOOL_DESCRIPTION = (
    "Fetch bounded evidence from known URLs. structured_formats requests source-addressed "
    "links, tables, typed JSON-LD, or schema extraction. extraction_schema is required "
    "for json_schema. force_refresh compares a live fetch with stored content; watch "
    "evaluates one declared transition and fails closed. stale_while_revalidate permits "
    "bounded stale content for non-watch reads."
)
MAP_TOOL_DESCRIPTION = (
    "Discover a bounded, robots-aware URL map for one site. Uses sitemaps first and "
    "link traversal when enabled; returns explicit per-URL admission outcomes."
)
CRAWL_TOOL_DESCRIPTION = (
    "Crawl a small, bounded part of one site and return typed page evidence. Uses the "
    "same robots, scope, sitemap, safety, deadline, and politeness policy as web_map. "
    "structured_formats adds source-addressed profiles; extraction_schema enables bounded "
    "JSON Schema output when max_pages is four or less."
)
