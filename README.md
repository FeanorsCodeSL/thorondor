# Thorondor

Thorondor is a local semantic web-search stack. The orchestrator discovers web
results, crawls selected pages, sends page text to the semantic chunker, reranks
the chunks, and returns cited passages through REST and MCP.

## Repository Layout

- `orchestrator/` - FastAPI REST and MCP service for the search pipeline.
- `semantic-chunking-service/` - FastAPI chunker using an OpenAI-compatible embedding endpoint.
- `searxng/` - local SearXNG configuration mounted read-only by Compose.
- `scripts/` - Windows PowerShell and Bash deployment/smoke helpers.
- `docs/foundational design/` - original architecture and deployment design.
- `docs/plans/` - implementation plans and verification notes.
- `.agents/skills/thorondor-web-search/` - repo-local agent skill for using Thorondor.

## Windows Quickstart With llama.cpp

Prerequisites:

- Docker Desktop is installed, running, and using Linux containers.
- Two GGUF files are available locally: one embedding model and one reranker model.
- Model license terms are acceptable for your use before placing weights in `models/`.

From PowerShell in the repo root:

```powershell
Copy-Item .env.example .env
Copy-Item .env.llamacpp.example .env.llamacpp
New-Item -ItemType Directory -Force models | Out-Null
```

Place the files referenced by `.env.llamacpp` under `models\`:

```text
models\bge-m3.gguf
models\bge-reranker-v2-m3.gguf
```

Then start the full local workflow:

```powershell
.\scripts\deploy-llamacpp.ps1
```

The script validates Compose, builds the first-party images, starts SearXNG,
Crawl4AI, the chunker, the orchestrator, and two llama.cpp model containers,
then runs a smoke search unless `-SkipSmoke` is supplied.

Useful checks:

```powershell
Invoke-RestMethod http://localhost:8080/healthz
docker compose --env-file .env --env-file .env.llamacpp -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models ps
```

## REST Usage

```powershell
$body = @{
  query = "what changed in the EU AI Act timeline in 2025"
  token_budget = 4000
} | ConvertTo-Json

Invoke-RestMethod http://localhost:8080/search `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The response includes `passages`, `citations`, and `stats`. If the reranker is
unavailable, Thorondor returns passages with `stats.reranked=false` instead of
failing the search.

## MCP Usage

Point an MCP client at:

```text
http://localhost:8080/mcp
```

The exposed tool is `web_search`. It returns the same citation-bearing passage
shape as `POST /search`.

## Agent Skill

The repo-local skill is:

```text
.agents/skills/thorondor-web-search/SKILL.md
```

Use it in agent environments that load repository skills when an agent should
query Thorondor for live web evidence, cite returned passages, or call the MCP
`web_search` tool.

## Development Checks

```powershell
python -m pytest semantic-chunking-service\tests orchestrator\tests -v
docker compose -f docker-compose.yml config
docker compose --env-file .env --env-file .env.llamacpp -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config
```

## Configuration

Base runtime configuration lives in `.env`. llama.cpp-specific model settings
live in `.env.llamacpp`.

Important variables:

- `EMBEDDING_ENDPOINT` and `EMBEDDING_MODEL` configure the chunker's `/v1/embeddings` server.
- `RERANKER_ENDPOINT`, `RERANKER_MODEL`, `RERANKER_PATH`, and `RERANKER_HEALTH_PATH` configure reranking.
- `MAX_URLS`, `CRAWL_CONCURRENCY`, `CRAWL_TIMEOUT_S`, and `DEFAULT_TOKEN_BUDGET` tune pipeline cost and latency.
- `DOMAIN_BLOCKLIST` applies a comma-separated host blocklist before crawl.
- `LLAMACPP_IMAGE`, `LLAMACPP_EMBEDDING_MODEL`, and `LLAMACPP_RERANKER_MODEL` select the local llama.cpp image and GGUF files.

The llama.cpp override uses `ghcr.io/ggml-org/llama.cpp:server`, `/v1/embeddings`
for embeddings, `/reranking` for reranking, and `/health` for model readiness.
These routes and image names are based on the upstream llama.cpp server and
Docker documentation:

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md

## Troubleshooting

- If Docker commands report a missing `dockerDesktopLinuxEngine` pipe, start Docker Desktop and wait until it reports ready.
- If `deploy-llamacpp.ps1` reports missing GGUF files, either place them under `models\` or edit `.env.llamacpp` to match your filenames.
- If health shows `embedding=false`, the chunker is up but cannot get vectors from the embedding container.
- If health shows `reranker=false`, check the reranker model path and the llama.cpp container logs.
