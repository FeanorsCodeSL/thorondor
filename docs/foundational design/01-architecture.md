# 01 — Architecture

> **Thorondor** — Semantic Web Search Pipeline.

This document describes the components, the pipeline stages, the data flow
between them, and the bring-your-own (BYO) seams that let an operator swap
embedding, reranking, and LLM backends.

---

## 1. System shape

The system is a small set of containers behind a single orchestrator. The
orchestrator is the only public surface; everything else is reachable only on
the internal compose network.

```
                         ┌─────────────────────────────────────────────┐
   AI agent  ──REST──▶    │              Orchestrator (FastAPI)          │
   AI agent  ──MCP───▶    │   POST /search   ·   MCP tool: web_search    │
                         └───┬───────┬───────┬───────┬──────────┬───────┘
                             │       │       │       │          │
              (optional)     ▼       ▼       ▼       ▼          ▼
              LLM endpoint  SearXNG  Crawl4AI  Chunking   Reranker endpoint
              (BYO chat)   (bundled) (bundled)  Service   (BYO / bundled)
                                                  │
                                                  ▼
                                          Embedding endpoint
                                          (BYO / bundled)
```

- **Orchestrator** — first-party FastAPI app. Owns the pipeline state machine,
  the REST API, and the MCP server. Stateless per request except an optional
  short-TTL cache.
- **Semantic Chunking Service** — first-party FastAPI app. Stateless,
  CPU-bound. Converts text/markdown into coherent passages. Calls the embedding
  endpoint. Documented in full in [`02-semantic-chunking-service.md`](02-semantic-chunking-service.md).
- **SearXNG** — bundled, unmodified, opaque. Metasearch over many engines.
- **Crawl4AI** — bundled. Headless fetch + render + boilerplate strip →
  markdown.
- **Embedding / Reranker / LLM endpoints** — external (BYO) OpenAI-compatible
  HTTP services, or the optional bundled defaults.

Why this split: each unit has one purpose, a stable HTTP contract, and can be
understood, tested, scaled, and swapped independently. The chunker in
particular is a separate service precisely so it can be reused outside this
pipeline (any consumer that needs coherent passages can call `/chunk`).

---

## 2. Pipeline stages

The orchestrator runs the query through an ordered pipeline. Each stage is
behind an interface (a Python `Protocol`) so it can be faked in tests and
replaced in production. Stages marked *(optional)* are off by default and gated
by config.

| # | Stage | Component | Purpose |
|---|---|---|---|
| 1 | **Plan** *(optional)* | Query Planner | Decompose multi-hop intents and lightly expand the query into 1–N search-friendly sub-queries. Identity (passthrough) by default. |
| 2 | **Discover** | SearXNG client | Issue sub-queries in parallel; collect `{title, url, snippet, engine, score}`. No page content yet. |
| 3 | **Merge / dedup** | Orchestrator (pure) | Union sub-query result sets, dedup by normalized URL, re-score using SearXNG aggregate ranking. |
| 4 | **Select / budget** | Selection policy (pure) | Cap to top-N URLs, drop blocklisted / non-crawlable domains, optional cheap snippet-relevance pass. **This protects the expensive crawl stage.** |
| 5 | **Extract** | Crawl4AI client | Fetch + render + strip → clean markdown. Bounded concurrency, per-URL timeout, failures non-fatal (partial results). |
| 6 | **Content dedup** | Orchestrator (pure) | Drop near-duplicate bodies (syndicated articles under different URLs). |
| 7 | **Chunk** | Chunking Service | Each markdown doc → coherent passages carrying `{source_url, title, position}` provenance. |
| 8 | **Pre-filter** *(optional)* | Orchestrator | If chunk count is large, a cheap in-memory cosine pass narrows to ~50–100 candidates before the reranker. |
| 9 | **Rerank** | Reranker client | Score each surviving passage against the **original user query** (not the sub-queries). |
| 10 | **Assemble** | Result assembler (pure) | Select top passages by **token budget**, not a fixed count; attach citations from provenance. |
| 11 | **Return** | Orchestrator | Hand the assembled, cited passages back to the caller. |

### Stage notes that matter

- **Stage 1 is optional and BYO.** Many RAG systems reuse an in-house LLM
  query-helper for decomposition. Here, the planner defaults to an identity
  transform so the service needs no LLM at all. Set `LLM_ENDPOINT` to enable
  decomposition/expansion against any OpenAI-compatible chat endpoint.
- **Stage 4 is the single most important production control.** Crawling is the
  expensive stage. Never crawl the full deduped list. Start with a top-N of
  5–8.
- **Stage 5 must be fault-tolerant.** A meaningful fraction of fetches fail
  every run (timeouts, bot walls, JS-only pages). The pipeline proceeds on
  partial results and never lets one slow target stall the call.
- **Stage 9 scores against the original query.** Sub-queries are a discovery
  device; relevance is judged against what the user/agent actually asked.
