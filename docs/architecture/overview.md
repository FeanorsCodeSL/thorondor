# Architecture Overview

## 1. System Purpose

Thorondor is a self-hosted semantic web-search service designed for agent workflows that require live web evidence with provenance. It solves the problem of agents needing up-to-date, cited information from the open web without depending on commercial hosted APIs (Exa, Tavily, Bing Search, etc.) that introduce data residency concerns, rate limits, and vendor lock-in.

The design is built around three hard constraints:

1. **No persistent corpus** — no vector store or web index. Search, map, and crawl remain fresh live requests. Known-URL fetches may use an operator-enabled local page cache that is disabled by default, capability-keyed, and retention-bounded. Robots snapshots use a separate bounded in-memory policy cache.
2. **Citations are first-class** — every passage is linked to the URL and title of its source page. Agents always know where a fact came from.
3. **Operator-controlled data path** — the operator decides which search engines SearXNG uses, which model endpoints handle embeddings and reranking, and which domains are crawlable. No data leaves the operator's infrastructure unless the operator explicitly configures outbound endpoints.

## 2. Component Map

### Orchestrator (`orchestrator/`)

The central FastAPI service. It exposes `POST /v1/search`, a backward-compatible `POST /search` alias, `POST /v1/fetch`, `POST /v1/map`, and `POST /v1/crawl`, with matching MCP tools mounted at `/mcp`. All four operation families share process-owned admission, byte, deadline, URL-safety, and Crawl4AI outcome policies. Map and crawl additionally share one deterministic frontier, robots, sitemap, scope, and politeness policy.

The orchestrator is the only service that speaks to all other components. It holds shared HTTP connection pools and robots snapshots, plus an optional SQLite page cache for known-URL fetches. On startup it validates every required environment variable through a strict settings loader; missing or blank required keys raise `RuntimeError` and prevent the process from starting.

### Semantic Chunking Service (`semantic-chunking-service/`)

A standalone FastAPI service exposing `POST /chunk` and `GET /healthz`. It receives page markdown from the orchestrator and returns semantically coherent chunks with token counts, provenance metadata, and exact Unicode code-point spans. The orchestrator uses `ORCHESTRATOR_MARKDOWN` mode so already-cleaned Markdown is not destructively pre-cleaned again.

The core algorithm is `ClusterSemanticChunker`: it splits text into ~50-token segments, generates embeddings for each segment using the configured OpenAI-compatible embedding server, builds an N×N cosine-similarity matrix, and uses dynamic programming to find globally optimal chunk boundaries that maximize semantic coherence within each chunk. When the segment count exceeds `CHUNKER_MAX_SEGMENTS_DP` (OOM guard), it falls back to a greedy-semantic algorithm that computes only adjacent-pair similarities. If the embedding server is unreachable, it falls back further to token-based splitting and marks chunks `embedding_degraded=true`.

### SearXNG

An unmodified upstream SearXNG Docker image, configured through `searxng/settings.yml`. Thorondor uses SearXNG as a multi-engine URL discovery layer: queries are submitted as `GET /search?q=...&format=json` and SearXNG fans out to configured search engines, deduplicates results, and returns ranked URLs with snippets. SearXNG is an AGPL-3.0-licensed component; Thorondor never modifies or vendors it.

### Crawl4AI

The public upstream Crawl4AI 0.9.2 self-hosted Docker API (`unclecode/crawl4ai@sha256:bd36741e...`), consumed over a dedicated internal control network. The orchestrator submits authenticated `POST /crawl` requests to extract pages, robots files, and sitemaps. Crawl4AI handles JavaScript rendering, applies its own robots check for ordinary page requests, and returns raw HTML, Markdown, links, metadata, status, and final-URL data. Thorondor never vendors or patches Crawl4AI source.

Crawl4AI has a separate outbound-only network for its built-in localhost pinning proxy. That proxy resolves each target once, rejects non-global destinations, and connects to the pinned address so Chromium cannot perform a second DNS resolution.

### Retained SSRF Egress Proxy (`ssrf-proxy/`)

A minimal async HTTP CONNECT proxy built in Python's `asyncio`. It remains packaged for deployment compatibility, but Crawl4AI 0.9.2 does not route through it because the hardened upstream server replaces external browser proxy configuration with its own DNS-pinning proxy. Removing this retained service and image pipeline is a separate cleanup after deployment consumers are audited.

The orchestrator performs pre-crawl and changed-final-URL safety validation independently of Crawl4AI's pinning proxy, providing defence-in-depth.

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

3. **URL discovery** — each sub-query is dispatched concurrently to SearXNG (`GET /search?q=...`). A failed sub-query attempt no longer discards successful sibling attempts. Results are merged by normalized URL while preserving distinct sub-query, plural-engine, position, and upstream-score contributions; repeated identical reports within one sub-query do not masquerade as independent agreement. The highest upstream score remains the production discovery score. Per-sub-query elapsed time/result count, per-engine contribution counts, and SearXNG's `unresponsive_engines` are retained in bounded `stats` fields. SearXNG does not expose true per-engine latency. Usable results with any failed attempt or engine failure set `stats.discovery_status=degraded`; failures with no usable results set it to `unavailable`.

4. **URL safety filter** — each discovered URL is checked against the `UrlSafetyPolicy`: hostname resolution is moved off the event loop with bounded concurrency, all returned IPs are checked against blocked categories and special IPs, and IPv6 addresses are expanded to find embedded IPv4 equivalents. Unsafe URLs are dropped silently.

