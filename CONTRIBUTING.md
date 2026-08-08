# Contributing

Thorondor is a Python/FastAPI project split into an orchestrator service, a
semantic chunking service, and a retained first-party SSRF proxy used for
deployment compatibility. Crawl4AI remains an unmodified upstream service.

## Setup

Use Python 3.13. From the repository root:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install \
  -r orchestrator/requirements.txt \
  -r orchestrator/requirements-dev.txt \
  -r semantic-chunking-service/requirements.txt \
  -r semantic-chunking-service/requirements-dev.txt
```

On Windows, use `.\.venv\Scripts\python` and the backslash-separated paths in
the corresponding PowerShell scripts.

## Checks

Run the relevant checks before opening a change:

```bash
./.venv/bin/python -m pytest semantic-chunking-service/tests orchestrator/tests -v
./.venv/bin/python -m pytest thorondor_cli/tests -q
PYTHON=./.venv/bin/python bash scripts/check-release-guard.sh
docker compose --env-file .env.example -f docker-compose.yml --profile bundled-models config
docker compose --env-file .env.example --env-file .env.llamacpp.example \
  -f docker-compose.yml -f docker-compose.llamacpp.yml \
  --profile llamacpp-models config
```

The PowerShell equivalents are `.\scripts\check-release-guard.ps1` and the
same pytest and Compose commands with Windows path separators. Focused changes
should also run their directly relevant tests and lint checks.

## Scope

Keep changes surgical. Do not vendor SearXNG, Crawl4AI, model weights, or
third-party source into this repository. Keep every key from `.env.example`
explicit in local `.env` files; do not rely on fallback configuration values.
Structured profiles must remain explicit opt-ins with bounded,
source-addressed output and untrusted-content labels. Do not make structured
extraction part of ordinary search or add caller-controlled code, headers,
cookies, regular expressions, or model endpoints.

## Documentation

Keep `README.md` and the documents under `docs/architecture/` synchronized with
the implemented REST/MCP contracts, limits, deployment topology, and security
boundaries. `docs/reviews/` contains historical snapshots and should not be
rewritten unless the user explicitly requests a revised artifact.

## Pull Requests

Summaries should state the user-facing behavior change, commands run, and any
license, model, or deployment impact. Do not commit, amend, or push without
explicit user authorization, and never add AI tools as authors or co-authors.
