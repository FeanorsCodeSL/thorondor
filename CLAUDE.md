# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**`thorondor`** is a **self-hosted, agent-ready semantic web search service** — a
sovereign, open-source alternative to hosted search-for-agents APIs (Exa, Tavily)
that runs entirely on the operator's own hardware with no mandatory external SaaS.
Give it a query; it returns a small, **reranked, citation-bearing, token-budgeted**
set of passages an AI agent can reason over. Part of the Fëanor's Code family of
projects (Tolkien naming; Thorondor is the King of the Eagles).

> **Name.** The project is **Thorondor** (slug `thorondor`); "Semantic Web Search
> Pipeline" is its descriptive subtitle. The slug is the package name, the Compose
> project (`name:`), the MCP server id (`FastMCP("thorondor")`), and the container
> image-tag stem. Early drafts used the placeholder `semantic-websearch`; the design
> docs have been reconciled to `thorondor`.

## Status: design-first, no code yet

This repo currently contains **only documentation** — there is no build system, test
runner, package manifest, Compose file, or first-party source. Nothing is committed
to git yet.

- **The design is canonical; the commands are not.** The design docs specify future
  runtime commands (`docker compose --profile bundled-models up`, `curl localhost:8080/search …`).
  **Do not present any of these as verified until the corresponding file exists.**
