# Architecture Overview

## 1. System Purpose

Thorondor is a self-hosted semantic web-search service designed for agent workflows that require live web evidence with provenance. It solves the problem of agents needing up-to-date, cited information from the open web without depending on commercial hosted APIs (Exa, Tavily, Bing Search, etc.) that introduce data residency concerns, rate limits, and vendor lock-in.

The design is built around three hard constraints:

1. **No persistent corpus** — no vector store, no web index. Every search is a fresh live request; nothing is cached between calls.
2. **Citations are first-class** — every passage is linked to the URL and title of its source page. Agents always know where a fact came from.
3. **Operator-controlled data path** — the operator decides which search engines SearXNG uses, which model endpoints handle embeddings and reranking, and which domains are crawlable. No data leaves the operator's infrastructure unless the operator explicitly configures outbound endpoints.

## 2. Component Map

### Orchestrator (`orchestrator/`)

The central FastAPI service. It exposes `POST /v1/search`, a backward-compatible `POST /search` alias, and an MCP `web_search` tool mounted at `/mcp`. A single `run_search` pipeline function handles both surfaces.

The orchestrator is the only service that speaks to all other components. It holds no state between requests other than shared HTTP connection pools (reused for efficiency). On startup it validates every required environment variable through a strict settings loader; missing or blank required keys raise `RuntimeError` and prevent the process from starting.

### Semantic Chunking Service (`semantic-chunking-service/`)

A standalone FastAPI service exposing `POST /chunk` and `GET /healthz`. It receives page markdown from the orchestrator and returns semantically coherent chunks with token counts, provenance metadata, and exact Unicode code-point spans. The orchestrator uses `ORCHESTRATOR_MARKDOWN` mode so already-cleaned Markdown is not destructively pre-cleaned again.

The core algorithm is `ClusterSemanticChunker`: it splits text into ~50-token segments, generates embeddings for each segment using the configured OpenAI-compatible embedding server, builds an N×N cosine-similarity matrix, and uses dynamic programming to find globally optimal chunk boundaries that maximize semantic coherence within each chunk. When the segment count exceeds `CHUNKER_MAX_SEGMENTS_DP` (OOM guard), it falls back to a greedy-semantic algorithm that computes only adjacent-pair similarities. If the embedding server is unreachable, it falls back further to token-based splitting and marks chunks `embedding_degraded=true`.

### SearXNG

An unmodified upstream SearXNG Docker image, configured through `searxng/settings.yml`. Thorondor uses SearXNG as a multi-engine URL discovery layer: queries are submitted as `GET /search?q=...&format=json` and SearXNG fans out to configured search engines, deduplicates results, and returns ranked URLs with snippets. SearXNG is an AGPL-3.0-licensed component; Thorondor never modifies or vendors it.

### Crawl4AI

The public upstream Crawl4AI 0.9.2 self-hosted Docker API (`unclecode/crawl4ai@sha256:bd36741e...`), consumed over the internal Compose network. The orchestrator submits `POST /crawl` requests to extract page content. Crawl4AI handles JavaScript rendering, robots.txt checking, and returns both raw HTML and Markdown forms of the page content. Thorondor never vendors or patches Crawl4AI source.

Crawl4AI's outbound HTTP traffic routes through the embedded SSRF egress proxy to block requests to RFC-1918 and embedded-IPv4 IPv6 addresses.

### SSRF Egress Proxy (`ssrf-proxy/`)

A minimal async HTTP CONNECT proxy built in Python's `asyncio`. It sits between Crawl4AI and the public internet. On every CONNECT or plain HTTP request it resolves the target hostname, expands IPv6 addresses (including NAT64, 6-to-4, and IPv4-compatible forms) to their embedded IPv4 equivalents, and rejects connections to loopback, link-local, private, reserved, multicast, unspecified, and operator-specified special IP addresses. This prevents Crawl4AI from being used to reach internal network resources.

The orchestrator also performs pre-crawl URL safety validation independently of the proxy (before sending URLs to Crawl4AI), providing defence-in-depth.

### Embedding Server

An OpenAI-compatible `/v1/embeddings` server used by the semantic chunking service. In the `bundled-models` Compose profile this is a pinned Hugging Face Text Embeddings Inference (TEI) container. In the `llamacpp-models` profile it is a llama.cpp server container loading a GGUF file from the host's `models/` directory. Operators may substitute any OpenAI-compatible embedding endpoint via `EMBEDDING_ENDPOINT`.

### Reranker Server

