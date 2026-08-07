# Configuration Reference

All environment variables must be present in `.env` (and `.env.llamacpp` or `.env.production` for the selected Compose overlay). The Python settings loader raises `RuntimeError` on startup for any missing key — including keys that are intentionally blank. Leave optional keys set to an empty string rather than deleting them.

## Variable Table

### Service URLs

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `SEARXNG_URL` | Required / `http://searxng:8080` | URL | Internal SearXNG base URL used by the orchestrator for search requests. | `SEARXNG_BASE_URL` |
| `CRAWL4AI_URL` | Required / `http://crawl4ai:11235` | URL | Internal Crawl4AI API base URL. | `CRAWL4AI_API_KEY` |
| `CHUNKER_URL` | Required / `http://chunker:8000` | URL | Internal semantic chunking service base URL. | `CHUNKER_API_KEY` |
| `SEARXNG_BASE_URL` | Required / `http://searxng:8080/` | URL | Passed to SearXNG for self-referencing link generation (Compose env, not read by Python settings loader). | `SEARXNG_URL` |
| `ORCHESTRATOR_HOST` | Required / `127.0.0.1` | host/IP | Host interface bound by Compose. Use `0.0.0.0` only behind firewall/auth/rate limiting. | `ORCHESTRATOR_PORT` |
| `ORCHESTRATOR_PORT` | Required / `8080` | int | Host port bound to the orchestrator container (Compose env only). | — |

### Logging

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `LOG_LEVEL` | Required / `INFO` | string | Orchestrator log level. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. | — |
| `CHUNKER_LOG_LEVEL` | Required / `INFO` | string | Chunking service log level. | — |
| `PROXY_LOG_LEVEL` | Required / `INFO` | string | SSRF egress proxy log level. | — |

### Healthcheck

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `HEALTHCHECK_TIMEOUT_S` | Required / `2.0` | float (> 0) | Per-dependency probe timeout in seconds. | `HEALTHCHECK_MAX_CONNECTIONS` |
| `HEALTHCHECK_MAX_CONNECTIONS` | Required / `8` | int (≥ 1) | Max connections in the healthcheck HTTP client pool. | `HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS` |
| `HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS` | Required / `4` | int (≥ 1) | Max keepalive connections in the healthcheck client pool. | `HEALTHCHECK_MAX_CONNECTIONS` |

