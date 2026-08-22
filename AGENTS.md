# Repository Guidelines

## Project Structure & Module Organization

Thorondor is an implemented Python/FastAPI project with live search, known-URL
fetch, site mapping, bounded crawling, optional page caching, durable crawl
jobs, and bounded structured extraction.

The project slug is `thorondor`; it is used by Compose, the MCP server id, and
the repo-local agent skill.

- `orchestrator/` - REST `/v1/search`, `/v1/fetch`, `/v1/map`, `/v1/crawl`, the
  REST-only crawl-job routes, MCP tools, pipeline stages, cache/job stores,
  structured extraction, clients, fakes, and tests.
- `semantic-chunking-service/` - standalone `/chunk` FastAPI service, cluster-semantic chunker, embedding client, and tests.
- `searxng/` - SearXNG config mounted by Compose.
- `scripts/` - deployment and smoke helpers for PowerShell and Bash.
- `docs/architecture/` - current architecture, pipeline, deployment, security,
  configuration, dependency, and contract documentation.
- `docs/plans/` - active implementation plans when work is in progress; completed plans are removed after implementation.
- `docs/reviews/` - historical review snapshots; do not rewrite them casually.

## Service Capabilities and Boundaries

- `POST /v1/search` and MCP `web_search` perform live discovery and semantic
  retrieval. Ordinary search is non-persistent.
- `POST /v1/fetch` and MCP `web_fetch` fetch known URLs with typed outcomes,
  final-URL identity, optional cache/change metadata, target watches, and
  explicit structured profiles for `links`, `tables`, `json_ld`, and bounded
  `json_schema` extraction.
- `POST /v1/map` and MCP `web_map` combine robots-aware sitemap discovery with
  bounded same-origin traversal.
- `POST /v1/crawl` and MCP `web_crawl` return bounded typed page results. The
  REST-only `/v1/crawl/jobs` family provides opt-in durable polling,
  pagination, cancellation, scoped idempotency, and restart recovery.
- The native stdio proxy forwards all four MCP tools to the same versioned REST
  contracts.

Structured extraction is always explicit. Deterministic profiles are bounded
and source-addressed. JSON Schema extraction uses only the configured LLM
operation seam, fixed system instructions, local validation, exact evidence
checks, and all-or-nothing model output. Missing LLM configuration disables
only `json_schema` extraction. Structured requests bypass the page cache and
cannot combine structured output with watch, change, diff, stale-read, or
revalidation semantics.

Crawl4AI 0.9.2 is attached to an internal control network shared only with the
orchestrator and a dedicated outbound network. Its built-in connect-time
DNS-pinning proxy owns target-site routing. The retained first-party
`ssrf-proxy` remains a deployment-compatibility service but is not in the
Crawl4AI target-site path. The orchestrator independently checks target URLs
and changed final URLs.

The optional page cache and crawl-job store use standard-library SQLite in the
`/var/lib/thorondor` volume. Cache persistence is disabled by default. Crawl
jobs are REST-only, single-process, and single-replica. Thorondor does not own
schedules, notifications, webhooks, browser actions, or autonomous follow-up
actions; Tengwar or another agent runtime owns those workflows.

## Build, Test, and Development Commands

Run from the repository root:

- `python -m pytest semantic-chunking-service/tests orchestrator/tests -v` - full service/orchestrator suite.
- `python -m pytest thorondor_cli/tests -q` - CLI suite.
- `PYTHON=.venv/bin/python bash scripts/check-release-guard.sh` - Compose, asset, lock, and egress validation.
- `docker compose --env-file .env.example -f docker-compose.yml --profile bundled-models config` - validate the bundled-models Compose stack.
- `docker compose --env-file .env.example --env-file .env.llamacpp.example -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config` - validate the local llama.cpp stack.
- `scripts/deploy-llamacpp.sh` or `scripts/deploy-llamacpp.ps1` - deploy the self-contained local stack using GGUF models under `models/`.
- `scripts/smoke.sh` or `scripts/smoke.ps1` - run live search smoke checks.
- From the Tengwar checkout, `just thorondor-deploy` - deploy the integrated Thorondor services without removing the shared network or volumes.
- The Windows equivalents use backslash-separated test paths and the
  PowerShell scripts under `scripts/`.
- The llama.cpp path expects `.env`, `.env.llamacpp`, and the required GGUF
  files under `models/`. The base and CLI Compose assets must remain
  synchronized; the release guard checks those copies and the Crawl4AI network
  boundary.

For Tengwar integration, use Tengwar's `just thorondor-deploy` recipe. It
builds or updates only the Thorondor services, keeps shared dependency
services, and must not be replaced with `docker compose down -v` or shared
network removal.

## Coding Style & Naming Conventions

Use Python 3.13 style, 4-space indentation, `snake_case` functions/modules,
`PascalCase` classes, and typed Pydantic request/response models. Keep pipeline
dependencies behind small `Protocol` interfaces so discovery, extraction,
chunking, reranking, and assembly stay testable. Match existing module style and
avoid speculative abstractions.

For docs, preserve concise Markdown headings and keep the architecture documents
in `docs/architecture/` synchronized with the REST/MCP contracts. Review docs
under `docs/reviews/` are historical snapshots; do not rewrite their findings
unless the user explicitly asks to revise the artifact.

## Repository Invariants

- No persistent web index or vector store exists in the hot path.
- Search, map, and crawl are live; only known-URL fetch may use the optional
  page cache.
- The service returns evidence and typed outcomes, not generated prose.
- Page-derived text and structured values are external, untrusted data.
- Evidence IDs are tied to the final URL, exact cleaned Markdown bytes, and
  exact Unicode spans.
- JSON Schema model data is accepted only after independent validation and
  exact value-bearing evidence checks.
- SearXNG remains an unmodified opaque AGPL dependency.
- Crawl4AI remains a public upstream containerized service consumed over HTTP.
- Model dependencies are operator-configured HTTP endpoints.
- All required configuration keys must be present; optional seams are blank
  but explicit in `.env`.

## Documentation Authority

- `README.md` — operator quickstart, REST/MCP usage, limits, and environment
  reference.
- `docs/architecture/overview.md` — components, data flow, and boundaries.
- `docs/architecture/pipeline-workflow.md` — fetch, cache, watch, crawl-job,
  and structured-extraction execution semantics.
- `docs/architecture/deployment.md` — Compose, Tengwar integration, health,
  upgrades, and production hardening.
- `docs/architecture/configuration-reference.md` — environment variables and
  validation rules.
- `docs/architecture/security.md` — SSRF, egress, untrusted content, and
  retention boundaries.
- `docs/architecture/url-identity-and-outcomes.md` — URL, document, evidence,
  and closed outcome contracts.
- Files under `docs/reviews/` are historical audit snapshots. The completed
  web-intelligence implementation plan and benchmark artifacts are not active
  documentation; behavior is documented in the files above.

## Working Rules

Keep changes surgical. Preserve REST/MCP contract parity, typed closed outcome
codes, bounded work, cancellation, URL safety, robots policy, untrusted-content
labels, and default non-persistence. Never vendor or mechanically adapt source
from Wigolo, Firecrawl, SearXNG, or Crawl4AI.

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
opaque AGPL dependency. Crawl4AI 0.9.2 is consumed as the public upstream
Docker image over HTTP with its own DNS-pinning outbound proxy; the retained
first-party proxy is not in that target-site path. Verify model-server and
model-weight licenses before bundling, and never commit secrets. Structured
profiles are explicit, bounded, source-addressed, and untrusted; `json_schema`
reuses the configured LLM settings and adds no environment variables.
