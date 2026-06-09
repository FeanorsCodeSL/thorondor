# 04 — API & Agent Integration

> **Thorondor** — Semantic Web Search Pipeline. MCP mounting/transport snippets are
> representative — verify against the MCP Python SDK version you pin.

The orchestrator exposes the same pipeline two ways:

- a **REST** endpoint (`POST /v1/search`, with `/search` kept as a compatibility alias) for any HTTP caller, and
- an **MCP server** exposing a `web_search` tool for MCP-capable agents.

Both call the identical pipeline function; the surfaces are thin.

---

## 1. REST: `POST /v1/search`

`POST /search` is kept as a compatibility alias during the v1 window. New
callers should use `/v1/search`.

### Request

```jsonc
{
  "query": "what changed in the EU AI Act timeline during 2025",  // required

  "token_budget": 4000,        // max total tokens of returned passages (default: DEFAULT_TOKEN_BUDGET)
  "max_urls": 6,               // crawl cap, overrides MAX_URLS (selection gate, stage 4)
  "max_passages": null,        // optional hard cap on passage count (budget still applies)

  "decompose": true,           // enable query planner IF an LLM endpoint is configured; else ignored
  "freshness": null,           // optional: "day" | "week" | "month" | "year"
  "domains": null,             // optional allowlist of domains
  "exclude_domains": null,     // optional blocklist (merged with DOMAIN_BLOCKLIST)

  "include_raw_markdown": false  // if true, attach per-source full markdown (large)
}
```

Only `query` is required. Bounds are enforced before discovery or crawl:
`query` is 1-500 characters, `token_budget` is 1-16000, `max_urls` is
1-20, and `max_passages` is 1-50.

### Response

```jsonc
{
  "query": "what changed in the EU AI Act timeline during 2025",
  "passages": [
    {
      "text": "On 1 August 2025 the obligations for general-purpose AI models ...",
      "score": 0.913,                // reranker score vs the ORIGINAL query
      "token_count": 312,
      "citation_id": 1,
      "provenance": "external_web",
      "trust": "untrusted"
    }
  ],
  "citations": [
    { "id": 1, "url": "https://example.org/eu-ai-act", "title": "EU AI Act timeline", "published": null }
  ],
  "stats": {
    "sub_queries": ["EU AI Act 2025 timeline", "EU AI Act GPAI obligations date"],
    "urls_discovered": 24,
    "urls_selected": 6,
    "urls_crawled_ok": 5,
    "chunks_produced": 88,
    "chunks_reranked": 88,
    "reranked": true,
    "tokens_returned": 3870,
    "elapsed_ms": 5210,
    "reason": null
  },
  "raw_markdown": null,
  "schema_version": "thorondor.search.v1"
}
```

- **`passages`** are ordered by reranker score and truncated to fit
  `token_budget`. Each carries a `citation_id` into `citations` and is labeled
  as untrusted external web content for downstream agents.
- **`citations`** is the deduplicated source list, so an agent can render
  footnotes without re-deriving provenance.
- **`stats`** makes the pipeline observable per call (how many URLs were found,
  selected, crawled, whether reranking actually ran). `reranked: false` signals
  the reranker degraded and order fell back to pre-filter/discovery (see
  [`01-architecture.md`](01-architecture.md) §7).

### Empty / degraded result

```jsonc
{
  "query": "...",
  "passages": [],
  "citations": [],
  "stats": { "urls_discovered": 0, "reason": "no_results_from_discovery" }
}
```

`reason` is one of `no_results_from_discovery`, `no_urls_after_selection`,
`all_crawls_failed`, `no_chunks_after_dedup`. The call still returns `200` with an explanatory
`reason` rather than an opaque error, so an agent can decide whether to reword
and retry.

### `GET /healthz`

Returns per-dependency reachability (SearXNG, Crawl4AI, chunker, reranker) for
liveness/readiness probes.

---

## 2. MCP: the `web_search` tool

The orchestrator runs an MCP server so any MCP-capable agent gets a native
`web_search` tool with no glue code. The tool delegates to the same pipeline
function as REST.