### Resource Envelope

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MAX_REQUEST_BODY_BYTES` | Required / `32768` | int (≥ 1) | Maximum REST or in-process MCP request payload. Oversize HTTP requests return 413. | `MAX_RESPONSE_BODY_BYTES` |
| `MAX_RESPONSE_BODY_BYTES` | Required / `2097152` | int (≥ 1) | Maximum serialized response and Crawl4AI or stdio-proxy response body. | `MAX_CONTENT_BYTES` |
| `SEARCH_ROUTE_DEADLINE_S` | Required / `120` | float (> 0) | End-to-end search deadline; expiry returns a closed 504 error. | `MAX_INFLIGHT_SEARCHES` |
| `FETCH_ROUTE_DEADLINE_S` | Required / `60` | float (> 0) | End-to-end known-URL fetch deadline; expiry returns a closed 504 error. | `MAX_INFLIGHT_FETCHES` |
| `MAP_ROUTE_DEADLINE_S` | Required / `90` | float (> 0) | End-to-end site-map deadline; expiry returns a closed 504 error. | `MAX_INFLIGHT_MAPS` |
| `SITE_CRAWL_ROUTE_DEADLINE_S` | Required / `120` | float (> 0) | End-to-end bounded site-crawl deadline; expiry returns a closed 504 error. | `MAX_INFLIGHT_CRAWLS` |
| `DISCOVERY_TIMEOUT_S` | Required / `20` | float (> 0) | Query-planning and per-subquery discovery stage deadline. | `MAX_SUBQUERIES` |
| `CHUNK_TIMEOUT_S` | Required / `45` | float (> 0) | Total chunking stage deadline. | `CHUNK_CONCURRENCY` |
| `MAX_INFLIGHT_SEARCHES` | Required / `4` | int (≥ 1) | Process-wide search admission slots. | `ADMISSION_WAIT_S` |
| `MAX_INFLIGHT_FETCHES` | Required / `8` | int (≥ 1) | Process-wide known-URL fetch admission slots. | `ADMISSION_WAIT_S` |
| `MAX_INFLIGHT_MAPS` | Required / `4` | int (≥ 1) | Process-wide site-map admission slots. | `ADMISSION_WAIT_S` |
| `MAX_INFLIGHT_CRAWLS` | Required / `2` | int (≥ 1) | Process-wide bounded site-crawl admission slots. | `ADMISSION_WAIT_S` |
| `ADMISSION_WAIT_S` | Required / `0.05` | float (≥ 0) | Maximum wait for route or crawler capacity before rejection. | `ADMISSION_RETRY_AFTER_S` |
| `ADMISSION_RETRY_AFTER_S` | Required / `1` | int (≥ 1) | `Retry-After` seconds returned with capacity HTTP 429 responses. | `ADMISSION_WAIT_S` |
| `MAX_INTERNAL_FANOUT` | Required / `20` | int (≥ 1) | Shared cap that must cover URL, subquery, crawl, chunk, and profile limits. | `MAX_URLS`, `MAX_SUBQUERIES` |
| `MAX_CONTENT_BYTES` | Required / `262144` | int (≥ 1) | Maximum combined Markdown and HTML bytes retained per fetched page. Must not exceed `MAX_RESPONSE_BODY_BYTES`. | `MAX_RESPONSE_BODY_BYTES` |
| `CHUNK_CONCURRENCY` | Required / `4` | int (≥ 1) | Process-owned concurrent chunk-service requests. | `CHUNK_TIMEOUT_S` |

### Page Cache and Change Detection

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `PAGE_CACHE_ENABLED` | Required / `false` | bool | Enable persistent records only for known-URL fetch. Search, map, and crawl remain live. | `PAGE_CACHE_PATH` |
| `PAGE_CACHE_PATH` | Required / `/var/lib/thorondor/page-cache.sqlite3` | absolute container path | SQLite page-cache path inside the dedicated Compose volume. | `PAGE_CACHE_ENABLED` |
| `PAGE_CACHE_TTL_S` | Required / `300` | int (≥ 1) | Fresh lifetime from fetch time. Cache reads do not extend it. | `PAGE_CACHE_STALE_S` |
| `PAGE_CACHE_STALE_S` | Required / `900` | int (≥ 0) | Additional stale-while-revalidate window accepted only by explicit non-watch requests. | `PAGE_CACHE_TTL_S` |
| `PAGE_CACHE_RETENTION_S` | Required / `604800` | int (≥ TTL + stale) | Absolute retention from the full content fetch. Reads and `304` responses do not extend it. | `PAGE_CACHE_TTL_S` |
| `PAGE_CACHE_RAW_HTML_ENABLED` | Required / `false` | bool | Permit raw HTML persistence only when a fetch also requests `raw_html`. | `PAGE_CACHE_ENABLED` |
| `PAGE_DIFF_MAX_INPUT_LINES` | Required / `2000` | int (≥ 1) | Per-document line cap for detailed comparison. | `PAGE_DIFF_MAX_OPERATIONS` |
| `PAGE_DIFF_MAX_OPERATIONS` | Required / `1000000` | int (≥ 1) | Maximum old-line × new-line comparison work. | `PAGE_DIFF_MAX_INPUT_LINES` |
| `PAGE_DIFF_MAX_OUTPUT_LINES` | Required / `24` | int (1–24) | Maximum added and removed lines returned. | `MAX_RESPONSE_BODY_BYTES` |

### Durable Crawl Jobs

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `CRAWL_JOBS_ENABLED` | Required / `false` | bool | Enable the REST-only SQLite crawl-job worker. Run one orchestrator replica. | `CRAWL_JOB_PATH` |
| `CRAWL_JOB_PATH` | Required / `/var/lib/thorondor/crawl-jobs.sqlite3` | absolute container path | Job state and result database in the existing persistent volume. | `CRAWL_JOBS_ENABLED` |
| `CRAWL_SYNC_MAX_PAGES` | Required / `10` | int (1–20) | Advertised default boundary for synchronous crawl; callers may still choose durable mode explicitly at any size. | `MAX_SITE_PAGES` |
| `CRAWL_JOB_RETENTION_S` | Required / `86400` | int (≥ 1) | Absolute result lifetime from terminal transition. Polling never extends it. | `CRAWL_JOB_EXPIRED_TOMBSTONE_S` |
| `CRAWL_JOB_EXPIRED_TOMBSTONE_S` | Required / `3600` | int (≥ 1) | Time an expired status remains available after results and the idempotency claim are removed. | `CRAWL_JOB_RETENTION_S` |
| `CRAWL_JOB_MAX_ATTEMPTS` | Required / `3` | int (1–5) | Total attempts for retryable capacity, deadline, rate-limit, timeout, or upstream failures. | `CRAWL_JOB_RETRY_BASE_S` |
| `CRAWL_JOB_RETRY_BASE_S` | Required / `1` | float (0–60) | Exponential retry base, capped internally at 30 seconds. | `CRAWL_JOB_MAX_ATTEMPTS` |
| `CRAWL_JOB_ATTEMPT_DEADLINE_S` | Required / `600` | float (1–3600) | Wall-clock limit for each durable attempt, independent of the synchronous route deadline. | `CRAWL_JOB_MAX_ATTEMPTS` |
| `CRAWL_JOB_MAX_RECORDS` | Required / `100` | int (1–10000) | Maximum retained job records across active, terminal, and expired states. New claims return 429 at the cap. | `CRAWL_JOB_RETENTION_S` |
| `CRAWL_JOB_RAW_HTML_ENABLED` | Required / `false` | bool | Permit durable serialization only when a crawl also requests `raw_html`. | `CRAWL_JOBS_ENABLED` |
| `CRAWL_JOB_MAX_INFLIGHT_REQUESTS` | Required / `16` | int (1–128) | Concurrent create, status, result-page, and cancellation store operations before bounded 429 admission. | `ADMISSION_WAIT_S` |
| `CRAWL_JOB_RESULT_PAGE_MAX_ITEMS` | Required / `10` | int (1–20) | Maximum result items accepted per cursor-page request. | `MAX_SITE_PAGES` |
| `CRAWL_JOB_RESULT_PAGE_MAX_BYTES` | Required / `524288` | int (1–half response cap) | Maximum stored result item and requested cursor-page payload. | `MAX_RESPONSE_BODY_BYTES` |

The worker wakes immediately when this process creates a job. While idle it performs expiry maintenance at most once per minute rather than polling SQLite continuously.

### Search Profiles

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MAX_SUBQUERIES` | Required / `3` | int (1–8) | Cap on sub-queries the LLM planner may return. Also caps the list even if decompose=true. | `LLM_ENDPOINT` |
| `DEFAULT_TOKEN_BUDGET` | Required / `4000` | int (≥ 1) | Token budget when no profile and no explicit `token_budget` in the request. | `SEARCH_PROFILE_*_TOKEN_BUDGET` |
| `SEARCH_PROFILE_QUICK_TOKEN_BUDGET` | Required / `2000` | int (≥ 1) | Token budget for the `quick` search profile. | `SEARCH_PROFILE_QUICK_MAX_URLS` |
| `SEARCH_PROFILE_QUICK_MAX_URLS` | Required / `5` | int (≥ 1) | Max URLs to crawl for the `quick` profile. | `SEARCH_PROFILE_QUICK_TOKEN_BUDGET` |
| `SEARCH_PROFILE_QUICK_MAX_PASSAGES` | Required / `5` | int (≥ 1) | Max passages returned for the `quick` profile. | `SEARCH_PROFILE_QUICK_TOKEN_BUDGET` |
| `SEARCH_PROFILE_RESEARCH_TOKEN_BUDGET` | Required / `8000` | int (≥ 1) | Token budget for the `research` profile. | `SEARCH_PROFILE_RESEARCH_MAX_URLS` |
| `SEARCH_PROFILE_RESEARCH_MAX_URLS` | Required / `12` | int (≥ 1) | Max URLs for `research`. | — |
| `SEARCH_PROFILE_RESEARCH_MAX_PASSAGES` | Required / `20` | int (≥ 1) | Max passages for `research`. | — |
| `SEARCH_PROFILE_DEEP_TOKEN_BUDGET` | Required / `16000` | int (≥ 1) | Token budget for the `deep` profile. | `SEARCH_PROFILE_DEEP_MAX_URLS` |
| `SEARCH_PROFILE_DEEP_MAX_URLS` | Required / `20` | int (≥ 1) | Max URLs for `deep`. | — |
| `SEARCH_PROFILE_DEEP_MAX_PASSAGES` | Required / `40` | int (≥ 1) | Max passages for `deep`. | — |