An OpenAI-compatible reranker server used by the orchestrator. In the `bundled-models` profile this is a TEI container. In the `llamacpp-models` profile it is a llama.cpp server with `--reranking` enabled, loading a GGUF reranker file. The orchestrator calls `POST <RERANKER_ENDPOINT><RERANKER_PATH>` with `{"query": ..., "documents": [...], "model": ...}` and receives a scored list. If the reranker is unreachable, the pipeline degrades gracefully to position-based ordering.

### Optional LLM Planner

When `LLM_ENDPOINT` and `LLM_MODEL` are configured, the orchestrator uses an `LlmPlanner` client that calls `POST /v1/chat/completions` to decompose the user query into up to `MAX_SUBQUERIES` sub-queries. Each sub-query is run against SearXNG in parallel, and the results are merged and deduplicated before URL selection. When `LLM_ENDPOINT` is blank (the default), an `IdentityPlanner` is used instead, which simply returns the original query unchanged.

## 3. Data Flow Narrative

A single search request proceeds as follows:

1. **Request arrival** — an HTTP client or MCP client submits a request to the orchestrator. The middleware assigns or propagates `X-Request-ID`.

2. **Query planning** — if `decompose=true` and an LLM planner is configured, the query is sent to the LLM planner which returns 1–3 sub-queries as a JSON array. The list is capped to `MAX_SUBQUERIES`. If planning fails, the original query is used.

3. **URL discovery** — each sub-query is dispatched concurrently to SearXNG (`GET /search?q=...`). Results from all sub-queries are merged and deduplicated by URL, preserving the highest score for each URL. SearXNG's `unresponsive_engines` entries are retained in `stats`. Usable results with reported engine failures set `stats.discovery_status=degraded`; reported failures with no usable results set it to `unavailable` and return `reason=search_provider_unavailable`. A response with no results and no reported engine failures remains `reason=no_results_from_discovery`. `stats.urls_discovered` is set.

4. **URL safety filter** — each discovered URL is checked against the `UrlSafetyPolicy`: hostname resolution is moved off the event loop with bounded concurrency, all returned IPs are checked against blocked categories and special IPs, and IPv6 addresses are expanded to find embedded IPv4 equivalents. Unsafe URLs are dropped silently.

5. **URL selection** — the `SelectionPolicyImpl` scores candidates by a combination of the SearXNG discovery score and a lexical overlap with the original query. It enforces per-domain limits (max 3 URLs per domain unless the allowed domain set has ≤ 1 entry), engine diversity, and domain diversity passes before filling by score. `stats.urls_selected` is set.

6. **Content extraction** — the orchestrator submits the original selected URL and configured crawler identity to Crawl4AI with `POST /crawl`, up to `CRAWL_CONCURRENCY` in parallel with `CRAWL_PER_HOST_CONCURRENCY` per hostname. Crawl4AI has no direct egress network and reaches target sites only through the SSRF proxy. The orchestrator captures bounded final URL, status, content type, validators, links, and metadata, then revalidates every changed final URL before accepting the page. Pages that return no markdown content are dropped. `stats.urls_crawled_ok` and `stats.urls_crawled_failed` are updated.

7. **Markdown cleaning** — each page's HTML is passed through trafilatura to produce clean markdown. Boilerplate, navigation, footers, and comment sections (if disabled) are removed. `stats.markdown_chars_before` and `stats.markdown_chars_after` reflect the reduction.

8. **Content deduplication** — near-duplicate pages (based on content hashing) are dropped. `stats.pages_deduped` records how many were removed.

9. **Semantic chunking** — the orchestrator calls `POST /chunk` on the chunking service for each page. The chunker splits page markdown into semantically coherent chunks using `ClusterSemanticChunker`. Exact mode returns each chunk as `cleaned_markdown[start_index:end_index]`; the orchestrator verifies that equality before issuing `document_id` and `evidence_id`. Chunk metadata also includes `source_url`, `title`, `position`, `source_id`, `chunk_strategy`, and `embedding_degraded`. If the chunker returns a 5xx error, a `ChunkerUnavailable` exception propagates and the orchestrator returns a 503.

10. **Candidate prefilter** — if more than 50 chunks were returned, the `CandidatePrefilterImpl` applies a fast lexical scorer to select the top-50 before reranking. The filter is source-preserving: it ensures at least one chunk from each crawled source is represented. `stats.chunks_prefiltered` records how many were dropped.

11. **Reranking** — the orchestrator calls the reranker in batches of `RERANKER_BATCH_SIZE`. Each batch sends `{"query": ..., "documents": [...], "model": ...}` and receives relevance scores. Failed batches are tracked; chunks from failed batches are assigned a floor score below all successful scores to preserve relative ordering. If all batches fail, `RerankerUnavailable` is raised and the pipeline falls back to position-based scoring with `stats.reranked=false`.

