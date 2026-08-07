from typing import Literal


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
DEFAULT_FETCH_CAPABILITIES = ("markdown", "javascript", "links", "metadata")
DEFAULT_MAX_REQUEST_BODY_BYTES = 32768
DEFAULT_MAX_RESPONSE_BODY_BYTES = 2097152
DEFAULT_SEARCH_ROUTE_DEADLINE_S = 120.0
DEFAULT_FETCH_ROUTE_DEADLINE_S = 60.0
DEFAULT_DISCOVERY_STAGE_DEADLINE_S = 20.0
DEFAULT_CRAWL_STAGE_DEADLINE_S = 45.0
DEFAULT_CHUNK_STAGE_DEADLINE_S = 45.0
DEFAULT_RERANK_STAGE_DEADLINE_S = 30.0
DEFAULT_MAX_INFLIGHT_SEARCHES = 4
DEFAULT_MAX_INFLIGHT_FETCHES = 8
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
    "Fetch bounded evidence from known URLs with per-URL terminal outcomes. "
    f"{RESOURCE_POLICY_SUMMARY}"
)
