# Deployment Guide

## 1. Compose Profiles

Thorondor uses two Compose files that can be stacked:

### Base stack — `docker-compose.yml`

Defines six services:

| Service | Profile | Description |
|---|---|---|
| `orchestrator` | (always) | FastAPI search pipeline |
| `chunker` | (always) | Semantic chunking service |
| `searxng` | (always) | URL discovery engine |
| `crawl4ai` | (always) | Page content extractor |
| `egress-proxy` | (always) | SSRF egress proxy for Crawl4AI outbound traffic |
| `embedding` | `bundled-models` | HuggingFace TEI embedding server |
| `reranker` | `bundled-models` | HuggingFace TEI reranker server |

The `bundled-models` profile is intended for deployments where you want Docker to manage the embedding and reranker containers using TEI images that download model weights from HuggingFace Hub. Activate it with `--profile bundled-models`.

Without any profile flag, only the five base services start. In this mode `EMBEDDING_ENDPOINT` and `RERANKER_ENDPOINT` must point to externally operated servers.

### llama.cpp overlay — `docker-compose.llamacpp.yml`

An override file that replaces the `embedding` and `reranker` containers with llama.cpp server containers. It:

- Activates the `llamacpp-models` profile (use `--profile llamacpp-models` instead of `bundled-models`).
- Mounts `./models:/models:ro` into both model containers.
- Sets the llama.cpp image via `${LLAMACPP_IMAGE}` (a pinned SHA from `.env.llamacpp`).
- Overrides `RERANKER_ENDPOINT`, `RERANKER_PATH`, and `RERANKER_MODEL` in the orchestrator to match the llama.cpp server API shape (path `/reranking` instead of `/rerank`).
- Overrides `EMBEDDING_ENDPOINT` and `EMBEDDING_MODEL` in the chunker.

To use the llama.cpp overlay, always pass both files:

```powershell
docker compose `
  --env-file .env --env-file .env.llamacpp `
  -f docker-compose.yml -f docker-compose.llamacpp.yml `
  --profile llamacpp-models `
  up -d
```

## 2. Environment Files

### `.env`

Primary runtime configuration. Every key used by the Python settings loader must be present — the loader calls `_required()` or `_configured_optional()` for each key and raises `RuntimeError` on any missing or blank required key.

Copy from `.env.example`:

```powershell
Copy-Item .env.example .env
```

**Optional keys** — variables like `SEARXNG_API_KEY`, `CRAWL4AI_API_KEY`, `LLM_ENDPOINT`, `DOMAIN_BLOCKLIST`, etc. must be present in `.env` but may be blank. Blank means disabled. Do not delete these keys — the loader raises if the key is entirely absent.

**SEARXNG_SECRET** — this key must be non-blank when SearXNG starts. The deploy scripts generate a random 32-byte base64 secret when the field is blank. Do not commit a real secret value. Rotate by blanking the key in `.env` and re-running `deploy.ps1`.

### `.env.llamacpp`

llama.cpp profile overrides. Contains the pinned image SHA, GGUF container paths, model aliases, and batch sizes. Copy from `.env.llamacpp.example`:

```powershell
Copy-Item .env.llamacpp.example .env.llamacpp
```

Edit `LLAMACPP_EMBEDDING_MODEL` and `LLAMACPP_RERANKER_MODEL` if your GGUF files have different names.

### Strict key-presence validation

The Python settings loader validates every key at process startup. Compose validates that every `${VAR}` interpolation has a value (using `?` suffix for optional Compose variables). Running `docker compose config` before `up` is the recommended way to surface missing keys before a deployment attempt.

## 3. Service Startup Order and Health Dependencies

Compose `depends_on` relationships (no `condition: service_healthy` by default, except Crawl4AI):

```
orchestrator
  └── depends_on: searxng, crawl4ai, chunker

crawl4ai
  └── depends_on: egress-proxy
  └── healthcheck: redis-cli ping + curl /health (interval 30s, 3 retries, 5s start_period)
```

All other services (`searxng`, `chunker`, `egress-proxy`, `embedding`, `reranker`) start without explicit health-gate dependencies and are polled by the deploy script via `GET /healthz` on the orchestrator.

The deploy script waits up to 120 seconds (60 attempts × 2s sleep) for `/healthz` to return a response with no `false` values in the `dependencies` object. The llama.cpp profile wait loop is longer (90 × 2s = 180s) to accommodate model loading time.

## 4. The Deploy Script

`scripts/deploy.ps1` is the base deploy script. `scripts/deploy-llamacpp.ps1` is a thin wrapper that sets llama.cpp-specific arguments and calls `deploy.ps1`. Both accept the same parameters.

### deploy.ps1 step by step

1. **Parameter defaults** — `$Profile` defaults to `bundled-models`; override with `-Profile llamacpp-models` or `-Profile ""` (no profile). `$HealthUrl` defaults to `http://localhost:8080/healthz`.

2. **Env file bootstrap** — if `.env` does not exist, it is copied from `.env.example`. This means the first run always produces a valid `.env` from the example.

3. **SEARXNG_SECRET generation** — `Ensure-DotEnvValue` reads `.env`, finds the `SEARXNG_SECRET=` line, and if the value is blank, replaces it with a randomly generated 32-byte base64 string (using `System.Security.Cryptography.RandomNumberGenerator`). Existing non-blank values are never overwritten.

4. **Compose validation** — `docker compose config` is run. Any misconfiguration (missing variable, bad override) raises a non-zero exit and stops the script.

5. **Image build** — `docker compose build` builds first-party images (`orchestrator`, `chunker`, `egress-proxy`). Pulled images are not rebuilt.

6. **Service start** — `docker compose up -d` starts all containers in the selected profile.

