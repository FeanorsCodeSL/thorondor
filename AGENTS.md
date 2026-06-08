# Repository Guidelines

## Project Structure & Module Organization

This repository is currently design-first. The project content lives under `docs/foundational design/`:

- `README.md` - project overview, pipeline, quickstart intent, and non-goals.
- `01-architecture.md` - container topology, pipeline stages, interfaces, and failure posture.
- `02-semantic-chunking-service.md` - planned chunking service source layout, contracts, and algorithm notes.
- `03-deployment.md` - intended Docker Compose topology, environment variables, healthchecks, and sizing.
- `04-api-and-agent-integration.md` - REST `/search`, `/healthz`, and MCP `web_search` contracts.
- `05-licensing-and-sovereignty.md` - license boundaries and operational/legal guidance.

When implementation starts, keep first-party services aligned with the documented layout: `orchestrator/`, `semantic-chunking-service/`, service-local `tests/`, `docker-compose.yaml`, and `.env.example`.

## Build, Test, and Development Commands

No build system, test runner, package manifest, or Compose file exists yet. Until code lands, use inspection commands only:

- `rg --files docs` - list design documents.
- `git status --short` - review local changes before submitting.

The design docs specify future runtime commands such as `docker compose --profile bundled-models up`, `docker compose up`, and `curl localhost:8080/search ...`. Do not present those as verified until the corresponding files exist.

## Coding Style & Naming Conventions

Planned first-party services are Python/FastAPI. Use Python 3.12 style, 4-space indentation, `snake_case` functions/modules, `PascalCase` classes, and typed request/response models. Keep pipeline dependencies behind small `Protocol` interfaces so discovery, extraction, chunking, reranking, and assembly remain testable. Prefer documented environment names such as `EMBEDDING_ENDPOINT`, `RERANKER_ENDPOINT`, and `MAX_URLS`.

For docs, preserve the numbered design-doc pattern and concise Markdown headings.

## Testing Guidelines

No automated tests are present. When adding code, add focused `pytest` tests beside each service. Cover pure policy logic with unit tests, HTTP contracts with FastAPI test clients, and external services with deterministic fakes. Include degraded cases: discovery failure, partial crawl failures, embedding fallback, and reranker degradation.

## Commit & Pull Request Guidelines

This branch has no existing commit history, so there is no established commit convention. Use short, imperative subjects such as `Add chunker contract tests` or `Document SearXNG config`.

Pull requests should summarize scope, list commands run, call out unimplemented assumptions, and note any license or configuration impact. Never commit secrets; use `.env.example` for placeholders.

## Security & Configuration Tips

Keep SearXNG unmodified as an opaque AGPL dependency unless legal obligations are understood. Verify model-server and model-weight licenses before bundling. Treat BYO endpoint URLs, API keys, and crawl policy settings as operator configuration, not source-controlled secrets.
