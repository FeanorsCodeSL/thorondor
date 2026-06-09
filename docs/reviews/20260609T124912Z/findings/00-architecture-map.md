# 00 - Architecture Map (as-built)

Ground-truth map of the Thorondor stack as implemented. Trust code over docs; drift is logged in section 9.
All references are to files under thorondor/.

The project CLAUDE.md claim "design-first, no code yet" is STALE - both first-party services
(orchestrator/, semantic-chunking-service/) are implemented with tests, Dockerfiles, Compose, and deploy scripts.

---

## 1. Pipeline stages (orchestrator/pipeline.py:run_search)

Single async entrypoint: run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse (pipeline.py:52). Ordered as built:

| # | Stage | Code | Pure/Impure | Protocol / module |
|---|---|---|---|---|
| 1 | Plan | deps.planner.plan(req.query); only if req.decompose else [req.query] (pipeline.py:58) | impure (HTTP) or no-op | QueryPlanner; IdentityPlanner/LlmPlanner |
| 2 | Discover | deps.discovery.search(q, freshness) for all subqueries via asyncio.gather (pipeline.py:62-64) | impure (HTTP) | SearchDiscovery; SearxngDiscovery |
| 3 | Merge/dedup | merge_dedup(result_sets) (pipeline.py:68, merge.py:6) | pure | free function merge.py |
| 4 | Select/budget | deps.selector.select(merged, max_urls, blocklist, allowlist) (pipeline.py:78) | pure | SelectionPolicy; SelectionPolicyImpl |
| 5 | Extract | deps.extractor.extract([r.url ...]) (pipeline.py:84) | impure (HTTP), partial-tolerant | ContentExtractor; Crawl4aiExtractor |
| 6 | Content dedup | content_dedup(pages) (pipeline.py:90, content_dedup.py:15) | pure | free function content_dedup.py |
| 7 | Chunk | deps.chunker.chunk(pages) (pipeline.py:92) | impure (HTTP) | SemanticChunker; ChunkerClient |
| 8 | Pre-filter (documented optional) | NOT IMPLEMENTED - no cosine narrowing stage exists in run_search | - | - |
| 9 | Rerank | deps.reranker.rerank(req.query, chunks) (pipeline.py:101); scores ORIGINAL query | impure (HTTP), graceful-degrade | Reranker; RerankerClient |
| 10 | Assemble | deps.assembler.assemble(scored, token_budget, req.max_passages) (pipeline.py:109) | pure | ResultAssembler; ResultAssemblerImpl |
| 11 | Return | builds SearchResponse; optional raw_markdown if req.include_raw_markdown (pipeline.py:113-128) | pure | - |

- Stage 1 is a no-op by default. build_deps_from_settings (pipeline.py:139-143) installs LlmPlanner only if BOTH settings.llm_endpoint and settings.llm_model are set; otherwise IdentityPlanner (returns [query], planner.py:7-9). req.decompose defaults True (models.py:44) but with the identity planner it still yields [query].
- Early returns (empty SearchResponse + reason): no merged URLs -> no_results_from_discovery (pipeline.py:70-72); no selected URLs -> no_results_from_discovery (:80-82); no crawled pages -> all_crawls_failed (:86-88); no chunks -> no_chunks_after_dedup (:96-98). Helper _empty_response (pipeline.py:47-49).
- Dependencies live in the PipelineDeps dataclass (pipeline.py:33-44) and are injected; run_search constructs nothing.

---

## 2. Protocol seams (orchestrator/interfaces.py)

All typing.Protocol. Concrete clients in orchestrator/clients/; fakes in orchestrator/fakes.py.

| Protocol | Signature (interfaces.py) | Concrete impl | Fake(s) |
|---|---|---|---|
| QueryPlanner | async plan(query: str) -> list[str] (:8-9) | IdentityPlanner, LlmPlanner (clients/planner.py) | FakePlanner (fakes.py:11) |
| SearchDiscovery | async search(subquery, freshness=None) -> list[DiscoveryResult] (:12-13) | SearxngDiscovery (clients/searxng_client.py:11) | FakeDiscovery, EmptyDiscovery, DownDiscovery (fakes.py:20,32,37) |
| SelectionPolicy | select(results, max_urls, blocklist, allowlist=None) -> list[DiscoveryResult] (:16-23) | SelectionPolicyImpl (selection.py:6) | real impl used in fakes |
| ContentExtractor | async extract(urls) -> list[Page] (:26-27) | Crawl4aiExtractor (clients/crawl4ai_client.py:12) | FakeExtractor, EmptyExtractor (fakes.py:42,47) |
| SemanticChunker | async chunk(pages) -> list[Chunk] (:30-31) | ChunkerClient (clients/chunker_client.py:11) | FakeChunker, EmptyChunker, DownChunker (fakes.py:52,61,66) |
| Reranker | async rerank(query, chunks) -> list[ScoredChunk] (:34-35) | RerankerClient (clients/reranker_client.py:11) | FakeReranker, DownReranker (fakes.py:71,76) |
| ResultAssembler | assemble(scored, token_budget, max_passages) -> tuple[list[Passage], list[Citation]] (:38-44) | ResultAssemblerImpl (assembly.py:7) | real impl used in fakes |