7. **Health poll** — the script polls `GET $HealthUrl` every 2 seconds for up to 120 seconds. It succeeds when the response has at least one dependency value and none are `false`.

8. **Smoke test** — unless `-SkipSmoke` is passed, `smoke.ps1` is called with the same Compose files, env files, and profile.

### deploy-llamacpp.ps1 additions

Before calling `deploy.ps1`, this script:

1. Copies `.env.llamacpp.example` to `.env.llamacpp` if not present.
2. Reads `LLAMACPP_EMBEDDING_MODEL` and `LLAMACPP_RERANKER_MODEL` from `.env.llamacpp`.
3. Converts container paths (`/models/<name>`) to host paths (`models\<name>`) and checks they exist. If any are missing, it calls `Write-Error` and stops.

## 5. The Smoke Test

`scripts/smoke.ps1` validates that a running deployment can serve real search results.

### Standard mode

Sends one POST to `/search` (the compat endpoint):

```json
{
  "query": "what changed in the EU AI Act timeline in 2025",
  "token_budget": 4000
}
```

Asserts:
- `passages` is non-empty.
- `citations` is not null.
- `stats.reranked == true`.
- `stats.tokens_returned <= token_budget`.

### Investigation mode (`-Investigation`)

Sends two additional queries with broader parameters:

1. A narrow factual query (`"Where was J. Robert Oppenheimer born?"` with `search_profile="research"`, `max_urls=8`, `max_passages=8`).
2. A broad multi-term query that should trigger the candidate prefilter.

In investigation mode, asserts all standard checks plus that `url_diagnostics` is non-empty for each response.

### Passing vs. failing

A passing run exits with code 0 and prints each request/response JSON pair. A failing run calls `Write-Error`, which throws a terminating error and sets exit code 1. The deploy script propagates this failure to its own exit code.

## 6. Upgrading

**Pull updated external images:**

```powershell
docker compose -f docker-compose.yml pull
```

**Rebuild first-party images after source changes:**

```powershell
docker compose -f docker-compose.yml build
```

**Restart services without recreating volumes:**

```powershell
docker compose -f docker-compose.yml up -d
```

Since Thorondor has no persistent corpus, there is no data migration. Restart is non-destructive. SearXNG writes no persistent state that would be lost on container recreation (the `settings.yml` is a mounted volume).

**Update the llama.cpp image:**

Edit `LLAMACPP_IMAGE` in `.env.llamacpp` to the new pinned SHA, then re-run `deploy-llamacpp.ps1`. The script will pull the new image and restart the model containers.

**Rotate SEARXNG_SECRET:**

Blank the `SEARXNG_SECRET=` value in `.env`, then re-run `deploy.ps1`. The script will generate a new secret and restart the SearXNG container with it.

## 7. Running on ARM64 / DGX Spark

The llama.cpp image SHA `4c52f549b6612fc1b4aee696c4cfb4a9dceecb10216bb7e677cf97db909e1b4a` is a multi-arch manifest that includes `linux/arm64`. The TEI image SHA is similarly multi-arch.

No ARM64-specific code changes are needed. Run the same `deploy-llamacpp.ps1` command. Docker Desktop or Docker Engine on the ARM64 host will pull the correct architecture layer.

Known considerations:
- CPU inference is the default. CUDA or Metal acceleration in llama.cpp requires rebuilding the image with GPU support — beyond the scope of this deployment guide.
- The SearXNG image (`sha256:02d441bb...`) and Crawl4AI image (`unclecode/crawl4ai:0.8.9`) are pulled from Docker Hub; verify ARM64 manifest availability for any image tag update.

## 8. Production Hardening Checklist

- [ ] **TLS termination** — place a reverse proxy (nginx, Caddy, Traefik) in front of port `ORCHESTRATOR_PORT` with a valid TLS certificate. The orchestrator does not terminate TLS itself.
- [ ] **Access control** — restrict the orchestrator port to authorized clients. No authentication is built into the REST or MCP endpoints.
- [ ] **Rotate SEARXNG_SECRET** — ensure `SEARXNG_SECRET` is a strong random value (the deploy script generates one; verify it is set in `.env` before first production start).
- [ ] **Enable ALLOWLIST_ONLY** — set `ALLOWLIST_ONLY=true` and populate `DOMAIN_ALLOWLIST` for deployments where crawling should be restricted to known domains.
- [ ] **SSRF proxy** — the egress proxy is enabled by default in Compose. Verify `PROXY_BLOCKED_IP_CATEGORIES` and `PROXY_BLOCKED_SPECIAL_IPS` match your network topology. Add any additional internal subnets to `DOMAIN_BLOCKLIST` or to blocked categories.
- [ ] **API keys on internal seams** — set `SEARXNG_API_KEY`, `CRAWL4AI_API_KEY`, `CHUNKER_API_KEY`, `RERANKER_API_KEY`, and `EMBEDDING_API_KEY` if the corresponding services are accessible beyond the internal Docker network.
- [ ] **Log shipping** — the orchestrator emits JSON logs to stdout. Configure a log driver or sidecar to ship to your log aggregation system.
- [ ] **Container resource limits** — set memory limits for all containers, especially `CHUNKER_MEM_LIMIT` (default `768m`) for large documents with many segments. The DP chunker allocates O(N²) during similarity matrix computation.
- [ ] **Health monitoring** — integrate `/healthz` with your monitoring system. Alert on `hard_failures` being non-empty.
- [ ] **Docker socket exposure** — Thorondor does not require access to the Docker socket. Verify no container has it mounted.
- [ ] **Secrets management** — do not commit `.env` or `.env.llamacpp` files containing secrets. Use a secrets manager or CI/CD vault to inject values at deploy time.