- **Stage 10 budgets tokens, not count.** The agent has a finite context
  window; assemble to fill it with the strongest evidence.

---

## 3. Sequence (single call)

```
agent → orchestrator:   POST /search {query, token_budget, max_urls, ...}

orchestrator:
  plan(query)                      → [q1, q2, ...]        (or [query])
  for q in sub-queries (parallel): searxng.search(q)      → results
  merged   = merge_dedup(results)
  selected = selection_policy(merged, max_urls, blocklist)
  pages    = crawl4ai.extract(selected_urls)              (bounded, partial-ok)
  pages    = content_dedup(pages)
  chunks   = chunking.chunk(pages)                         (POST /chunk per doc or batched)
  cands    = prefilter(chunks, query)                      (optional)
  scored   = reranker.rerank(query, cands)
  passages = assemble(scored, token_budget)                (+ citations)

orchestrator → agent:   200 {passages[], citations[], stats}
```

In an agentic loop the agent may call `/search` again for the next hop. The
pipeline is **stateless per call**; the only shared state is an optional cache
(§5).

---

## 4. Bring-your-own seams

Every external model dependency is an HTTP endpoint configured by environment
variable. None are hard-coded. This is what makes the project reusable.

| Seam | Env var | Contract | Bundled default available? |
|---|---|---|---|
| Embedding | `EMBEDDING_ENDPOINT`, `EMBEDDING_MODEL` | OpenAI `/v1/embeddings` | Yes (compose `bundled-models` profile) |
| Reranker | `RERANKER_ENDPOINT`, `RERANKER_MODEL` | `/rerank` (query + documents → scores) | Yes (compose `bundled-models` profile) |
| LLM (query planner) | `LLM_ENDPOINT`, `LLM_MODEL` | OpenAI `/v1/chat/completions` | No (optional; identity planner if unset) |
| Discovery | `SEARXNG_URL` | SearXNG JSON API | Yes (bundled SearXNG) |
| Extraction | `CRAWL4AI_URL` | Crawl4AI HTTP API | Yes (bundled Crawl4AI) |

**Contract over implementation.** The chunker does not care whether
`EMBEDDING_ENDPOINT` is a `vLLM`, `text-embeddings-inference` (TEI), `Infinity`,
or `Ollama` server — only that it speaks `/v1/embeddings`. Likewise the reranker
seam expects a small, well-defined `/rerank` contract (documented in
[`03-deployment.md`](03-deployment.md)); an adapter shim is provided for servers
that expose a different rerank shape.

---

## 5. State, caching, and the agentic loop

The system is deliberately near-stateless:

- **Per-call state** lives in the request handler and is discarded on response.
- **Optional cache** (in-process or Redis, configured by `CACHE_BACKEND`):
  - short TTL on SearXNG result sets keyed by sub-query, and
  - longer TTL on crawled markdown keyed by `url + crawl_date`.
  This saves re-crawling overlapping URLs across loop hops and across
  concurrent agents. It is injected into the orchestrator, never owned by it.
- **No persistent corpus.** Crawled content is never written to a vector store.
  This is a hard non-goal — it keeps the project a *search tool*, not a
  knowledge base, and sidesteps the storage/freshness/eviction problems a
  persistent web index would create.

---

## 6. Interfaces (orchestrator internals)

The orchestrator is organized around small, single-purpose seams. In Python
these are `Protocol`s; each has a real implementation and a deterministic fake
for tests.

| Interface | Responsibility | Purity |
|---|---|---|
| `QueryPlanner` | query → sub-queries | impure (optional LLM) |
| `SearchDiscovery` | sub-query → results | impure (SearXNG) |
| `SelectionPolicy` | results → selected URLs | **pure** |
| `ContentExtractor` | URLs → markdown docs | impure (Crawl4AI), partial-tolerant |
| `SemanticChunker` | doc → passages | impure (chunking service) |
| `Reranker` | (query, passages) → scored | impure (reranker) |
| `ResultAssembler` | scored + budget → response | **pure** |

The two pure stages (selection and assembly) hold the policy logic that most
affects quality and cost, and are unit-tested in isolation with no network.

---

## 7. Failure posture (summary)

| Dependency down | Behavior |
|---|---|
| LLM (planner) | Fall back to identity planner; single-query search still works. |
| SearXNG | Hard fail the call (no discovery → no results). Surface a clear error. |
| Crawl4AI | Partial: proceed with whatever fetched; if zero pages, return empty with a reason. |
| Chunking service | Hard fail the call (cannot produce passages). |
| Embedding endpoint | Chunker falls back to greedy token-based chunking (see [`02`](02-semantic-chunking-service.md)); degraded but functional. |
| Reranker | Optional graceful degrade: return pre-filter / discovery order with a `reranked: false` flag. |

Detailed timeouts, retries, and concurrency bounds are in
[`03-deployment.md`](03-deployment.md).
