# Repository Guidelines

## Project Structure & Module Organization

Thorondor is now an implemented Python/FastAPI project, not a design-only repo.

- `orchestrator/` - REST `/v1/search`, compatibility `/search`, MCP `web_search`, pipeline stages, clients, fakes, and tests.
- `semantic-chunking-service/` - standalone `/chunk` FastAPI service, cluster-semantic chunker, embedding client, and tests.
- `searxng/` - SearXNG config mounted by Compose.
- `scripts/` - deployment and smoke helpers for PowerShell and Bash.
- `docs/foundational design/` - architecture, deployment, API, and licensing docs kept in sync with the implementation.
- `docs/plans/` and `docs/reviews/` - remediation plans and historical review artifacts.

## Build, Test, and Development Commands

Run from the repository root:

- `python -m pytest semantic-chunking-service\tests orchestrator\tests -v` - full offline test suite.
- `docker compose --env-file .env -f docker-compose.yml --profile bundled-models config` - validate the bundled-models Compose stack.
- `docker compose --env-file .env --env-file .env.llamacpp -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config` - validate the local llama.cpp stack.
- `.\scripts\deploy-llamacpp.ps1` - deploy the self-contained local stack using GGUF models under `models\`.
- `.\scripts\smoke.ps1 -ComposeFiles @("docker-compose.yml","docker-compose.llamacpp.yml") -EnvFiles @(".env",".env.llamacpp") -Profile llamacpp-models -Investigation` - run live search smoke checks.

## Coding Style & Naming Conventions

Use Python 3.12 style, 4-space indentation, `snake_case` functions/modules,
`PascalCase` classes, and typed Pydantic request/response models. Keep pipeline
dependencies behind small `Protocol` interfaces so discovery, extraction,
chunking, reranking, and assembly stay testable. Match existing module style and
avoid speculative abstractions.

For docs, preserve the numbered foundational-doc pattern and concise Markdown
headings. Review docs under `docs/reviews/` are historical snapshots; do not
rewrite their findings unless the user explicitly asks to revise the artifact.

## Testing Guidelines

Use focused `pytest` coverage beside the relevant service. Cover pure policy
logic with unit tests, HTTP contracts with FastAPI test clients, and external
services with deterministic fakes. Keep degraded cases covered: discovery
failure, partial crawl failures, embedding fallback, reranker degradation,
strict settings failures, and REST/MCP contract parity.

## Commit & Pull Request Guidelines

Use short, imperative subjects such as `Harden settings validation` or
`Document llama.cpp deployment`. Pull requests should summarize scope, list
commands run, call out unimplemented assumptions, and note license or
configuration impact. Do not commit, amend, or push unless the user explicitly
asks. Never add AI tools as authors or co-authors.

## Security & Configuration Tips

Compose and Python settings fail early when required values are missing. Keep
every key from `.env.example` present in `.env`; leave optional API keys and
filters blank only when intentionally disabled. Keep SearXNG unmodified as an
opaque AGPL dependency. Crawl4AI is consumed as the public upstream Docker image
over HTTP. Verify model-server and model-weight licenses before bundling, and
never commit secrets.
