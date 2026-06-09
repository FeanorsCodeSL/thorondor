# Thorondor — Implementation Plans

These plans turn `docs/foundational design/` into executable, phased work. Each
plan produces working, independently-testable software and follows the workspace
plan format (Goal / References / Build & run / phased Tasks + Verification). All
unit tests run **offline** — external dependencies are faked.

## Build order

1. **[01 — Semantic Chunking Service](01-semantic-chunking-service.md)** — the
   first-party `/chunk` service. Most concrete: doc 02 embeds its source verbatim.
   Build first; the orchestrator depends on its contract.
2. **[02 — Orchestrator](02-orchestrator.md)** — the pipeline state machine, REST
   `/search`, and MCP `web_search`. Greenfield (no verbatim source); the plan pins
   module signatures and data types up front. Testable in isolation via fakes.
3. **[03 — Deployment & Integration](03-deployment-and-integration.md)** — Compose
   with the default core stack plus the optional `bundled-models` profile,
   `.env.example`, SearXNG config, and the live end-to-end smoke. Requires both
   images from 01 and 02.

## Conventions shared across plans

- **Python 3.12 / FastAPI**, typed models, stages behind `Protocol` interfaces.
- **TDD:** write the failing test, implement minimally, verify, and leave changes
  reviewable. Commit only when explicitly asked.
- **Verbatim source is referenced, not re-pasted** — doc 02 §4–§8 is authoritative
  for every `chunking/*.py` file; reproduce it logic-for-logic.
- **The chunker's golden-file parity must never silently drift** (Plan 01 Phase 4).
- **Failure posture is graded** (doc 01 §7): mandatory dependency unavailable
  (SearXNG/chunker transport or non-200) → explicit hard failure; empty discovery,
  zero crawls, or zero chunks → 200-shaped empty response with a `reason`; crawl
  partial → proceed; embedding down → token fallback; reranker down →
  `reranked: false`.
- **Commits:** the user is sole author; no AI co-authors; commit only when asked.