Internal dataclasses (types.py): DiscoveryResult, Page, Chunk, ScoredChunk.
Wire models (models.py): SearchRequest, SearchResponse, Passage, Citation, RawMarkdown, SearchStats.

---

## 3. Pure vs impure inventory

Impure (network I/O via httpx): LlmPlanner (/v1/chat/completions), SearxngDiscovery (GET /search), Crawl4aiExtractor (POST /crawl), ChunkerClient (POST /chunk), RerankerClient (POST {path}). IdentityPlanner is impure-by-Protocol but does zero I/O.

Pure (no network, deterministic):
- merge_dedup (merge.py): dict keyed by normalize_url, keeps highest score, sorts by (-score, url).
- SelectionPolicyImpl.select (selection.py): confirmed PURE; filters by blocklist/allowlist via host_for, sorts (-score, url), slices [:max_urls]. No I/O, no hidden state.
- content_dedup (content_dedup.py): sha256 fingerprint of normalized first 2000 chars.
- ResultAssemblerImpl.assemble (assembly.py): confirmed PURE; greedy by descending score under token_budget, assigns/dedups citation ids keyed by normalize_url, respects max_passages. No I/O.
- normalize_url / host_for (normalize.py): URL canonicalization (scheme/host/port, strips utm_*, trailing slash). Shared by merge, selection, assembly, raw-markdown matching.

---

## 4. BYO model seams

Wiring: env -> Settings (settings.py) -> build_deps_from_settings (pipeline.py:131-155) -> client.

| Seam | Env vars | Wire contract | Client | Bundled default? |
|---|---|---|---|---|
| Embedding | EMBEDDING_ENDPOINT, EMBEDDING_MODEL (chunker service only) | POST /v1/embeddings {model, input[]} -> {data:[{embedding, index}]} (embedding_function.py:154-172) | EmbeddingFunction in chunker | Yes; TEI bundled-models profile (docker-compose.yml:60-64) |
| Reranker | RERANKER_ENDPOINT, RERANKER_MODEL, RERANKER_PATH (default /rerank), RERANKER_HEALTH_PATH (/healthz in code, /health in compose/env) | POST {endpoint}{path} {query, documents[], model} -> {results:[{index, score}]} (also reads data, relevance_score, rank_score) (reranker_client.py:17-36) | RerankerClient | Yes; TEI bundled-models profile (docker-compose.yml:66-70); also llama.cpp via docker-compose.llamacpp.yml |
| LLM planner | LLM_ENDPOINT, LLM_MODEL (both optional) | POST /v1/chat/completions; expects JSON-array string in choices[0].message.content (planner.py:17-42) | LlmPlanner | No bundled default; falls back to IdentityPlanner when unset |

No model server is hardcoded; endpoints are env-driven. EmbeddingFunction parses host:port from EMBEDDING_ENDPOINT and rebuilds http://{host}:{port}/v1/embeddings (embedding_function.py:21-31,73) - it FORCES http scheme, REQUIRES an explicit port, and DROPS any URL path, a divergence from a generic base-URL contract. Orchestrator clients (reranker/planner/searxng/crawl) instead use full base URLs verbatim.

---

## 5. The two public surfaces

Both surfaces call the single shared run_search (pipeline.py:52); no forked logic.

