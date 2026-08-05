# Thorondor

> **Public alpha:** Thorondor is ready for local self-hosted evaluation, but it is not a managed production service. It does not include built-in endpoint authentication or rate limiting; expose it only behind your own proxy/security layer.

A self-hosted, data-sovereign semantic web-search service for agents — discovers URLs through SearXNG, crawls pages via Crawl4AI, chunks content with a first-party semantic chunker, reranks passages against the original query, and returns cited evidence through REST and MCP with no mandatory hosted vendor.

## What It Is

Thorondor is a local semantic web-search stack designed to replace hosted search-for-agents APIs (Exa, Tavily, and equivalents) with an on-premises, operator-controlled pipeline. It uses SearXNG for multi-engine URL discovery, Crawl4AI for JavaScript-capable page crawling, a first-party `ClusterSemanticChunker` backed by BGE-M3 embeddings for globally-optimal chunk boundaries, and BGE-reranker-v2-m3 to score passages against the original query before assembly. The orchestrator exposes both `POST /v1/search` (REST) and an MCP `web_search` tool; both surfaces return the same versioned `SearchResponse` envelope with `passages`, `citations`, and `stats`. No persistent corpus, vector store, or implemented cache exists — all content is fetched live, processed, returned, and discarded.

## Architecture Overview

```
query
  -> optional LLM query planner (subquery expansion)
  -> SearXNG multi-engine URL discovery
  -> URL safety pre-filter (SSRF/IP blocklist)
  -> domain allow/block policy + selection scoring
  -> Crawl4AI page extraction (concurrent, per-host bounded)
  -> trafilatura HTML-to-Markdown cleaning + content dedup
  -> semantic chunking service (ClusterSemanticChunker + BGE-M3 embeddings)
  -> lexical candidate prefilter (keeps top-50 before reranking)
  -> batched reranking (BGE-reranker-v2-m3)
  -> relevance floor filter
  -> token-budget assembly -> passages + citations
  -> SearchResponse (REST or MCP)
```

All inter-service traffic travels over an internal Compose network. Crawl4AI's outbound HTTP exits through the embedded SSRF egress proxy.

## Repository Layout

| Path | Type | Description |
|---|---|---|
| `orchestrator/` | service | FastAPI app: `/v1/search`, `/search` (compat), `/livez`, `/healthz`, MCP `/mcp` |
| `semantic-chunking-service/` | service | FastAPI chunker: `/chunk`, `/healthz` — ClusterSemanticChunker + OpenAI-compatible embedding client |
| `thorondor_cli/` | tool | Textual configurator, env/deploy helpers, native `thorondor-mcp` proxy, and harness writers |
| `ssrf-proxy/` | service | Minimal async HTTP CONNECT proxy that blocks RFC-1918 and embedded-IPv4 IPv6 targets |
| `searxng/` | config | SearXNG `settings.yml` mounted read-only by Compose |
| `models/` | artifact | Local GGUF model files consumed by the llama.cpp profile (not committed) |
| `scripts/` | scripts | `install.*`, `deploy.ps1`, `deploy-llamacpp.ps1`, `smoke.ps1`, `check-release-guard.ps1` (PowerShell) + Bash equivalents |
| `docker-compose.yml` | config | Core stack: orchestrator, chunker, SearXNG, Crawl4AI, egress-proxy; `bundled-models` profile adds TEI embedding + reranker |
| `docker-compose.llamacpp.yml` | config | Override: swaps embedding and reranker to local llama.cpp containers using GGUF files under `models/` |
| `docker-compose.production.yml` | config | Image-only production slice for Tengwar-style deployments; no `build:` blocks or model containers |
| `.env.example` | config | Template for `.env` — every key the Python settings loader requires |
| `.env.llamacpp.example` | config | Template for `.env.llamacpp` — llama.cpp image SHA, GGUF paths, and batch sizes |
| `.env.production.example` | config | Template overlay for released GHCR image refs and production service names |
| `docs/architecture/` | docs | Operational architecture reference (overview, pipeline, deployment, deps, security, config) |
| `docs/plans/` | docs | Active remediation and implementation plans |
| `.agents/skills/thorondor-web-search/` | skill | Repo-local agent skill for calling the running service |
| `LICENSE` | license | MIT |
| `THIRD-PARTY-NOTICES.md` | license | Third-party runtime image and package notices |

## Prerequisites