12. **Relevance floor** — if `RELEVANCE_SCORE_FLOOR > 0.0` and reranking succeeded, passages scoring at or below the floor are dropped (unless all passages would be dropped, in which case all are kept).

13. **Token-budget assembly** — `ResultAssemblerImpl` sorts by descending score and greedily selects chunks until either the `token_budget` is exhausted or `max_passages` is reached. A final exact chunk may be truncated to the remaining word-token budget by shortening its end-exclusive span and recomputing its evidence ID. Citations are deduplicated by source-document identity when available, and each passage references its citation by compatibility integer ID.

14. **Response** — `SearchResponse` is serialised and returned with additive evidence spans, stable identities, section headings, and bounded source-attributed metadata. If `include_raw_markdown=true`, both original and exact cleaned Markdown for each cited page are appended. The structured log summary is emitted.

## 4. Deployment Topologies

### (a) Full-local llama.cpp profile

The `llamacpp-models` Compose profile activates two llama.cpp containers that load GGUF files from the host's `./models/` directory. The embedding server listens on `http://embedding:8080` (paths `/v1/embeddings`, `/health`) and the reranker on `http://reranker:8080` (paths `/reranking`, `/health`). The overlay `docker-compose.llamacpp.yml` overrides `RERANKER_PATH=/reranking` and `RERANKER_ENDPOINT=http://reranker:8080`. No GPU is required; all inference runs on CPU. This is the default development topology.

### (b) Bundled TEI containers profile

The `bundled-models` profile (base `docker-compose.yml`) activates Hugging Face TEI containers for embedding and reranking. The pinned image is AMD64-only and requires NVIDIA CUDA runtime access. The containers download model weights from the immutable Hub revisions declared in `.env` on first start. This profile is suitable for AMD64 NVIDIA deployments where pre-downloading GGUF files is not practical.

### (c) BYO remote endpoints

Set `EMBEDDING_ENDPOINT`, `RERANKER_ENDPOINT`, and optionally `LLM_ENDPOINT` to external OpenAI-compatible servers. The embedding, reranker, and LLM containers are not started. This topology is suitable for deployments where model inference is handled by a shared inference cluster.

### (d) ARM64 / DGX Spark

The llama.cpp build `b10276` image is pinned to a specific SHA (`bde659bf...`) that is verified for both `linux/amd64` and `linux/arm64`. On ARM64 hosts the same `deploy-llamacpp.ps1` command applies with the same env files. The pinned TEI revision `4150561` is AMD64-only, so the bundled-models profile is not supported natively on ARM64. The Thorondor first-party code itself remains architecture-independent.

## 5. Extension Points

**Search engines** — SearXNG's `settings.yml` controls which engines are active. Operators can add or remove engines without touching Thorondor code.

**Embedding server** — any server that speaks `POST /v1/embeddings` (OpenAI format) works. Set `EMBEDDING_ENDPOINT` and `EMBEDDING_MODEL`. The chunker service is model-agnostic.

**Reranker** — any server that accepts `POST <path>` with `{"query", "documents", "model"}` and returns `{"results": [{"index", "score"}, ...]}` works. Adjust `RERANKER_ENDPOINT`, `RERANKER_PATH`, `RERANKER_HEALTH_PATH`, and `RERANKER_MODEL`.

**Query planner / LLM** — any OpenAI-compatible chat completions server works. Set `LLM_ENDPOINT` and `LLM_MODEL`. The planner prompt asks for a JSON array of 1–3 sub-queries.

**Domain policy** — `DOMAIN_BLOCKLIST`, `DOMAIN_ALLOWLIST`, `ALLOWLIST_ONLY`, and per-call `domains`/`exclude_domains` in the request body provide layered domain control without code changes.

**Chunking strategy** — the chunker service resolves strategy versions from `CHUNKER_DEFAULT_STRATEGY_VERSION`. New strategies can be registered in `semantic-chunking-service/chunking/strategies.py`.

## 6. Intentionally Out of Scope

- **Persistent corpus** — no web index, no vector store. The design is stateless between requests.
- **Implemented cache** — there is no result cache. The architecture has a cache seam defined in the interface layer but it is not instantiated in the current deployment.
- **Authentication on the orchestrator** — the REST and MCP endpoints have no built-in auth. Operators should place a reverse proxy with TLS and access control in front of the orchestrator port.
- **Rate limiting** — not implemented in the service itself; add a reverse proxy if needed.
- **Crawled content sandboxing** — beyond Crawl4AI's own isolation and the SSRF proxy, crawled content is not sandboxed at the OS level. The orchestrator treats all crawled text as untrusted.
- **Mandatory hosted vendor** — Thorondor never makes outbound calls to commercial search APIs unless the operator configures SearXNG to use them (an explicit operator choice in `searxng/settings.yml`).