```python
# orchestrator/mcp_server.py
from mcp.server.fastmcp import FastMCP
from .models import SearchRequest
from .pipeline import run_search          # shared with the REST handler

mcp = FastMCP("thorondor", streamable_http_path="/", stateless_http=True)


@mcp.tool()
async def web_search(
    query: str,
    token_budget: int | None = None,
    max_urls: int | None = None,
    freshness: str | None = None,
    domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    decompose: bool | None = None,
    max_passages: int | None = None,
    include_raw_markdown: bool | None = None,
) -> dict:
    """Search the live web and return the versioned SearchResponse envelope.

    Use this when you need current information from the open web. Returns a
    small set of the most relevant passages (already reranked and trimmed to
    `token_budget`) plus a citation list. Call again with a refined query for
    follow-up questions; each call is independent.

    Args:
        query: Natural-language search query.
        token_budget: Optional maximum total tokens of passages to return.
        max_urls: Optional maximum number of pages to select before crawl.

    Returns:
        Full SearchResponse dict: query, passages, citations, stats,
        raw_markdown, and schema_version. Invalid bounds fail before fan-out.
    """
    req = SearchRequest(query=query, token_budget=token_budget, max_urls=max_urls)
    return (await run_search(req, _get_deps())).model_dump()
```

### Serving REST + MCP from one process

Mount the MCP server's streamable-HTTP app under the FastAPI app, and also allow
a stdio transport for local agents:

```python
# orchestrator/app.py  (representative — confirm against your MCP SDK version)
from fastapi import FastAPI
from .mcp_server import mcp
from .pipeline import run_search
from .models import SearchRequest, SearchResponse

app = FastAPI(title="thorondor")

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/v1/search", response_model=SearchResponse)
@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    return await run_search(req, get_deps())

# MCP over streamable HTTP at /mcp for remote agents:
app.mount("/mcp", mcp.streamable_http_app())
```

For a local agent that launches the server over **stdio**, ship a console entry
point that calls `mcp.run(transport="stdio")`.

### Client configuration (any MCP host)

Remote (HTTP) transport — point the agent's MCP client at the mounted endpoint:

```jsonc
{
  "mcpServers": {
    "web-search": {                       // rename
      "transport": "streamable-http",
      "url": "http://your-host:8080/mcp"
    }
  }
}
```

Local (stdio) transport — launch the container/command directly:

```jsonc
{
  "mcpServers": {
    "web-search": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", ".env", "your-image:tag", "web-search-mcp"]
    }
  }
}
```

This works with any MCP host (a desktop agent app, a custom agent runtime, an
IDE integration) — the project does not assume a particular client.

---

## 3. End-to-end agent example

A bare-bones agent loop that uses the tool and renders cited output. This is
deliberately framework-agnostic — substitute your own LLM/tool-calling runtime.

```python
import httpx

ORCH = "http://your-host:8080"

def web_search(query: str, token_budget: int = 4000) -> dict:
    r = httpx.post(f"{ORCH}/v1/search", json={"query": query, "token_budget": token_budget}, timeout=60)
    r.raise_for_status()
    return r.json()

# --- agent turn ---
res = web_search("what changed in the EU AI Act timeline during 2025")

# Build a grounded context block for the model, with inline citation markers.
context = "\n\n".join(
    f"[{p['citation_id']}] {p['text']}" for p in res["passages"]
)
sources = "\n".join(
    f"[{c['id']}] {c['title']} — {c['url']}" for c in res["citations"]
)

prompt = (
    "Answer using ONLY the sources below. Cite with [n].\n\n"
    f"SOURCES:\n{context}\n\nSOURCE LIST:\n{sources}\n\n"
    "QUESTION: What changed in the EU AI Act timeline during 2025?"
)
# answer = your_llm(prompt)   # the passages are pre-reranked and budget-fit
```

In an **investigative loop** the agent calls `web_search` repeatedly — one hop
per sub-question — and the orchestrator's optional cache (see
[`01-architecture.md`](01-architecture.md) §5) keeps overlapping crawls cheap
across hops. Because each call is stateless, the agent owns the loop; the
service stays a pure tool.

---

## 4. Design contract for the agent surface

- **The tool returns evidence, not prose.** It does not summarize or answer —
  it returns the strongest passages plus citations and lets the agent reason.
  This keeps the tool model-agnostic and avoids a second hidden LLM hop.
- **Citations are first-class.** Every passage maps to a source; the agent can
  always attribute claims. This is the difference between a search tool and a
  black box.
- **Budget, not count.** `token_budget` is the primary control; agents size it
  to the room left in their context window rather than guessing a `top_k`.
- **Independent calls.** No server-side session is required. Any session cache
  is an optimization, never part of the contract.