- **Docker Desktop** — installed, running, and configured for Linux containers.
- **GGUF model files** (llama.cpp profile only) — `bge-m3.gguf` and `bge-reranker-v2-m3.gguf` placed under `models/`. Verify model license terms before downloading weights.
- **Hardware** — tested on Windows 10/11 with Docker Desktop (WSL2 backend) and on ARM64 (NVIDIA DGX Spark). The llama.cpp image is pinned to a SHA known to work on both architectures and supports CPU inference. The bundled TEI image requires an AMD64 NVIDIA CUDA host.
- **PowerShell 7+** for the deploy and smoke scripts. Bash equivalents exist under `scripts/`.

## Quickstart — Textual Configurator

The interactive path is a one-line host install. It creates the local
`thorondor` and `thorondor-mcp` commands; the first `thorondor` run initializes
the managed deployment directory under `~/.thorondor`.

```bash
curl -LsSf https://raw.githubusercontent.com/FeanorsCodeSL/thorondor/main/scripts/install.sh | sh
thorondor
```

PowerShell:

```powershell
irm https://raw.githubusercontent.com/FeanorsCodeSL/thorondor/main/scripts/install.ps1 | iex
thorondor
```

The `thorondor` dashboard writes complete `.env` files, can probe endpoints,
generates `SEARXNG_SECRET` and `CRAWL4AI_API_KEY` when blank, and drives the existing Compose stack.
The dashboard uses the same keyboard model as the Imladris TUI: `↑/↓` to move
through the action menu, `Enter` to open a screen, `←/→` to focus the action
menu or the components table, and `Esc` to back out of a form. The left rail
lists the operator actions; the main panel shows the live component table
(searxng / crawl4ai / chunker / embedding / reranker / llm planner) plus
status and harness status:

| Action | Purpose |
|---|---|
| `Mode` | Choose BYO endpoints, bundled TEI containers, or llama.cpp GGUF containers. Picking `llamacpp` auto-downloads missing GGUF files into `models/` with a progress bar. |
| `Endpoints` | Edit embedding, reranker, and optional LLM planner endpoints. |
| `Search/crawl` | Tune ports, budgets, crawl limits, robots, and domain filters. |
| `Validate` | Check env completeness and show the exact Compose command. |
| `Deploy` | Run `config`, `build`, `up -d`, `/healthz`, and smoke search. |
| `Wire MCP` | Wire Claude Code, Codex, or OpenCode to `thorondor-mcp` or Docker HTTP `/mcp`. |
| `Refresh state` | Re-read `.env` and the harness detection without restarting. |
| `Quit` | Exit the dashboard. |

The llama.cpp mode also exposes a `Download models` button (and a `thorondor
download-models` subcommand for non-interactive use) that fetches the two Q8
GGUF files from immutable Hugging Face revisions into `models/` and verifies
their SHA-256 checksums without changing the active mode. Both are wired into `scripts/deploy-llamacpp.ps1`
and `scripts/deploy-llamacpp.sh` so a fresh clone can deploy the llamacpp
profile with no manual file placement.

`thorondor doctor` is the non-interactive status path. `thorondor-mcp` is a
native stdio MCP proxy that forwards `web_search` to the running
`POST /v1/search` endpoint. The Dockerized HTTP MCP surface remains available at
`http://localhost:8080/mcp`.

`thorondor uninstall` stops the managed stack, removes `~/.thorondor`, and then
removes the installed `thorondor` tool. Use `thorondor uninstall --keep-tool`
when you only want to reset the local deployment files.

When a BYO embedding endpoint points outside the Compose internal network, the
configurator generates `docker-compose.host-endpoints.yml` and includes it
during deploy. That overlay gives only the `chunker` egress for the external
embedding call; host-rewritten `localhost` endpoints also get
`host.docker.internal:host-gateway` entries for Linux Docker Engine.

## Quickstart — Windows with llama.cpp (fully local)

```powershell
# 1. Clone and enter the repo
git clone <repo-url> thorondor
Set-Location thorondor

# 2. Create environment files from examples
Copy-Item .env.example .env
Copy-Item .env.llamacpp.example .env.llamacpp

# 3. Create the models directory and place GGUF files
New-Item -ItemType Directory -Force models | Out-Null
# Copy bge-m3.gguf and bge-reranker-v2-m3.gguf into models\

# 4. Deploy: copies env files, generates internal service secrets, validates Compose,
#    builds first-party images, starts all services, runs a smoke search
.\scripts\deploy-llamacpp.ps1
```

The script exits non-zero on any failure. After a successful deploy:

```powershell
# Health check
Invoke-RestMethod http://localhost:8080/healthz

# Quick search
$body = @{ query = "latest Python packaging tools 2025"; token_budget = 3000 } | ConvertTo-Json
Invoke-RestMethod http://localhost:8080/v1/search -Method Post -ContentType "application/json" -Body $body
```

To run the investigation-mode smoke test (broader queries + URL diagnostics):

```powershell
.\scripts\smoke.ps1 `
  -ComposeFiles @("docker-compose.yml","docker-compose.llamacpp.yml") `
  -EnvFiles @(".env",".env.llamacpp") `
  -Profile llamacpp-models `
  -Investigation
```

## Quickstart — BYO Embedding and Reranker Endpoints

If you already operate OpenAI-compatible embedding and reranker servers (vLLM, TEI, Infinity, etc.), skip the llama.cpp profile entirely and point the core stack at your endpoints:

```powershell
# 1. Copy base env only
Copy-Item .env.example .env

# 2. Edit .env — set your external endpoints
# EMBEDDING_ENDPOINT=http://your-embedding-host:8082
# EMBEDDING_MODEL=BAAI/bge-m3
# RERANKER_ENDPOINT=http://your-reranker-host:8081
# RERANKER_MODEL=BAAI/bge-reranker-v2-m3
# RERANKER_PATH=/rerank
# RERANKER_HEALTH_PATH=/health

# 3. Deploy without the llama.cpp override
.\scripts\deploy.ps1 -Profile ""
```

The `bundled-models` profile (TEI containers) is still available if you want to run embedding and reranker inside Docker without GGUF files:

This profile requires an AMD64 host with NVIDIA Container Toolkit GPU access.

```powershell
.\scripts\deploy.ps1 -Profile bundled-models
```

## Production Images for Tengwar

Thorondor production deployments should consume released images rather than building from a source checkout on the headless host. The release workflow publishes:

- `ghcr.io/feanorscodesl/thorondor-orchestrator`
- `ghcr.io/feanorscodesl/thorondor-chunker`
- `ghcr.io/feanorscodesl/thorondor-egress-proxy`

Create a Git tag such as `v0.1.0` or run the `publish-images` workflow manually. The workflow pushes multi-arch `linux/amd64` and `linux/arm64` images, uploads digest artifacts, and attaches `THORONDOR_IMAGE_DIGESTS.md` to tag releases.

For Tengwar, copy `.env.production.example` to `.env.production`, replace the three first-party image refs with the release digest refs, and run the image-only Compose file:

```powershell
docker compose `
  --env-file .env --env-file .env.production `
  -f docker-compose.production.yml `
  config
```

`docker-compose.production.yml` expects Tengwar's external `app-network` and externally managed `embedding` and `reranker` services. It does not start extra model containers.

## REST API

### POST /v1/search

```http
POST http://localhost:8080/v1/search
Content-Type: application/json

{
  "query": "what changed in the EU AI Act timeline in 2025",
  "search_profile": "research",
  "token_budget": 4000,
  "max_urls": 8,
  "max_passages": 10,
  "freshness": "year",
  "domains": ["eur-lex.europa.eu", "europarl.europa.eu"],
  "exclude_domains": ["example.com"],
  "decompose": true,
  "include_raw_markdown": false
}
```

PowerShell example:

```powershell
$body = @{
  query         = "what changed in the EU AI Act timeline in 2025"
  search_profile = "research"
  token_budget  = 4000
} | ConvertTo-Json

Invoke-RestMethod http://localhost:8080/v1/search `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

curl example:

```bash
curl -s -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"EU AI Act 2025 timeline","search_profile":"research","token_budget":4000}' \
  | python -m json.tool
```

### Request fields

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string (required) | — | Information need, max 500 chars. Reranking always scores against this string, not sub-queries. |
| `search_profile` | `"quick"` \| `"research"` \| `"deep"` | `null` | Selects a preset bundle of `token_budget`, `max_urls`, `max_passages` defaults. |
| `token_budget` | int 1–16000 | profile or server default | Controls passage assembly, not crawl cost. |
| `max_urls` | int 1–20 | profile or server default | Cap on URLs selected before crawling. |
| `max_passages` | int 1–50 | profile default | Hard cap on returned passages after token budgeting. |
| `decompose` | bool | `true` | Whether the optional LLM planner may expand the query into sub-queries. No-op when `LLM_ENDPOINT` is blank. |
| `freshness` | `"day"` \| `"week"` \| `"month"` \| `"year"` | `null` | Discovery time-range hint passed to SearXNG. |
| `domains` | `list[str]` | `null` | Per-call domain allowlist. Combined with `ALLOWLIST_ONLY` if set. |
| `exclude_domains` | `list[str]` | `null` | Per-call domain blocklist merged with `DOMAIN_BLOCKLIST`. |
| `include_raw_markdown` | bool | `false` | Attach raw crawled markdown for cited pages in `raw_markdown[]`. |

