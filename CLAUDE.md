# CLAUDE.md

This file gives Claude Code repo-specific context for `thorondor`.

## What This Is

Thorondor is a self-hosted, agent-ready semantic live-web search service. It
discovers URLs with SearXNG, crawls selected pages through the public upstream
Crawl4AI Docker API, chunks markdown with a first-party semantic chunking
service, reranks passages, and returns citation-bearing evidence through REST
and MCP.

The project slug is `thorondor`; it is used by Compose (`name:`), the MCP server
id (`MCPServer("thorondor")`), and local agent skill naming.

## Current Status

This repo contains implemented services, tests, Dockerfiles, Compose stacks,
deployment scripts, and documentation. Earlier design-only guidance is obsolete.

Primary implementation areas:

- `orchestrator/` - FastAPI app, `/v1/search`, compatibility `/search`, MCP
  `web_search`, pipeline, clients, fakes, and tests.
- `semantic-chunking-service/` - standalone `/chunk` service and cluster-semantic
  chunker.
- `docker-compose.yml` - core stack plus `bundled-models` profile.
- `docker-compose.llamacpp.yml` - local llama.cpp embedding and reranker profile.
- `.agents/skills/thorondor-web-search/SKILL.md` - repo-local skill for calling
  the running service.

## Architecture

Pipeline:

```text
query
  -> optional planner
  -> discovery (SearXNG)
  -> merge and URL selection
  -> extraction (Crawl4AI)
  -> markdown cleaning and content dedup
  -> semantic chunking
  -> deterministic prefilter when chunk count is broad
  -> batched reranking
  -> token-budget assembly
  -> passages, citations, stats, optional raw markdown
```

REST `POST /v1/search` and MCP `web_search` are thin surfaces over the same
`run_search` pipeline and return the versioned `SearchResponse` envelope.
`POST /search` remains as a compatibility alias.

## Commands

Run from the repo root:

```powershell
python -m pytest semantic-chunking-service\tests orchestrator\tests -v
docker compose --env-file .env -f docker-compose.yml --profile bundled-models config
docker compose --env-file .env --env-file .env.llamacpp -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config
.\scripts\deploy-llamacpp.ps1
.\scripts\smoke.ps1 -ComposeFiles @("docker-compose.yml","docker-compose.llamacpp.yml") -EnvFiles @(".env",".env.llamacpp") -Profile llamacpp-models -Investigation
```

The llama.cpp path expects `.env`, `.env.llamacpp`, and GGUF model files under
`models\` matching `.env.llamacpp`.

## Invariants

- No persistent web index or vector store in the hot path.
- The tool returns evidence, not prose.
- Citations and passage provenance are first-class.
- Reranking scores passages against the original user query.
- `token_budget` controls assembly; `max_passages` is only a cap after ranking.
- SearXNG stays an unmodified opaque AGPL dependency.
- Crawl4AI stays a public upstream containerized service consumed over HTTP.
- Model dependencies are operator-configured HTTP endpoints.
- All required configuration keys must be present; optional seams are blank but
  explicit in `.env`.
- Commit only when the user explicitly asks, and never add AI co-authors.

## Documentation Authority

- `README.md` - operator quickstart and common usage.
- `docs/foundational design/01-architecture.md` - components and pipeline.
- `docs/foundational design/03-deployment.md` - Compose, env, health, sizing.
- `docs/foundational design/04-api-and-agent-integration.md` - REST/MCP contract.
- `docs/foundational design/05-licensing-and-sovereignty.md` - license and
  sovereignty posture.
- `AGENTS.md` - contribution and tooling rules.

Review files under `docs/reviews/` are historical audit artifacts. Treat them as
snapshots unless the user asks to rewrite or supersede them.