5. **URL selection** — the `SelectionPolicyImpl` scores candidates by a combination of the SearXNG discovery score and a lexical overlap with the original query. It enforces per-domain limits (max 3 URLs per domain unless the allowed domain set has ≤ 1 entry), plural-engine diversity, and domain diversity passes before filling by score. Selected URL diagnostics are retained first; filtered decisions fill the remaining 50-item/64-KiB envelope and omissions are counted by reason.

6. **Content extraction** — the orchestrator submits the original selected URL and configured crawler identity to Crawl4AI with authenticated `POST /crawl`, up to `CRAWL_CONCURRENCY` in parallel with `CRAWL_PER_HOST_CONCURRENCY` per hostname. Crawl4AI reaches target sites through its dedicated egress network and built-in DNS-pinning proxy. The orchestrator captures bounded final URL, status, content type, validators, links, and metadata, then revalidates every changed final URL before accepting the page. Pages that return no markdown content are dropped. `stats.urls_crawled_ok` and `stats.urls_crawled_failed` are updated.

7. **Markdown cleaning** — each page's HTML is passed through trafilatura to produce clean markdown. Boilerplate, navigation, footers, and comment sections (if disabled) are removed. `stats.markdown_chars_before`, `stats.markdown_chars_after`, and an exact-line multiset count of non-empty source Markdown lines absent from the cleaned output are reported.

8. **Content deduplication** — only full normalized-Markdown identity is used for destructive page deduplication. Shared prefixes with different bodies survive, and declared canonical metadata does not change this decision. `stats.pages_deduped` records exact duplicates removed.

9. **Semantic chunking** — the orchestrator calls `POST /chunk` on the chunking service for each page. The chunker splits page markdown into semantically coherent chunks using `ClusterSemanticChunker`. Exact mode returns each chunk as `cleaned_markdown[start_index:end_index]`; the orchestrator verifies that equality before issuing `document_id` and `evidence_id`. Chunk metadata also includes `source_url`, `title`, `position`, `source_id`, `chunk_strategy`, and `embedding_degraded`. If the chunker returns a 5xx error, a `ChunkerUnavailable` exception propagates and the orchestrator returns a 503.

10. **Candidate prefilter** — if more than 50 chunks were returned, the `CandidatePrefilterImpl` applies a fast lexical scorer to select the top-50 before reranking. The filter is source-preserving: it ensures at least one chunk from each crawled source is represented. `stats.chunks_prefiltered` records how many were dropped.

11. **Reranking** — the orchestrator calls the reranker in batches of `RERANKER_BATCH_SIZE`. Each call returns its scores and telemetry together, so concurrent searches cannot overwrite shared counters. Failed batches receive a calculated floor below successful scores. If all batches fail, the pipeline uses position scores with `stats.reranked=false`. Every returned passage keeps the existing scalar final score and a bounded, versioned component naming the calculation that produced it.

12. **Relevance and evidence quality** — if `RELEVANCE_SCORE_FLOOR > 0.0` and reranking succeeded, chunks at or below the floor are dropped. When `EVIDENCE_QUALITY_ENABLED=true`, the deterministic `evidence-quality@1` gate rejects narrowly structured navigation/footer boilerplate and generic-link fragments. It is disabled by default pending broader independent-corpus measurement.

13. **Bounded assembly** — evidence first enters an independent 50-item, 64-KiB-per-serialized-item, 256-KiB-serialized-list envelope. An oversized top-ranked item is shortened while preserving or safely degrading its evidence identity. `ResultAssemblerImpl` then applies the caller's token and passage budgets. A final exact chunk may be truncated to the remaining word-token budget by shortening its end-exclusive span and recomputing its evidence ID.

14. **Response** — `SearchResponse` is serialised with additive evidence spans, stable identities, score components, diagnostics, and quality/drop counters. Optional raw Markdown has a separate 20-item, 256-KiB-per-item, 512-KiB-total envelope, so diagnostics cannot defeat the caller's evidence budget.

Map and crawl use a separate synchronous path. The seed is safety-checked and fetched through Crawl4AI, then same-origin and seed-directory scope are re-homed to its validated final URL. Thorondor obtains one RFC 9309 robots snapshot per origin, reads declared and common sitemaps within document, entry, and byte limits, optionally adds SearXNG `site:` candidates, and traverses admitted links breadth-first. Every candidate passes the same normalization, scope, file, query, safety, robots, deduplication, and depth policy before queueing. `map` returns URL records only; `crawl` adds bounded typed page results. Both report requested and effective origins, source contributions, state transitions, terminal reasons, omissions, and warnings.

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

- **Persistent corpus** — no web index or vector store. Optional known-URL page records are not a searchable corpus and are never used by ordinary search.
- **Search, map, or crawl result cache** — only `/v1/fetch` and `web_fetch` can use the optional page cache. Search, map, and bounded crawl remain live.
- **Scheduler or autonomous actions** — Thorondor evaluates a target watch only when called. Tengwar or another agent runtime owns schedules, notifications, and follow-up actions.
- **Authentication on the orchestrator** — the REST and MCP endpoints have no built-in auth. Operators should place a reverse proxy with TLS and access control in front of the orchestrator port.
- **Rate limiting** — not implemented in the service itself; add a reverse proxy if needed.
- **Crawled content sandboxing** — beyond Crawl4AI's non-root, read-only container posture and built-in egress controls, crawled content is not sandboxed at the OS level. The orchestrator treats all crawled text as untrusted.
- **Mandatory hosted vendor** — Thorondor never makes outbound calls to commercial search APIs unless the operator configures SearXNG to use them (an explicit operator choice in `searxng/settings.yml`).