### Response envelope (`SearchResponse`)

```json
{
  "query": "...",
  "schema_version": "thorondor.search.v1",
  "passages": [
    {
      "text": "...",
      "score": 0.87,
      "token_count": 134,
      "citation_id": 1,
      "provenance": "external_web",
      "trust": "untrusted"
    }
  ],
  "citations": [
    { "id": 1, "url": "https://...", "title": "..." }
  ],
  "stats": {
    "sub_queries": ["..."],
    "discovery_status": "degraded",
    "unresponsive_engines": [
      { "engine": "mojeek", "reason": "access denied" }
    ],
    "urls_discovered": 18,
    "urls_selected": 6,
    "urls_crawled_ok": 5,
    "urls_crawled_failed": 1,
    "chunks_produced": 43,
    "chunks_sent_to_reranker": 43,
    "reranked": true,
    "tokens_returned": 3812,
    "elapsed_ms": 4200,
    "reason": null
  },
  "raw_markdown": null
}
```

Key `stats` fields:

| Field | Meaning |
|---|---|
| `discovery_status` | `ok` when SearXNG reports no engine failures, `degraded` when results remain usable despite failed engines, or `unavailable` when engine failures leave no usable discovery results. |
| `unresponsive_engines` | SearXNG engine names and reported failure or suspension reasons. |
| `reranked` | `false` when the reranker was unreachable; passages are still returned sorted by position. |
| `reason` | Non-null closed enum when the search ended before normal assembly: `search_provider_unavailable`, `no_results_from_discovery`, `no_urls_after_selection`, `all_crawls_failed`, `no_chunks_after_dedup`, `no_chunks_after_rerank`. |
| `embedding_degraded` | `true` when the chunker fell back to token-based splitting because embeddings failed. |
| `url_diagnostics` | Per-URL selection decisions (populated when `include_raw_markdown` is true or the investigation smoke test is used). |

An empty-passage response with `stats.reason` set is a normal 200, not an error. The caller should read `reason` rather than interpreting `passages.length == 0` alone. `search_provider_unavailable` means SearXNG reported at least one failed engine and returned no usable discovery results; a healthy empty search remains `no_results_from_discovery`.

`POST /search` is a backwards-compatible alias for `POST /v1/search`.

## MCP

Mount the MCP endpoint in your agent's configuration:

```
http://localhost:8080/mcp
```

Transport: streamable HTTP (`stateless_http=True`). The server ID is `thorondor`.

The single exposed tool is `web_search`. Its parameters mirror the REST request exactly. Returns the same `SearchResponse` dict shape as the REST endpoint, serialised by `model_dump()`.

Example MCP tool call (Claude SDK style):

```json
{
  "name": "web_search",
  "input": {
    "query": "EU AI Act enforcement 2025",
    "search_profile": "research",
    "token_budget": 4000
  }
}
```

The MCP server also supports stdio transport via `python -m orchestrator.mcp_server` for environments that require it.

## Health and Observability

### /livez

`GET /livez` is the process-only liveness endpoint used by the orchestrator
container health check. It returns `{"status":"ok"}` without probing SearXNG or
any other dependency.

### /healthz

```json
{
  "status": "ok",
  "dependencies": {
    "searxng": true,
    "crawl4ai": true,
    "chunker": true,
    "embedding": true,
    "reranker": true
  },
  "hard_failures": [],
  "degraded_dependencies": []
}
```

`GET /healthz` is the dependency-readiness endpoint. `status` is `"degraded"`
if any dependency probe fails. The SearXNG probe calls its local `/healthz`
endpoint and never performs a public search. `hard_failures` lists `searxng` or
`chunker` — either alone causes `SearchDependencyUnavailable` (503).
`degraded_dependencies` lists `crawl4ai`, `embedding`, and `reranker`; the
pipeline tolerates their absence with reduced quality.

### X-Request-ID