- REST POST /search (app.py:30-44): resolves defaults via req.model_copy then await run_search(resolved, get_deps()). Catches SearchDependencyUnavailable -> HTTP 503 with {dependency, reason} (app.py:40-44). Deps lazily built once via module-global get_deps()/get_settings() (app.py:12-27). GET /healthz probes each dependency only on the cold path; once deps are non-None it returns a hardcoded all-true dependency map (app.py:61-91), which RES-1/API-2 flags.
- MCP web_search tool (mcp_server.py:24-34): FastMCP("thorondor") (:7); builds SearchRequest(query, token_budget=4000, max_urls=6) and calls the same run_search, returning ONLY {passages, citations} (no stats, no reason, no raw_markdown). Deps via _get_deps() which defers to app.get_deps() unless overridden by set_deps() (mcp_server.py:11-21).
- Transport/mounting: app.py:94-96 imports mcp and mounts mcp.streamable_http_app() at /mcp (Streamable HTTP). mcp_server.main() (:37-38) also offers a stdio transport for standalone runs. The MCP tool is therefore reachable BOTH mounted under the REST app at /mcp and as a standalone stdio server.

---

## 6. Chunking service (semantic-chunking-service/chunking/)

- Exposure: app.py FastAPI; POST /chunk (app.py:32-64), GET /healthz (:27-29, includes live embedding health_check()).
- /chunk contract (models.py): request {text, source_type(DOCUMENT|WEB_MARKDOWN), strategy_version?, params?{max/min/initial}, metadata?}; response {chunks:[{text, token_count, position, start_index, end_index, metadata}], strategy_version, chunk_count}. metadata is passthrough; service injects strategy_version into each chunk metadata (app.py:60).
- Flow: resolve_strategy(version, overrides) (strategies.py:24) -> preclean(text, source_type) (textprep.py:9; strips standalone image embeds + collapses blank lines for WEB_MARKDOWN) -> ClusterSemanticChunker(...).split_text_with_metadata(text) (app.py:43-51).
- One shared embedder per process: _embedder = EmbeddingFunction() module-global (app.py:18). Token proxy = word count (_length, app.py:21-24; no tiktoken/HF tokenizer wired).
- Embedding seam: EmbeddingFunction.__call__ batches (default 64) -> POST /v1/embeddings (embedding_function.py:94-179). Raises RuntimeError on non-200 or exception.
- DP algorithm (cluster_semantic.py:112-291): segment via RecursiveCharacterTextSplitter (~initial_segment_size tokens, recursive_splitter.py) -> embed -> NxN cosine similarity matrix (_compute_similarity_matrix:347) -> DP maximizing intra-chunk pairwise-similarity reward under max_chunk_size with min_chunk_size soft floor (_dynamic_programming_chunking:391, _fill_dp_table:468, _evaluate_chunk_candidate:490). Reward LRU-cached (REWARD_CACHE_MAX_SIZE). Releases matrix/embeddings + gc.collect() after DP (:260-262).
- Three safety valves (match CLAUDE.md invariants):
  1. OOM guard: len(segments) > CHUNKER_MAX_SEGMENTS_DP (default 10000, settings.py:22) -> _greedy_semantic_chunking (O(N), adjacent-pair cosine, 25th-percentile threshold) (cluster_semantic.py:171-183, 631-725).
  2. Embedding failure / count mismatch -> _fallback_chunking, greedy token-based, no semantics (:206-207, 607-629; detected in _generate_embeddings_for_segments:293-317).
  3. DP no-solution (dp[n] == -inf) -> _greedy_fallback_chunking (:436-442, 516-541).
- Strategy versioning: DEFAULT_STRATEGY_VERSION = cluster-semantic@1 (strategies.py:13); registry _STRATEGIES with params max=400/min=50/initial=50 (:15-21). Unknown version -> HTTP 400 (app.py:38-39). Golden parity: tests/golden/cluster_semantic@1.json, tests/test_parity.py.
- Orchestrator caller (ChunkerClient, clients/chunker_client.py): calls /chunk ONE page at a time in a loop (:19-41), source_type=WEB_MARKDOWN, metadata {source_url, title}. Any non-200 or exception -> ChunkerUnavailable (:28-45). 60s timeout.

---

## 7. State and cache

- No cache is implemented. CACHE_BACKEND (default "memory") and REDIS_URL exist only as a Settings field (settings.py:45,68) and in .env.example:22-23. There is NO cache class, NO injection point, NO SearXNG/crawl caching anywhere in orchestrator/. The doc-described cache (01-architecture.md section 5) is absent.
- Everything is per-call. run_search builds a fresh SearchStats() per call (pipeline.py:56). The only process-global state is the lazily-built singleton deps/settings in app.py and the per-process _embedder in the chunker. No persistent corpus / vector store (invariant upheld).

---

