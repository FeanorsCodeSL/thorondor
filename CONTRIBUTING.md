# Contributing

Thorondor is a Python/FastAPI project split into an orchestrator service, a semantic chunking service, and a minimal SSRF egress proxy.

## Setup

Use Python 3.13. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install `
  -r orchestrator\requirements.txt `
  -r orchestrator\requirements-dev.txt `
  -r semantic-chunking-service\requirements.txt `
  -r semantic-chunking-service\requirements-dev.txt
```

## Checks

Run these before opening a change:

```powershell
.\.venv\Scripts\python -m pytest semantic-chunking-service\tests orchestrator\tests -v
docker compose --env-file .env.example -f docker-compose.yml --profile bundled-models config
docker compose --env-file .env.example --env-file .env.llamacpp.example -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config
.\scripts\check-release-guard.ps1
```

## Scope

Keep changes surgical. Do not vendor SearXNG, Crawl4AI, model weights, or third-party source into this repository. Keep every key from `.env.example` explicit in local `.env` files; do not rely on fallback configuration values.

## Pull Requests

Summaries should state the user-facing behavior change, commands run, and any license, model, or deployment impact.