Every REST and MCP request generates or accepts `X-Request-ID`. The same ID is returned to the caller and forwarded to all downstream seams (SearXNG, Crawl4AI, chunker, embedding, reranker, LLM planner).

### Structured logging

One JSON log line is emitted per completed search:

```json
{
  "level": "INFO",
  "logger": "orchestrator.pipeline",
  "message": "search_completed",
  "request_id": "a3f7...",
  "event": "search_completed",
  "query_hash": "4a8b1c2d",
  "sub_query_count": 2,
  "urls_discovered": 18,
  "urls_selected": 6,
  "urls_crawled_ok": 5,
  "urls_crawled_failed": 1,
  "reranked": true,
  "reason": null,
  "elapsed_ms": 4200
}
```

Logs never contain raw query text, page markdown, secret values, userinfo components, or URL query strings.

## Configuration Reference

All keys must be present in `.env` (leave optional keys blank rather than deleting them). The Python settings loader raises `RuntimeError` on startup for any missing key.

### Service URLs

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | Internal SearXNG base URL used by the orchestrator. |
| `CRAWL4AI_URL` | `http://crawl4ai:11235` | Internal Crawl4AI API base URL. |
| `CHUNKER_URL` | `http://chunker:8000` | Internal semantic chunking service base URL. |
| `SEARXNG_BASE_URL` | `http://searxng:8080/` | External base URL passed to SearXNG for self-referencing links (Compose env). |
| `ORCHESTRATOR_HOST` | `127.0.0.1` | Host interface bound by Compose. Keep localhost for personal use; set `0.0.0.0` only behind firewall/auth. |
| `ORCHESTRATOR_PORT` | `8080` | Host port bound to the orchestrator container (Compose env). |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Orchestrator log level. |
| `CHUNKER_LOG_LEVEL` | `INFO` | Chunker service log level. |
| `PROXY_LOG_LEVEL` | `INFO` | SSRF egress proxy log level. |

### Healthcheck

| Variable | Default | Description |
|---|---|---|
| `HEALTHCHECK_TIMEOUT_S` | `2.0` | Per-dependency probe timeout in seconds. |
| `HEALTHCHECK_MAX_CONNECTIONS` | `8` | Max connections in the healthcheck HTTP client pool. |
| `HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS` | `4` | Max keepalive connections in the healthcheck client pool. |

### Search Profiles

| Variable | Default | Description |
|---|---|---|
| `MAX_SUBQUERIES` | `3` | Maximum sub-queries the planner may produce. |
| `DEFAULT_TOKEN_BUDGET` | `4000` | Token budget used when no profile and no explicit `token_budget` is given. |
| `SEARCH_PROFILE_QUICK_TOKEN_BUDGET` | `2000` | Token budget for the `quick` profile. |
| `SEARCH_PROFILE_QUICK_MAX_URLS` | `5` | Max URLs for `quick`. |
| `SEARCH_PROFILE_QUICK_MAX_PASSAGES` | `5` | Max passages for `quick`. |
| `SEARCH_PROFILE_RESEARCH_TOKEN_BUDGET` | `8000` | Token budget for `research`. |
| `SEARCH_PROFILE_RESEARCH_MAX_URLS` | `12` | Max URLs for `research`. |
| `SEARCH_PROFILE_RESEARCH_MAX_PASSAGES` | `20` | Max passages for `research`. |
| `SEARCH_PROFILE_DEEP_TOKEN_BUDGET` | `16000` | Token budget for `deep`. |
| `SEARCH_PROFILE_DEEP_MAX_URLS` | `20` | Max URLs for `deep`. |
| `SEARCH_PROFILE_DEEP_MAX_PASSAGES` | `40` | Max passages for `deep`. |

### Crawl Tuning

| Variable | Default | Description |
|---|---|---|
| `MAX_URLS` | `6` | Default max URLs when no profile and no explicit `max_urls` is given. |
| `CRAWL_CONCURRENCY` | `4` | Total concurrent Crawl4AI requests (1–20). |
| `CRAWL_PER_HOST_CONCURRENCY` | `1` | Concurrent requests per hostname. |
| `CRAWL_TIMEOUT_S` | `15` | Per-URL crawl timeout in seconds. |
| `CRAWL_RESPECT_ROBOTS_TXT` | `true` | Whether Crawl4AI observes robots.txt. |
| `CRAWL_VALIDATE_REDIRECTS` | `true` | Preflight-check redirect chains for SSRF targets before sending to Crawl4AI. |
| `CRAWL_MAX_PREFLIGHT_REDIRECTS` | `5` | Max redirect hops to follow during preflight validation. |