### Crawl Tuning

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MAX_URLS` | Required / `6` | int (≥ 1) | Default URL cap when no profile and no explicit `max_urls` in the request. | `CRAWL_CONCURRENCY` |
| `CRAWL_CONCURRENCY` | Required / `4` | int (1–20) | Total concurrent Crawl4AI requests. Hard upper bound: 20. | `CRAWL_PER_HOST_CONCURRENCY` |
| `CRAWL_PER_HOST_CONCURRENCY` | Required / `1` | int (≥ 1) | Concurrent requests to the same hostname. | `CRAWL_CONCURRENCY` |
| `CRAWL_TIMEOUT_S` | Required / `15` | int (> 0) | Per-URL crawl timeout in seconds. Also the asyncio.wait timeout for the batch. | `CRAWL_CONCURRENCY` |
| `CRAWL_RESPECT_ROBOTS_TXT` | Required / `true` | bool | Pass `check_robots_txt` to Crawl4AI. | — |
| `CRAWLER_USER_AGENT` | Required / `ThorondorBot/1.0 (+https://github.com/FeanorsCodeSL/thorondor)` | string | Stable outbound Crawl4AI browser identity. Must contain an HTTP(S) contact URL and no newline characters. | `CRAWLER_ROBOTS_USER_AGENT` |
| `CRAWLER_ROBOTS_USER_AGENT` | Required / `ThorondorBot` | token | Stable robots matching token. Must use letters, digits, `.`, `_`, or `-` and appear in `CRAWLER_USER_AGENT`. | `CRAWLER_USER_AGENT` |
| `SITE_DEFAULT_DELAY_S` | Required / `0.5` | float (≥ 0) | Minimum spacing between map/crawl target requests to one host. | `SITE_MAX_JITTER_S` |
| `SITE_MAX_JITTER_S` | Required / `0.25` | float (≥ 0) | Maximum deterministic per-host spacing jitter. | `SITE_DEFAULT_DELAY_S` |
| `SITE_MAX_COOLDOWN_S` | Required / `300` | int (≥ 1) | Maximum adaptive 403/429 and parsed `Retry-After` cooldown in seconds. | — |
| `ROBOTS_CACHE_TTL_S` | Required / `86400` | int (1–86400) | Absolute process-local robots snapshot TTL in seconds. Reads do not extend it. | `MAX_ROBOTS_BYTES` |
| `MAX_ROBOTS_BYTES` | Required / `262144` | int (≥ 1) | Maximum robots body parsed per origin. Must not exceed `MAX_CONTENT_BYTES`. | `ROBOTS_CACHE_TTL_S` |
| `MAX_SITEMAP_BYTES` | Required / `262144` | int (≥ 1) | Maximum body parsed for one sitemap document. Must not exceed `MAX_CONTENT_BYTES`. | `MAX_SITEMAP_ENTRIES` |
| `MAX_SITEMAP_ENTRIES` | Required / `500` | int (1–500) | Maximum entries retained from one sitemap document. | `MAX_SITEMAP_DOCUMENTS` |
| `MAX_SITEMAP_DOCUMENTS` | Required / `16` | int (1–`MAX_INTERNAL_FANOUT`) | Maximum sitemap documents fetched by one map or crawl operation. | `MAX_SITEMAP_ENTRIES` |

### Markdown Extraction

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MARKDOWN_EXTRACTOR` | Required / `trafilatura` | string | Must be `trafilatura`. Only this value is supported; any other raises `RuntimeError`. | — |
| `MARKDOWN_EXTRACTOR_FAVOR_RECALL` | Required / `true` | bool | Enable recall-favoring mode in trafilatura. | — |
| `MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS` | Required / `false` | bool | Include comment sections in extracted content. | — |
| `MARKDOWN_EXTRACTOR_INCLUDE_TABLES` | Required / `true` | bool | Include HTML table content. | — |
| `MARKDOWN_EXTRACTOR_DEDUPLICATE` | Required / `true` | bool | Enable trafilatura's internal deduplication pass. | — |

### Chunker Tuning

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `CHUNKER_DEFAULT_STRATEGY_VERSION` | Required / `cluster-semantic@1` | string | Default chunking strategy version. Sent to the chunker service when the request omits `strategy_version`. | — |
| `CHUNKER_MAX_SEGMENTS_DP` | Required / `10000` | int (≥ 1) | Segment count threshold above which `ClusterSemanticChunker` falls back from O(N²) DP to greedy-semantic. Memory for the full DP matrix at 10000 segments ≈ 400 MB. | `REWARD_CACHE_MAX_SIZE` |
| `REWARD_CACHE_MAX_SIZE` | Required / `100000` | int (≥ 1) | LRU cache size bound for the DP reward function. One entry per `(start, end)` pair. | `CHUNKER_MAX_SEGMENTS_DP` |
| `CHUNKER_MEM_LIMIT` | Compose only / `768m` | Docker memory string | Docker memory limit for the chunker container. Size above `CHUNKER_MAX_SEGMENTS_DP` can trigger OOM without this limit. | `CHUNKER_MAX_SEGMENTS_DP` |

### Embedding (read by chunking service)

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `EMBEDDING_ENDPOINT` | Required / `http://embedding:80` | URL | Base URL of the OpenAI-compatible `/v1/embeddings` server. | `EMBEDDING_MODEL` |
| `EMBEDDING_MODEL` | Required / `BAAI/bge-m3` | string | Model name sent in embedding requests. | `EMBEDDING_ENDPOINT` |
| `EMBEDDING_MODEL_REVISION` | Compose only / `5617a9f61b028005a4858fdac845db406aefb181` | commit | Immutable Hub revision passed to bundled TEI. | `EMBEDDING_MODEL` |
| `EMBEDDING_API_KEY` | Optional (blank) | string | Bearer token for the embedding server. Blank disables the header. | `EMBEDDING_ENDPOINT` |
| `EMBEDDING_BATCH_SIZE` | Required / `64` | int (≥ 1) | Texts per embedding API call. | `EMBEDDING_TIMEOUT_S` |
| `EMBEDDING_TIMEOUT_S` | Required / `60` | float (> 0) | Embedding request timeout in seconds. | `EMBEDDING_BATCH_SIZE` |

### Reranker

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `RERANKER_ENDPOINT` | Required / `http://reranker:80` | URL | Reranker server base URL. For llama.cpp: `http://reranker:8080`. | `RERANKER_PATH` |
| `RERANKER_MODEL` | Required / `BAAI/bge-reranker-v2-m3` | string | Model name sent in rerank requests. | `RERANKER_ENDPOINT` |
| `RERANKER_MODEL_REVISION` | Compose only / `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | commit | Immutable Hub revision passed to bundled TEI. | `RERANKER_MODEL` |
| `RERANKER_PATH` | Required / `/rerank` | string | Rerank endpoint path. For llama.cpp: `/reranking`. | `RERANKER_ENDPOINT` |
| `RERANKER_HEALTH_PATH` | Required / `/health` | string | Reranker health endpoint path. | `RERANKER_ENDPOINT` |
| `RERANKER_API_KEY` | Optional (blank) | string | Bearer token for the reranker. Blank disables the header. | `RERANKER_ENDPOINT` |
| `RERANKER_BATCH_SIZE` | Required / `32` | int (≥ 1) | Chunks per reranker API call. | `RERANKER_TIMEOUT_S` |
| `RERANKER_TIMEOUT_S` | Required / `30` | int (≥ 1) | Reranker request timeout in seconds. | `RERANKER_BATCH_SIZE` |
| `RELEVANCE_SCORE_FLOOR` | Required / `0.0` | float | Passages with reranker score ≤ this value are dropped post-reranking. `0.0` disables the floor. Has no effect when `stats.reranked=false`. | `RERANKER_ENDPOINT` |
| `EVIDENCE_QUALITY_ENABLED` | Required / `false` | bool | Enable the `evidence-quality@1` structural filter. It remains disabled by default pending broader independent-corpus measurement. | `RELEVANCE_SCORE_FLOOR` |

### Optional LLM Planner

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `LLM_ENDPOINT` | Optional (blank) | URL | OpenAI-compatible chat completions base URL. Blank activates `IdentityPlanner` (no decomposition). | `LLM_MODEL` |
| `LLM_MODEL` | Optional (blank) | string | Model name for the LLM planner. Required when `LLM_ENDPOINT` is set. | `LLM_ENDPOINT` |
| `LLM_API_KEY` | Optional (blank) | string | Bearer token for the LLM planner. | `LLM_ENDPOINT` |

### URL Safety and SSRF Protection

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `URL_SAFETY_BLOCKED_IP_CATEGORIES` | Required / `loopback,link_local,private,reserved,multicast,unspecified` | comma-separated strings | IP address categories blocked before crawl. Valid values: `loopback`, `link_local`, `private`, `reserved`, `multicast`, `unspecified`. | `URL_SAFETY_BLOCKED_SPECIAL_IPS` |
| `URL_SAFETY_BLOCKED_SPECIAL_IPS` | Required / `169.254.169.254,fd00:ec2::254` | comma-separated IPs | Specific IPs always blocked regardless of category. | `URL_SAFETY_BLOCKED_IP_CATEGORIES` |
| `URL_SAFETY_NAT64_NETWORKS` | Required / `64:ff9b::/96` | comma-separated IPv6 CIDRs | IPv6 NAT64 prefixes; the low 32 bits are extracted as IPv4 and re-checked. | — |
| `URL_SAFETY_SIX_TO_FOUR_NETWORKS` | Required / `2002::/16` | comma-separated IPv6 CIDRs | IPv6 6-to-4 networks; bits 17–48 are extracted as IPv4 and re-checked. | — |
| `URL_SAFETY_IPV4_COMPAT_NETWORKS` | Required / `::/96` | comma-separated IPv6 CIDRs | IPv4-compatible IPv6 networks; low 32 bits extracted and re-checked. | — |

### MCP Transport Security

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MCP_ALLOWED_HOSTS` | Required / `127.0.0.1:*,localhost:*,[::1]:*,thorondor:*,orchestrator:*` | comma-separated host patterns | Host headers accepted by the MCP streamable HTTP endpoint. Supports exact values and wildcard port patterns ending in `:*`. | `MCP_ALLOWED_ORIGINS` |
| `MCP_ALLOWED_ORIGINS` | Required / `http://127.0.0.1:*,http://localhost:*,http://[::1]:*,http://thorondor:*,http://orchestrator:*` | comma-separated origins | Origin headers accepted by the MCP streamable HTTP endpoint when an Origin header is present. Supports exact values and wildcard port patterns ending in `:*`. | `MCP_ALLOWED_HOSTS` |

### Domain Allow/Block Lists

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `DOMAIN_BLOCKLIST` | Optional (blank) | comma-separated hostnames | Domains excluded from all searches. Subdomain matching: blocking `example.com` also blocks `sub.example.com`. | `ALLOWLIST_ONLY` |
| `DOMAIN_ALLOWLIST` | Optional (blank) | comma-separated hostnames | Permitted domains when `ALLOWLIST_ONLY=true`. | `ALLOWLIST_ONLY` |
| `ALLOWLIST_ONLY` | Required / `false` | bool | When `true`, restricts crawls to `DOMAIN_ALLOWLIST` ∩ per-call `domains`. Cannot be escaped by callers when operator sets it `true`. | `DOMAIN_ALLOWLIST` |

### SSRF Egress Proxy

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `PROXY_HOST` | Required / `0.0.0.0` | string | Proxy listen address inside its container. | `PROXY_PORT` |
| `PROXY_PORT` | Required / `8888` | int (1–65535) | Proxy listen port. | `PROXY_HOST` |
| `PROXY_MAX_HEADER_BYTES` | Required / `65536` | int (≥ 1) | Maximum bytes read for a single HTTP request's headers. | — |
| `PROXY_READ_CHUNK_BYTES` | Required / `4096` | int (≥ 1) | Read chunk size for incoming data from the client. | `PROXY_RELAY_CHUNK_BYTES` |
| `PROXY_RELAY_CHUNK_BYTES` | Required / `65536` | int (≥ 1) | Chunk size for relaying data to/from upstream. | `PROXY_READ_CHUNK_BYTES` |
| `PROXY_BLOCKED_IP_CATEGORIES` | Required / `loopback,link_local,private,reserved,multicast,unspecified` | comma-separated strings | IP categories blocked at the proxy layer. Same valid values as `URL_SAFETY_BLOCKED_IP_CATEGORIES`. | `PROXY_BLOCKED_SPECIAL_IPS` |
| `PROXY_BLOCKED_SPECIAL_IPS` | Required / `169.254.169.254,fd00:ec2::254` | comma-separated IPs | Special IPs blocked at the proxy layer. | `PROXY_BLOCKED_IP_CATEGORIES` |
| `PROXY_NAT64_NETWORKS` | Required / `64:ff9b::/96` | comma-separated IPv6 CIDRs | NAT64 networks for proxy IP expansion. | — |
| `PROXY_SIX_TO_FOUR_NETWORKS` | Required / `2002::/16` | comma-separated IPv6 CIDRs | 6-to-4 networks for proxy IP expansion. | — |
| `PROXY_IPV4_COMPAT_NETWORKS` | Required / `::/96` | comma-separated IPv6 CIDRs | IPv4-compat networks for proxy IP expansion. | — |

### Crawl4AI Egress Safety

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `CRAWL4AI_ALLOW_INTERNAL_URLS` | Required / `false` | bool | Keeps Crawl4AI 0.9.2's connect-time DNS-pinning proxy restricted to globally routable targets. Managed deployments must not enable the internal-target escape hatch. | `URL_SAFETY_BLOCKED_IP_CATEGORIES` |

The `PROXY_*` settings above configure the retained first-party proxy service. Crawl4AI 0.9.2 does not consume them and must not receive standard HTTP proxy environment variables.

### API Keys and Secrets

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `SEARXNG_SECRET` | Optional (blank — generated by deploy script) | string | SearXNG HMAC secret. Must be non-blank when SearXNG starts. The deploy script generates and injects it when blank. Never commit a real value. | — |
| `SEARXNG_API_KEY` | Optional (blank) | string | Bearer token for SearXNG's JSON search API. | `SEARXNG_URL` |
| `CRAWL4AI_API_KEY` | Generated for managed deployments | string | Bearer token passed to the orchestrator and the bundled Crawl4AI container. Deploy scripts and the configurator generate it when blank; BYO endpoints may leave it blank only when their API is unauthenticated. | `CRAWL4AI_URL` |
| `CHUNKER_API_KEY` | Optional (blank) | string | Bearer token for the chunking service. | `CHUNKER_URL` |
| `RERANKER_API_KEY` | Optional (blank) | string | Bearer token for the reranker. | `RERANKER_ENDPOINT` |
| `EMBEDDING_API_KEY` | Optional (blank) | string | Bearer token for the embedding server (read by the chunker service). | `EMBEDDING_ENDPOINT` |
| `LLM_API_KEY` | Optional (blank) | string | Bearer token for the optional LLM planner. | `LLM_ENDPOINT` |

### llama.cpp Overrides (`.env.llamacpp` only)

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `LLAMACPP_IMAGE` | Required | Docker image reference | Pinned llama.cpp server image SHA. Do not change without testing the new SHA. | — |
| `LLAMACPP_EMBEDDING_MODEL` | Required / `/models/bge-m3.gguf` | container path | Container path to the embedding GGUF. Mounted from `./models:/models:ro`. | `LLAMACPP_EMBEDDING_ALIAS` |
| `LLAMACPP_EMBEDDING_ALIAS` | Required / `bge-m3` | string | Model alias used in embedding API requests (`model` field). Must match `EMBEDDING_MODEL` when the llama.cpp overlay is active. | `LLAMACPP_EMBEDDING_MODEL` |
| `LLAMACPP_EMBEDDING_POOLING` | Required / `cls` | string | Pooling mode for the embedding server (`--pooling` flag). `cls` for BGE-M3. | — |
| `LLAMACPP_EMBEDDING_CONTEXT` | Required / `8192` | int | Context window size for the embedding server. | — |
| `LLAMACPP_RERANKER_MODEL` | Required / `/models/bge-reranker-v2-m3.gguf` | container path | Container path to the reranker GGUF. | `LLAMACPP_RERANKER_ALIAS` |
| `LLAMACPP_RERANKER_ALIAS` | Required / `bge-reranker-v2-m3` | string | Model alias for reranker requests. Must match `RERANKER_MODEL` when the overlay is active. | `LLAMACPP_RERANKER_MODEL` |
| `LLAMACPP_RERANKER_CONTEXT` | Required / `8192` | int | Context window size for the reranker server. | — |
| `LLAMACPP_BATCH` | Required / `8192` | int | Batch size (`-b`) for llama.cpp. | `LLAMACPP_UBATCH` |
| `LLAMACPP_UBATCH` | Required / `8192` | int | Micro-batch size (`-ub`) for llama.cpp. | `LLAMACPP_BATCH` |

### Production Image Overlay (`.env.production` only)

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `THORONDOR_ORCHESTRATOR_IMAGE` | Required / `ghcr.io/feanorscodesl/thorondor-orchestrator:0.1.0` | Docker image reference | First-party orchestrator image. Use the release digest ref for production promotion. | — |
| `THORONDOR_CHUNKER_IMAGE` | Required / `ghcr.io/feanorscodesl/thorondor-chunker:0.1.0` | Docker image reference | First-party chunker image. Use the release digest ref for production promotion. | — |
| `THORONDOR_EGRESS_PROXY_IMAGE` | Required / `ghcr.io/feanorscodesl/thorondor-egress-proxy:0.1.0` | Docker image reference | First-party SSRF egress proxy image. Use the release digest ref for production promotion. | — |
| `THORONDOR_SEARXNG_IMAGE` | Required / pinned upstream digest | Docker image reference | SearXNG image used by the production slice. May point to a GHCR mirror of the same pinned artifact. | `SEARXNG_URL` |
| `THORONDOR_CRAWL4AI_IMAGE` | Required / pinned upstream digest | Docker image reference | Crawl4AI image used by the production slice. May point to a GHCR mirror of the same pinned artifact. | `CRAWL4AI_URL` |
| `THORONDOR_APP_NETWORK` | Required / `app-network` | Docker network name | External network provided by Tengwar and shared with model services. | `EMBEDDING_ENDPOINT`, `RERANKER_ENDPOINT` |
| `THORONDOR_SEARXNG_CONFIG_DIR` | Required / `./searxng` | host path | Directory mounted at `/etc/searxng:ro`. In production this should be a copied config directory, not the Thorondor source tree. | `SEARXNG_BASE_URL` |

---

## Validation Rules

The settings loader enforces the following constraints at startup time:

| Constraint | Error |
|---|---|
| `CRAWL_CONCURRENCY` must be between 1 and 20 | `RuntimeError: CRAWL_CONCURRENCY must be between 1 and {MAX_CRAWL_CONCURRENCY}` |
| `CRAWL_PER_HOST_CONCURRENCY` ≥ 1 | `RuntimeError` |
| `CRAWLER_USER_AGENT` contains a contact URL and no newlines | `RuntimeError` |
| `CRAWLER_ROBOTS_USER_AGENT` is a valid token included in the outbound identity | `RuntimeError` |
| `RERANKER_BATCH_SIZE` ≥ 1 | `RuntimeError` |
| `RERANKER_TIMEOUT_S` ≥ 1 | `RuntimeError` |
| `HEALTHCHECK_TIMEOUT_S` > 0 | `RuntimeError` |
| `HEALTHCHECK_MAX_CONNECTIONS` ≥ 1 | `RuntimeError` |
| `HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS` ≥ 1 | `RuntimeError` |
| `MARKDOWN_EXTRACTOR` must equal `trafilatura` (case-insensitive) | `RuntimeError: MARKDOWN_EXTRACTOR must be trafilatura` |
| `MAX_SUBQUERIES` between 1 and 8 | `RuntimeError: MAX_SUBQUERIES must be between 1 and 8` |
| `SEARCH_PROFILE_*_TOKEN_BUDGET` ≥ 1 | `RuntimeError: search profile token_budget must be >= 1` |
| `SEARCH_PROFILE_*_MAX_URLS` ≥ 1 | `RuntimeError: search profile max_urls must be >= 1` |
| `SEARCH_PROFILE_*_MAX_PASSAGES` ≥ 1 | `RuntimeError: search profile max_passages must be >= 1` |
| `URL_SAFETY_BLOCKED_IP_CATEGORIES` must use only valid category names | `RuntimeError: URL_SAFETY_BLOCKED_IP_CATEGORIES contains unsupported categories: ...` |
| `URL_SAFETY_NAT64_NETWORKS`, `*_SIX_TO_FOUR_NETWORKS`, `*_IPV4_COMPAT_NETWORKS` must contain only IPv6 CIDRs | `RuntimeError: ... must contain only IPv6 networks` |
| Every key used by Python settings loader must be present in the environment (not just `.env`) | `RuntimeError: Required environment variable {name} is not set` |

Cross-key constraints:

- `ALLOWLIST_ONLY=true` without any entries in `DOMAIN_ALLOWLIST` means all calls will return `no_urls_after_selection` unless per-call `domains` is provided in every request.
- `LLM_ENDPOINT` set and `LLM_MODEL` blank: the `LlmPlanner` will be constructed but model-less requests will return `[query]` (the fallback path). For correctness, always set both together.
- `RERANKER_PATH` must match the server's actual endpoint. The llama.cpp server uses `/reranking`; TEI uses `/rerank`. A mismatch produces 404s that cause `RerankerUnavailable` on every search.
- `EMBEDDING_ENDPOINT` set to a URL that ends in `/v1` will have `/embeddings` appended; ending in `/embeddings` is used as-is; anything else gets `/v1/embeddings` appended.

---

## Search Profile Schema

Search profiles are named preset bundles. Three profiles are built-in: `quick`, `research`, and `deep`. Each has three sub-keys:

```
SEARCH_PROFILE_{NAME}_TOKEN_BUDGET   # Maximum tokens in assembled passages
SEARCH_PROFILE_{NAME}_MAX_URLS       # Maximum URLs to crawl
SEARCH_PROFILE_{NAME}_MAX_PASSAGES   # Maximum passages returned
```

Where `{NAME}` is the uppercase profile name: `QUICK`, `RESEARCH`, or `DEEP`.

Profile defaults are resolved at request time in this precedence order:

1. **Explicit request field** — if the caller provides `token_budget`, `max_urls`, or `max_passages` in the request body, it overrides the profile and the server default.
2. **Profile default** — if a `search_profile` is named in the request and the profile has a value for the sub-key, it is used.
3. **Server default** — `DEFAULT_TOKEN_BUDGET` for `token_budget`; `MAX_URLS` for `max_urls`; no default for `max_passages` (uncapped).

Hard request-body caps enforced by the Pydantic model (cannot be exceeded even with explicit fields):

| Field | Cap |
|---|---|
| `query` | 500 characters |
| `token_budget` | 16,000 |
| `max_urls` | 20 |
| `max_passages` | 50 |

The built-in profile defaults as shipped:

| Profile | `token_budget` | `max_urls` | `max_passages` |
|---|---|---|---|
| `quick` | 2,000 | 5 | 5 |
| `research` | 8,000 | 12 | 20 |
| `deep` | 16,000 | 20 | 40 |