- When implementation begins, match the documented layout exactly (see "Planned
  layout" below) so the design docs stay accurate.

### Where the authority lives

| Source | What it governs |
|---|---|
| `docs/foundational design/01-architecture.md` | Components, the 11-stage pipeline, data flow, BYO seams, failure posture |
| `docs/foundational design/02-semantic-chunking-service.md` | The chunker — full algorithm **and embedded verbatim source**, `/chunk` contract, fallbacks |
| `docs/foundational design/03-deployment.md` | Dockerfiles, Compose + profiles, full env reference, healthchecks, sizing |
| `docs/foundational design/04-api-and-agent-integration.md` | REST `/search` schema, MCP `web_search` tool, end-to-end agent example |
| `docs/foundational design/05-licensing-and-sovereignty.md` | Per-component licensing, the SearXNG AGPL boundary, crawl posture |
| `AGENTS.md` | Contributor mechanics: coding style, testing approach, commit/PR conventions, security tips |

`AGENTS.md` is the authority on **how to contribute** (Python 3.12 / FastAPI style,
`pytest` beside each service, imperative commit subjects). This file is the authority
on **what the system is and how its parts fit** — don't duplicate AGENTS.md here.

## Architecture (the big picture)

A small set of containers behind a single **orchestrator**, which is the only public
surface. Everything else is reachable only on the internal Compose network.

**The pipeline** (orchestrator runs a query through an ordered, fault-tolerant state
machine; each stage sits behind a Python `Protocol` so it can be faked in tests and
swapped in production):

```
query
  → plan (optional, BYO LLM)   decompose/expand → sub-queries (identity by default)
  → discover (SearXNG)         sub-queries → candidate URLs
  → merge/dedup (pure)         union + dedup by normalized URL
  → select/budget (pure)       cap to top-N, drop blocklisted   ← protects the crawl
  → extract (Crawl4AI)         URL → clean markdown (bounded, partial-tolerant)
  → content dedup (pure)       drop near-duplicate bodies
  → chunk (Chunking Service)   markdown → coherent passages      ← the core service
  → pre-filter (optional)      cheap cosine narrowing
  → rerank (BYO)               score passages vs the ORIGINAL query
  → assemble (pure)            top passages by TOKEN BUDGET, attach citations
  → return                     passages + citations + provenance
```

**Two first-party services (both FastAPI, both stateless, both scale horizontally):**

- **Orchestrator** — owns the pipeline state machine, the REST API (`POST /search`),
  and the MCP server (`web_search` tool). Both surfaces call one shared
  `run_search(...)` pipeline function; the surfaces are thin. Published on `:8080`.
- **Semantic Chunking Service** — converts text/markdown into globally-coherent
  passages via the **ClusterSemanticChunker** (Chroma Research, July 2024): a dynamic
  program that maximizes within-chunk semantic coherence subject to a token-size
  constraint, instead of fixed-size windows. CPU-bound; deliberately reusable
  standalone via `POST /chunk`. **It does not load an embedding model** — it calls the
  BYO embedding endpoint, which keeps it light and stateless.

**Bundled opaque dependencies (run unmodified, never forked):**

- **SearXNG** (AGPL-3.0) — discovery (query → URLs).
- **Crawl4AI** (Apache-2.0) — extraction (URL → markdown).

**Bring-your-own (BYO) seams — every model dependency is an HTTP endpoint set by env
var, never hard-coded:** embedding (`/v1/embeddings`), reranker (`/rerank`), and the
optional LLM query planner (`/v1/chat/completions`). Optional bundled defaults exist
behind the `bundled-models` Compose profile so a fresh stack runs with zero external
dependencies. **Contract over implementation:** code targets the wire contract, not a
specific server (vLLM / TEI / Infinity / Ollama are interchangeable).

## Invariants worth preserving

These are load-bearing design decisions — don't quietly break them.

- **No persistent web index — a hard non-goal.** Crawled content is chunked,
  reranked, returned, and discarded per call. The only state is an optional
  short-TTL cache (`CACHE_BACKEND`). This keeps it a *search tool*, not a knowledge
  base, and minimizes data-retention surface. Don't add a vector store to the hot path.
- **Stateless per call.** No server-side session. Any cache is an optimization, never
  part of the contract — the agent owns the loop.
- **The tool returns evidence, not prose.** No summarization, no hidden second LLM
  hop. Strongest passages + citations; the agent reasons. Citations are first-class.
- **Budget, not count.** `token_budget` is the primary assembly control, not `top_k`.
- **Rerank scores the ORIGINAL user query**, not the discovery sub-queries.
- **The two pure stages (selection, assembly) hold the cost/quality policy** and are
  unit-tested in isolation with no network.
- **Failure posture is graded, not all-or-nothing:** SearXNG down or chunker down →
  hard-fail with a clear reason; Crawl4AI partial failures → proceed on whatever
  fetched; embedding down → chunker falls back to greedy token chunking; reranker down
  → return discovery/pre-filter order with `reranked: false`. Degraded calls still
  return `200` with a `reason`, never an opaque error.
- **Chunker behavior must not drift.** It carries three safety valves
  (`CHUNKER_MAX_SEGMENTS_DP` OOM guard → greedy-semantic O(N) path; embedding-failure
  → token-based fallback; DP-no-solution → greedy grouping) and `strategy_version`
  pinning. When extracting/porting it, prove parity with a **golden-file test driven
  by a deterministic stub embedding function** before changing anything else.
- **SearXNG stays a black box** you pull and configure (via its own `settings.yml`),
  never a codebase you patch — this is the AGPL-3.0 network-copyleft boundary. See
  `05-licensing-and-sovereignty.md` before publishing or distributing.

## Planned layout (when code lands)

Keep first-party services aligned with the documented structure:

```
orchestrator/                 # FastAPI: pipeline, REST /search, MCP web_search
semantic-chunking-service/
  chunking/                   # base, recursive_splitter, cluster_semantic,
                              # embedding_function, settings, strategies, textprep,
                              # models, app   (source embedded verbatim in doc 02)
  tests/                      # test_parity.py — golden-file parity vs reference impl
docker-compose.yaml           # profiles: core (default) | bundled-models
.env.example                  # all env vars with safe placeholders
```

Each service is its own container with a service-local `tests/`, `requirements.txt`,
and `Dockerfile`.

## Commands

**None are runnable yet.** Until code lands, only inspection commands work:

```powershell
rg --files docs        # list design documents
git status --short     # review local changes before committing
```

**Planned (per the design docs — do NOT claim these work until the files exist):**

```bash
docker compose --profile bundled-models up   # zero-dependency local run
cp .env.example .env && docker compose up     # core profile (BYO embedding+reranker)
curl -s localhost:8080/search -H 'content-type: application/json' \
  -d '{"query":"...","token_budget":4000}' | jq
pytest                                         # focused tests beside each service
```

## Project conventions

- **First-party services are Python 3.12 / FastAPI**, typed request/response models,
  pipeline dependencies behind small `Protocol` interfaces. Full style rules:
  `AGENTS.md`.
- **No established commit convention yet** (no history) — use short imperative
  subjects (`Add chunker contract tests`). Per the workspace: the user is always the
  sole commit author; never add AI tools as authors or co-authors; commit only when
  explicitly asked.
- **Credit Chroma Research** for the ClusterSemanticChunker algorithm in README/code
  headers; do not represent it as novel to this project.
- **Secrets are operator configuration, not source:** BYO endpoint URLs, API keys, and
  crawl policy live in `.env`; commit only `.env.example` placeholders.

## Relationship to the workspace

`thorondor` is an **independent** sibling under `C:\git\FEANORS-CODE` — like
`Mirrormere` and `amon-hen`, it is **not part of the TENGWAR ecosystem** and does
**not** inherit the workspace's Spanish-first or gold-brand conventions. It is an
English-first, generic developer tool. It is registered in the root `CLAUDE.md`
router. If it later warrants quality-gate coverage, register it in the shared
SonarQube scanner scripts under `sonarQube/` per the rules in the root `CLAUDE.md`.