### Markdown Extraction

| Variable | Default | Description |
|---|---|---|
| `MARKDOWN_EXTRACTOR` | `trafilatura` | Must be `trafilatura` (only supported value). |
| `MARKDOWN_EXTRACTOR_FAVOR_RECALL` | `true` | Trafilatura include_tables-style recall mode. |
| `MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS` | `false` | Include comment sections in extracted text. |
| `MARKDOWN_EXTRACTOR_INCLUDE_TABLES` | `true` | Include HTML table content. |
| `MARKDOWN_EXTRACTOR_DEDUPLICATE` | `true` | Enable trafilatura's internal deduplication. |

### Chunker Tuning

| Variable | Default | Description |
|---|---|---|
| `CHUNKER_DEFAULT_STRATEGY_VERSION` | `cluster-semantic@1` | Default chunking strategy version sent to the chunker service. |
| `CHUNKER_MAX_SEGMENTS_DP` | `10000` | Segment count above which the chunker falls back from DP to greedy-semantic (OOM guard). |
| `REWARD_CACHE_MAX_SIZE` | `100000` | LRU cache bound for the DP reward function. |
| `CHUNKER_MEM_LIMIT` | `768m` | Docker memory limit for the chunker container (Compose env). |

### Embedding (used by the chunker service)

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_ENDPOINT` | `http://embedding:80` | OpenAI-compatible `/v1/embeddings` server base URL. |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Model name sent in embedding requests. |
| `EMBEDDING_MODEL_REVISION` | `5617a9f61b028005a4858fdac845db406aefb181` | Immutable Hub revision used by the bundled TEI embedding container. |
| `EMBEDDING_API_KEY` | _(blank)_ | Optional bearer token for the embedding server. |
| `EMBEDDING_BATCH_SIZE` | `64` | Texts per embedding API call. |
| `EMBEDDING_TIMEOUT_S` | `60` | Embedding request timeout in seconds. |

### Reranker

| Variable | Default | Description |
|---|---|---|
| `RERANKER_ENDPOINT` | `http://reranker:80` | Reranker server base URL. |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Model name sent in rerank requests. |
| `RERANKER_MODEL_REVISION` | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | Immutable Hub revision used by the bundled TEI reranker container. |
| `RERANKER_PATH` | `/rerank` | Rerank endpoint path. |
| `RERANKER_HEALTH_PATH` | `/health` | Reranker health endpoint path. |
| `RERANKER_API_KEY` | _(blank)_ | Optional bearer token for the reranker. |
| `RERANKER_BATCH_SIZE` | `32` | Chunks per reranker API call. |
| `RERANKER_TIMEOUT_S` | `30` | Reranker request timeout in seconds. |
| `RELEVANCE_SCORE_FLOOR` | `0.0` | Passages with reranker score ≤ this value are dropped after reranking (0.0 disables the filter). |

### Optional LLM Planner

| Variable | Default | Description |
|---|---|---|
| `LLM_ENDPOINT` | _(blank)_ | Optional OpenAI-compatible chat completions server for query decomposition. Leave blank to use `IdentityPlanner` (no decomposition). |
| `LLM_MODEL` | _(blank)_ | Model name sent to the LLM planner. Required when `LLM_ENDPOINT` is set. |
| `LLM_API_KEY` | _(blank)_ | Optional bearer token for the LLM planner. |

### URL Safety and SSRF Protection

| Variable | Default | Description |
|---|---|---|
| `URL_SAFETY_BLOCKED_IP_CATEGORIES` | `loopback,link_local,private,reserved,multicast,unspecified` | IP address categories blocked before crawl. |
| `URL_SAFETY_BLOCKED_SPECIAL_IPS` | `169.254.169.254,fd00:ec2::254` | Specific IPs blocked regardless of category (AWS/cloud metadata endpoints). |
| `URL_SAFETY_NAT64_NETWORKS` | `64:ff9b::/96` | IPv6 NAT64 networks; embedded IPv4 is extracted and re-checked. |
| `URL_SAFETY_SIX_TO_FOUR_NETWORKS` | `2002::/16` | IPv6 6-to-4 networks; embedded IPv4 is extracted and re-checked. |
| `URL_SAFETY_IPV4_COMPAT_NETWORKS` | `::/96` | IPv4-compatible IPv6 networks; embedded IPv4 is extracted and re-checked. |

### MCP Transport Security