## 8. As-built failure posture

| Dependency | Behavior (code) | Where flagged |
|---|---|---|
| Planner (LLM) | LlmPlanner.plan swallows all errors / non-200 and returns [query] (planner.py:33-42). Identity by default. Degrades silently. | stats.sub_queries (pipeline.py:59) |
| SearXNG | DiscoveryUnavailable (network or non-200, searxng_client.py:22-25) -> run_search raises SearchDependencyUnavailable(searxng, searxng_unavailable) (pipeline.py:65-66) -> REST 503. HARD FAIL. | app.py:40-44 |
| Crawl4AI | Per-URL failures logged and dropped (returns None, kept partial) (crawl4ai_client.py:21-38). Zero pages -> 200 empty + reason=all_crawls_failed (pipeline.py:86-88). DEGRADE / PARTIAL. | stats.urls_crawled_ok, stats.reason |
| Chunker | ChunkerUnavailable -> SearchDependencyUnavailable(chunker, chunker_unavailable) (pipeline.py:93-94) -> 503. HARD FAIL. Zero chunks (not an error) -> 200 empty + no_chunks_after_dedup. | app.py:40-44 |
| Embedding | Handled inside chunker, not orchestrator: EmbeddingFunction raises -> chunker falls back to greedy token chunking and still returns 200 chunks. Orchestrator sees normal chunks. DEGRADE (hidden from orchestrator). | chunker logs only; no orchestrator flag |
| Reranker | RerankerUnavailable (network or non-200, reranker_client.py:24-27) caught in run_search; falls back to discovery order with score 1/(index+1), sets stats.reranked=False, chunks_reranked=0 (pipeline.py:104-107). GRACEFUL DEGRADE, still 200. | stats.reranked |

- reason originates solely in _empty_response / run_search early returns (pipeline.py:47-49,72,82,88,98).
- reranked flag set at pipeline.py:102 (True) / :106 (False).
- stats (SearchStats, models.py:26-36) is populated incrementally through run_search.
- The MCP surface DISCARDS stats/reason/reranked - returns only passages+citations (mcp_server.py:31-34) - so MCP callers cannot observe degradation.

---

## 9. Divergences from docs/foundational design/

| # | Doc | Code reality |
|---|---|---|
| D1 | 01 section 5 + section 2 stage 8: optional cache (in-proc/Redis, TTL on SearXNG + crawl) and Pre-filter cosine stage | NEITHER exists. CACHE_BACKEND/REDIS_URL are dead settings; no pre-filter stage in run_search. |
| D2 | CLAUDE.md "design-first, no code yet" | Stale; full implementation + tests exist for both services. |
| D3 | 01 section 2: stage 4 "optional cheap snippet-relevance pass"; stage 3 "re-score using SearXNG aggregate ranking" | SelectionPolicyImpl does only blocklist/allowlist filter + sort; merge_dedup keeps the max existing score, no aggregate re-scoring. |
| D4 | 03-deployment.md:178 reranker RERANKER_HEALTH_PATH; .env.example:13 and docker-compose.yml:22 use /health | settings.py:61 defaults RERANKER_HEALTH_PATH to /healthz (mismatch with compose/env /health). |
| D5 | 01 section 4 / 04: BYO endpoints as generic OpenAI-compatible base URLs | Chunker EmbeddingFunction reduces EMBEDDING_ENDPOINT to http://{host}:{port}, forcing http scheme, requiring an explicit port, and dropping any path (embedding_function.py:21-31,73). Orchestrator clients use full base URLs. |
| D6 | 04 section 1: response always includes full stats | REST returns full stats; MCP web_search returns only passages+citations (mcp_server.py:31-34). |
| D7 | 01 section 7: SearXNG down "Surface a clear error" | Implemented as HTTP 503 {dependency, reason} (a non-200 path distinct from the 200+reason degraded path). Chunker hard-fail likewise 503. |
| D8 | 01 section 3: "POST /chunk per doc or batched" | ChunkerClient is strictly per-page sequential (chunker_client.py:19); no batching. |
| D9 | 02 token counting | Token proxy is word count (app.py _length), not tiktoken/HF; token_budget semantics are word-based end to end. |
| D10 | Bundled compose recipe | bundled-models profile uses TEI for embedding+reranker; a second docker-compose.llamacpp.yml (profile llamacpp-models) wires llama.cpp where the reranker path is /reranking vs the default /rerank. Two parallel BYO deployment recipes exist. |
