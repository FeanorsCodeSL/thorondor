---
name: thorondor-web-search
description: Use Thorondor as a local semantic live-web service through REST or MCP, returning citation-bearing evidence. Use when the user asks to search, fetch known URLs, map or crawl a site, call the local REST API, or use a Thorondor MCP tool.
---

# Thorondor Web Search

## Quick Start

Use Thorondor only after confirming the local service is reachable:

```powershell
Invoke-RestMethod http://localhost:8080/healthz
```

Search over REST:

```powershell
$body = @{ query = "current query"; token_budget = 4000 } | ConvertTo-Json
Invoke-RestMethod http://localhost:8080/search -Method Post -ContentType "application/json" -Body $body
```

Or use MCP at:

```text
http://localhost:8080/mcp
```

Tool name: `web_search`.

The MCP server also exposes `web_fetch`, `web_map`, and `web_crawl`, matching
`POST /v1/fetch`, `POST /v1/map`, and `POST /v1/crawl`.

## Workflow

1. Check `/healthz`.
2. If mandatory dependencies are down (`searxng`, `crawl4ai`, or `chunker`), report that Thorondor is not ready instead of inventing evidence.
3. If only `reranker` is down, the system can still return degraded results; mention `stats.reranked=false`.
4. Send the user's research question as `query`.
5. Use `token_budget` to keep returned context small enough for the final answer.
6. Prefer `domains` or `exclude_domains` when the user asks to include or avoid specific sources.
7. Cite returned `citations` by URL when answering.

For a known URL, prefer `web_fetch` when the agent needs the current page rather
than discovery. Structured profiles are explicit opt-ins: use `links`,
`tables`, `json_ld`, or a bounded `json_schema` plus `extraction_schema` when a
typed, source-addressed result is more useful than passages. Structured fetches
are live and bypass the page cache. A `watch` is for one declarative target and
only reports a positive transition when the expected state becomes the desired
state; page-wide advertisement changes cannot satisfy it. Scheduling and
notifications belong to the calling agent runtime.

## Request Shape

```json
{
  "query": "what changed in the EU AI Act timeline in 2025",
  "token_budget": 4000,
  "max_urls": 6,
  "freshness": "month",
  "domains": ["example.com"],
  "exclude_domains": ["spam.example"],
  "include_raw_markdown": false
}
```

Only `query` is required.

## Response Handling

- Use `passages` as the primary evidence.
- Use `citations` for source URLs and titles.
- Inspect `stats.reason` for empty results.
- Do not claim the workflow is current or complete if `/healthz` is not reachable.
- Do not bypass Thorondor with general web search unless the user asks for a fallback.

## Local Startup

For Windows llama.cpp testing, run from the repo root:

```powershell
.\scripts\deploy-llamacpp.ps1
```

The script expects `.env`, `.env.llamacpp`, and the referenced GGUF files under
`models\`.