| Variable | Default | Description |
|---|---|---|
| `MCP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*,[::1]:*,thorondor:*,orchestrator:*` | Host headers accepted by the MCP streamable HTTP endpoint. |
| `MCP_ALLOWED_ORIGINS` | `http://127.0.0.1:*,http://localhost:*,http://[::1]:*,http://thorondor:*,http://orchestrator:*` | Origin headers accepted by the MCP streamable HTTP endpoint when an Origin header is present. |

### Domain Allow/Block Lists

| Variable | Default | Description |
|---|---|---|
| `DOMAIN_BLOCKLIST` | _(blank)_ | Comma-separated domains excluded from all searches. Supports subdomain matching. |
| `DOMAIN_ALLOWLIST` | _(blank)_ | Comma-separated domains permitted when `ALLOWLIST_ONLY=true`. |
| `ALLOWLIST_ONLY` | `false` | When `true`, restricts all crawls to `DOMAIN_ALLOWLIST` + per-call `domains`. |

### SSRF Egress Proxy

| Variable | Default | Description |
|---|---|---|
| `PROXY_HOST` | `0.0.0.0` | Proxy listen address. |
| `PROXY_PORT` | `8888` | Proxy listen port. |
| `PROXY_MAX_HEADER_BYTES` | `65536` | Max bytes read for HTTP request headers. |
| `PROXY_READ_CHUNK_BYTES` | `4096` | Read chunk size for incoming data. |
| `PROXY_RELAY_CHUNK_BYTES` | `65536` | Relay chunk size for proxied data. |
| `PROXY_BLOCKED_IP_CATEGORIES` | _(same as URL_SAFETY)_ | IP categories blocked by the proxy itself. |
| `PROXY_BLOCKED_SPECIAL_IPS` | _(same as URL_SAFETY)_ | Special IPs blocked by the proxy. |
| `PROXY_NAT64_NETWORKS` | `64:ff9b::/96` | NAT64 networks for the proxy's IP expansion. |
| `PROXY_SIX_TO_FOUR_NETWORKS` | `2002::/16` | 6-to-4 networks for the proxy's IP expansion. |
| `PROXY_IPV4_COMPAT_NETWORKS` | `::/96` | IPv4-compat networks for the proxy's IP expansion. |

### Crawl4AI Proxy Pass-Through

| Variable | Default | Description |
|---|---|---|
| `CRAWL4AI_HTTP_PROXY` | `http://egress-proxy:8888` | HTTP proxy for Crawl4AI outbound connections. |
| `CRAWL4AI_HTTPS_PROXY` | `http://egress-proxy:8888` | HTTPS proxy for Crawl4AI outbound connections. |
| `CRAWL4AI_ALL_PROXY` | `http://egress-proxy:8888` | All-protocol proxy fallback. |
| `CRAWL4AI_NO_PROXY` | _(blank)_ | Comma-separated hosts to bypass the proxy. |

### API Keys

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_SECRET` | _(blank — generated by deploy script)_ | SearXNG instance secret. Generated by `deploy.ps1`; never commit a real value. |
| `SEARXNG_API_KEY` | _(blank)_ | Optional bearer token accepted by SearXNG for its JSON API. |
| `CRAWL4AI_API_KEY` | _(blank — generated by deploy script)_ | Bearer token shared by the orchestrator and managed Crawl4AI container. BYO endpoints may leave it blank only when unauthenticated. |
| `CHUNKER_API_KEY` | _(blank)_ | Optional bearer token for the chunking service. |
| `RERANKER_API_KEY` | _(blank)_ | Optional bearer token for the reranker. |
| `EMBEDDING_API_KEY` | _(blank)_ | Optional bearer token for the embedding server (read by chunker). |
| `LLM_API_KEY` | _(blank)_ | Optional bearer token for the LLM planner. |

### llama.cpp Overrides (`.env.llamacpp`)

| Variable | Default | Description |
|---|---|---|
| `LLAMACPP_IMAGE` | `ghcr.io/ggml-org/llama.cpp:server@sha256:bde659bf...` | Pinned llama.cpp build `b10276` server image SHA. |
| `LLAMACPP_EMBEDDING_MODEL` | `/models/bge-m3.gguf` | Container path to the embedding GGUF (mounted from `./models`). |
| `LLAMACPP_EMBEDDING_ALIAS` | `bge-m3` | Model alias used in embedding API requests. |
| `LLAMACPP_EMBEDDING_POOLING` | `cls` | Pooling strategy for the embedding model. |
| `LLAMACPP_EMBEDDING_CONTEXT` | `8192` | Context length for the embedding server. |
| `LLAMACPP_RERANKER_MODEL` | `/models/bge-reranker-v2-m3.gguf` | Container path to the reranker GGUF. |
| `LLAMACPP_RERANKER_ALIAS` | `bge-reranker-v2-m3` | Model alias used in reranker API requests. |
| `LLAMACPP_RERANKER_CONTEXT` | `8192` | Context length for the reranker server. |
| `LLAMACPP_BATCH` | `8192` | Batch size for llama.cpp. |
| `LLAMACPP_UBATCH` | `8192` | Micro-batch size for llama.cpp. |

## Development Checks

```powershell
# Create or activate a Python environment, then install test dependencies
python -m pip install `
  -r orchestrator\requirements-dev.lock `
  -r semantic-chunking-service\requirements-dev.lock

