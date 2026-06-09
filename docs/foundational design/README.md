# Thorondor — Semantic Web Search Pipeline

> **Name.** The project is **Thorondor** (slug `thorondor`). The slug is used as
> the package name, the Compose project (`name:`), the MCP server id, and the
> container image-tag stem. (Earlier drafts used the placeholder `semantic-websearch`.)

A **self-hosted, agent-ready web search service**. Give it a query; it returns a
small, **reranked, citation-bearing, token-budgeted** set of passages that any
AI agent can reason over. It is a sovereign, open-source alternative to hosted
search-for-agents APIs (Exa, Tavily, and similar) that you run entirely on your
own hardware with no mandatory external SaaS dependency.

The pipeline:

```
query
  → (optional) query planner        decompose / expand
  → discovery (SearXNG)             query → candidate URLs
  → select / budget                 cost gate before crawling
  → extraction (Crawl4AI)           URL → clean markdown
  → content dedup
  → semantic chunking               markdown → coherent passages   ← the core service
  → (optional) pre-filter           cheap cosine narrowing
  → rerank                          score passages vs the query
  → token-budget assembly           top-K to fit the agent's context
  → comprehensive response          passages + citations + provenance
```

Two of the stages are upstream containers run as opaque dependencies
(**SearXNG** for discovery, **Crawl4AI** for extraction). One is a first-party
service this project extracts and ships: the **semantic chunking service**,
which converts crawled markdown into globally-coherent passages using a dynamic
programming chunker. Embedding and reranking are reached through
**bring-your-own** endpoints, with optional bundled defaults so a fresh
`docker compose up` works out of the box.

## Why this exists

Autonomous agents need to read the live web, not just a frozen training corpus.
Hosted "search for agents" products solve this but send every query and every
agent's reasoning context to a third party and bill per call. For
privacy-sensitive, on-premises, or cost-controlled deployments that is a
non-starter. This project assembles freely-licensed components into the same
capability, kept inside your own network.

The design goal is **retrieval quality parity with a hosted API** while staying
self-hosted:

- **Discovery breadth** comes from SearXNG aggregating many engines.
- **Clean extraction** comes from Crawl4AI's headless render + boilerplate
  stripping, so chunks are prose, not navigation chrome.
- **Coherent passages** come from a globally-optimal semantic chunker rather
  than fixed-size windows — chunks respect topic boundaries.
- **Precision** comes from a cross-encoder reranker scoring every passage
  against the *original* user query.
- **Budget discipline** means the agent gets the strongest evidence that fits
  its context window, not an arbitrary fixed count.

## Components

| Component | Ships as | Role | License |
|---|---|---|---|
| **Orchestrator** | First-party (FastAPI) | Coordinates the pipeline; exposes REST `/search` + an MCP `web_search` tool | First-party |
| **Semantic Chunking Service** | First-party (FastAPI) | Markdown/text → coherent passages (cluster-semantic DP chunker) | First-party |
| **SearXNG** | Bundled container | Discovery (query → URLs) | AGPL-3.0 — run unmodified, opaque |
| **Crawl4AI** | Public upstream Docker image | Extraction (URL → markdown) | Apache-2.0 |
| **Embedding endpoint** | Bring-your-own (optional bundled default) | Segment embeddings for the chunker + optional pre-filter | depends on model server |
| **Reranker endpoint** | Bring-your-own (optional bundled default) | Relevance scoring of passages | depends on model server |
| **LLM endpoint** (optional) | Bring-your-own | Query decomposition / expansion | depends on model server |

## Quickstart

> Full instructions live in [`03-deployment.md`](03-deployment.md). The short
> version:

```bash
# 1. Zero-dependency local run: starts SearXNG, Crawl4AI, a default
#    embedding server and a default reranker server.
docker compose --profile bundled-models up

# 2. Production run: bring your own embedding + reranker endpoints,
#    start only SearXNG + Crawl4AI + the first-party services.
cp .env.example .env        # set EMBEDDING_ENDPOINT, RERANKER_ENDPOINT, ...
docker compose up

# 3. Query it
curl -s localhost:8080/search \
  -H 'content-type: application/json' \
  -d '{"query": "what changed in the EU AI Act timeline in 2025", "token_budget": 4000}' | jq
```

An MCP-capable agent points at the orchestrator's MCP endpoint and gets a
`web_search` tool with no extra glue. See
[`04-api-and-agent-integration.md`](04-api-and-agent-integration.md).

## Documentation

| Doc | Contents |
|---|---|
| [`01-architecture.md`](01-architecture.md) | Components, pipeline stages, data flow, the bring-your-own seams |
| [`02-semantic-chunking-service.md`](02-semantic-chunking-service.md) | The extracted cluster-semantic chunker: full algorithm, **embedded source**, `/chunk` contract, config, fallbacks |
| [`03-deployment.md`](03-deployment.md) | Dockerfiles, `docker compose` with profiles, full env reference, healthchecks, resource sizing |
| [`04-api-and-agent-integration.md`](04-api-and-agent-integration.md) | REST `/search` schema, MCP tool definition, end-to-end agent example |
| [`05-licensing-and-sovereignty.md`](05-licensing-and-sovereignty.md) | Per-component licensing, the SearXNG AGPL boundary, self-hosting posture |

## Non-goals

- **No persistent web index.** The crawled corpus is ephemeral — chunked,
  reranked, and discarded per call. There is no vector database in the hot path
  (an optional short-TTL cache is the only state). If you want a persistent
  knowledge base, that is a different system.
- **Not a crawler framework.** Crawl4AI is a public upstream containerized
  service; this project orchestrates it over HTTP.
- **Not a model server.** Embedding and reranking are external endpoints
  (yours, or the optional bundled defaults).

## License

First-party code (orchestrator, chunking service) is intended to be released
under a permissive license of your choosing. Note the **SearXNG AGPL-3.0
boundary**: run it unmodified as an opaque container dependency. See
[`05-licensing-and-sovereignty.md`](05-licensing-and-sovereignty.md) before
publishing or distributing.
