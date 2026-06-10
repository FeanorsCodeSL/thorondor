# Configuration Reference

All environment variables must be present in `.env` (and `.env.llamacpp` for the llama.cpp profile). The Python settings loader raises `RuntimeError` on startup for any missing key — including keys that are intentionally blank. Leave optional keys set to an empty string rather than deleting them.

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

### Search Profiles

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `MAX_SUBQUERIES` | Required / `3` | int (≥ 1) | Cap on sub-queries the LLM planner may return. Also caps the list even if decompose=true. | `LLM_ENDPOINT` |
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
| `CRAWL_VALIDATE_REDIRECTS` | Required / `true` | bool | Perform preflight HEAD requests to follow and validate redirect chains before crawling. | `CRAWL_MAX_PREFLIGHT_REDIRECTS` |
| `CRAWL_MAX_PREFLIGHT_REDIRECTS` | Required / `5` | int (≥ 1) | Max redirect hops to follow during preflight validation. | `CRAWL_VALIDATE_REDIRECTS` |

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
| `EMBEDDING_API_KEY` | Optional (blank) | string | Bearer token for the embedding server. Blank disables the header. | `EMBEDDING_ENDPOINT` |
| `EMBEDDING_BATCH_SIZE` | Required / `64` | int (≥ 1) | Texts per embedding API call. | `EMBEDDING_TIMEOUT_S` |
| `EMBEDDING_TIMEOUT_S` | Required / `60` | float (> 0) | Embedding request timeout in seconds. | `EMBEDDING_BATCH_SIZE` |

### Reranker

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `RERANKER_ENDPOINT` | Required / `http://reranker:80` | URL | Reranker server base URL. For llama.cpp: `http://reranker:8080`. | `RERANKER_PATH` |
| `RERANKER_MODEL` | Required / `BAAI/bge-reranker-v2-m3` | string | Model name sent in rerank requests. | `RERANKER_ENDPOINT` |
| `RERANKER_PATH` | Required / `/rerank` | string | Rerank endpoint path. For llama.cpp: `/reranking`. | `RERANKER_ENDPOINT` |
| `RERANKER_HEALTH_PATH` | Required / `/health` | string | Reranker health endpoint path. | `RERANKER_ENDPOINT` |
| `RERANKER_API_KEY` | Optional (blank) | string | Bearer token for the reranker. Blank disables the header. | `RERANKER_ENDPOINT` |
| `RERANKER_BATCH_SIZE` | Required / `32` | int (≥ 1) | Chunks per reranker API call. | `RERANKER_TIMEOUT_S` |
| `RERANKER_TIMEOUT_S` | Required / `30` | int (≥ 1) | Reranker request timeout in seconds. | `RERANKER_BATCH_SIZE` |
| `RELEVANCE_SCORE_FLOOR` | Required / `0.0` | float | Passages with reranker score ≤ this value are dropped post-reranking. `0.0` disables the floor. Has no effect when `stats.reranked=false`. | `RERANKER_ENDPOINT` |

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

### Crawl4AI Proxy Pass-Through

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `CRAWL4AI_HTTP_PROXY` | Required / `http://egress-proxy:8888` | URL | HTTP proxy for Crawl4AI outbound connections. Routes traffic through the SSRF proxy. | `CRAWL4AI_HTTPS_PROXY` |
| `CRAWL4AI_HTTPS_PROXY` | Required / `http://egress-proxy:8888` | URL | HTTPS proxy for Crawl4AI outbound connections. | `CRAWL4AI_HTTP_PROXY` |
| `CRAWL4AI_ALL_PROXY` | Required / `http://egress-proxy:8888` | URL | All-protocol proxy fallback for Crawl4AI. | `CRAWL4AI_HTTP_PROXY` |
| `CRAWL4AI_NO_PROXY` | Optional (blank) | comma-separated hostnames | Hosts Crawl4AI bypasses the proxy for. | `CRAWL4AI_ALL_PROXY` |

### API Keys and Secrets

| Variable | Required / Default | Type | Description | Related |
|---|---|---|---|---|
| `SEARXNG_SECRET` | Optional (blank — generated by deploy script) | string | SearXNG HMAC secret. Must be non-blank when SearXNG starts. The deploy script generates and injects it when blank. Never commit a real value. | — |
| `SEARXNG_API_KEY` | Optional (blank) | string | Bearer token for SearXNG's JSON search API. | `SEARXNG_URL` |
| `CRAWL4AI_API_KEY` | Optional (blank) | string | Bearer token (`CRAWL4AI_API_TOKEN` env in the container) for the Crawl4AI API. | `CRAWL4AI_URL` |
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

---

## Validation Rules

The settings loader enforces the following constraints at startup time:

| Constraint | Error |
|---|---|
| `CRAWL_CONCURRENCY` must be between 1 and 20 | `RuntimeError: CRAWL_CONCURRENCY must be between 1 and {MAX_CRAWL_CONCURRENCY}` |
| `CRAWL_PER_HOST_CONCURRENCY` ≥ 1 | `RuntimeError` |
| `CRAWL_MAX_PREFLIGHT_REDIRECTS` ≥ 1 | `RuntimeError` |
| `RERANKER_BATCH_SIZE` ≥ 1 | `RuntimeError` |
| `RERANKER_TIMEOUT_S` ≥ 1 | `RuntimeError` |
| `HEALTHCHECK_TIMEOUT_S` > 0 | `RuntimeError` |
| `HEALTHCHECK_MAX_CONNECTIONS` ≥ 1 | `RuntimeError` |
| `HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS` ≥ 1 | `RuntimeError` |
| `MARKDOWN_EXTRACTOR` must equal `trafilatura` (case-insensitive) | `RuntimeError: MARKDOWN_EXTRACTOR must be trafilatura` |
| `MAX_SUBQUERIES` ≥ 1 | `RuntimeError` |
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