# Run all tests
python -m pytest semantic-chunking-service\tests orchestrator\tests -v

# Run local SonarQube analysis
# Requires SONAR_HOST_URL and SONAR_TOKEN in the environment.
.\scripts\sonar.ps1

# Validate base Compose config
docker compose --env-file .env -f docker-compose.yml config

# Validate llama.cpp overlay config
docker compose --env-file .env --env-file .env.llamacpp `
  -f docker-compose.yml -f docker-compose.llamacpp.yml `
  --profile llamacpp-models config

# Check release guard (CI parity check)
.\scripts\check-release-guard.ps1
```

## Troubleshooting

**Docker pipe error (`dockerDesktopLinuxEngine` not found)**
Start Docker Desktop and wait until its system tray icon reports "running". Then re-run the deploy script.

**Missing GGUF files**
`deploy-llamacpp.ps1` validates model paths before starting Compose. Either place the files under `models\` matching the paths in `.env.llamacpp`, or edit `.env.llamacpp` to point to your actual filenames.

`thorondor download-models` verifies the pinned SHA-256 for both default GGUF files. On a mismatch it preserves the existing file and exits non-zero; remove or replace that file only after confirming its provenance.

**`embedding=false` in /healthz**
The chunker is running but cannot reach the embedding server. Check `EMBEDDING_ENDPOINT` in `.env` and verify the embedding container is healthy: `docker compose logs embedding`.

**`reranker=false` in /healthz**
The reranker server is unreachable. For llama.cpp, verify the GGUF file loads without error: `docker compose --env-file .env --env-file .env.llamacpp -f docker-compose.yml -f docker-compose.llamacpp.yml logs reranker`. For remote endpoints, check `RERANKER_ENDPOINT` and `RERANKER_HEALTH_PATH`.

**`stats.reranked=false` in search response**
The reranker was unreachable at query time. Passages are returned sorted by position (no semantic ranking). The service continues to function; fix the reranker and passages will be reranked again.

**Port conflict on 8080**
Change `ORCHESTRATOR_PORT` in `.env` to an available port, then restart: `docker compose up -d orchestrator`.

**Expose beyond localhost**
Compose binds the orchestrator to `127.0.0.1` by default. To expose it to another host, set `ORCHESTRATOR_HOST=0.0.0.0` and put the service behind TLS, authentication, and rate limiting first. Thorondor does not implement endpoint authentication itself.

**`SEARXNG_SECRET not set` error**
Run `.\scripts\deploy.ps1` (or `deploy-llamacpp.ps1`) rather than `docker compose up` directly — the deploy script generates the secret when the field is blank.

**Crawl4AI returns 401 or is unreachable**
Run the deploy script so `CRAWL4AI_API_KEY` is generated and passed to both services. Crawl4AI 0.9.2 binds only to loopback and creates an ephemeral unknown token when the configured token is blank.

**All crawls failing (`urls_crawled_ok=0`)**
Check Crawl4AI logs: `docker compose logs crawl4ai`. Common causes: slow network, robots.txt refusals (`CRAWL_RESPECT_ROBOTS_TXT=true`), or proxy misconfiguration. Increase `CRAWL_TIMEOUT_S` for slow sites.

**No results from SearXNG**
SearXNG may be rate-limited or blocking engines may be unavailable. Check `docker compose logs searxng`. The SearXNG web UI is not published by the default Compose stack; inspect it from inside the Compose network or add a temporary local-only port mapping during debugging.

## License

MIT — see [LICENSE](LICENSE). Third-party notices: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
