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
| `egress-proxy` | (always) | Retained first-party SSRF proxy; not used by Crawl4AI 0.9.2 |
| `embedding` | `bundled-models` | HuggingFace TEI embedding server |
| `reranker` | `bundled-models` | HuggingFace TEI reranker server |

The `bundled-models` profile is intended for AMD64 NVIDIA CUDA deployments where Docker manages the embedding and reranker containers using TEI images that download model weights from the immutable Hub revisions in `.env`. The host must provide NVIDIA Container Toolkit GPU access. Activate it with `--profile bundled-models`.

Without any profile flag, only the five base services start. In this mode `EMBEDDING_ENDPOINT` and `RERANKER_ENDPOINT` must point to externally operated servers.

### llama.cpp overlay — `docker-compose.llamacpp.yml`

An override file that replaces the `embedding` and `reranker` containers with llama.cpp server containers. It:

- Activates the `llamacpp-models` profile (use `--profile llamacpp-models` instead of `bundled-models`).
- Mounts `./models:/models:ro` into both model containers.
- Sets the llama.cpp image via `${LLAMACPP_IMAGE}` (a pinned SHA from `.env.llamacpp`).
- Overrides `RERANKER_ENDPOINT`, `RERANKER_PATH`, and `RERANKER_MODEL` in the orchestrator to match the llama.cpp server API shape (path `/reranking` instead of `/rerank`).
- Overrides `EMBEDDING_ENDPOINT` and `EMBEDDING_MODEL` in the chunker.

`deploy-llamacpp.ps1` (and `deploy-llamacpp.sh`) call `thorondor download-models` to fetch any missing GGUF files into `./models/` before bringing the stack up, so the overlay works on a fresh checkout with no manual model placement. The same flow is wired into the TUI: picking the `llamacpp` mode auto-downloads the missing files with a progress indicator, then writes the env and returns to the dashboard.

To use the llama.cpp overlay, always pass both files:

```powershell
docker compose `
  --env-file .env --env-file .env.llamacpp `
  -f docker-compose.yml -f docker-compose.llamacpp.yml `
  --profile llamacpp-models `
  up -d
```

### Production image slice — `docker-compose.production.yml`

`docker-compose.production.yml` is the source-free deployment slice for
Tengwar-style production hosts. It has no `build:` blocks and starts no
embedding or reranker containers. Instead, it consumes:

- `THORONDOR_ORCHESTRATOR_IMAGE`
- `THORONDOR_CHUNKER_IMAGE`
- `THORONDOR_EGRESS_PROXY_IMAGE`
- `THORONDOR_SEARXNG_IMAGE`
- `THORONDOR_CRAWL4AI_IMAGE`

The first three are first-party GHCR images published by the `publish-images`
workflow. Use the digest refs from the release asset when promoting to
production, for example:

```text
THORONDOR_ORCHESTRATOR_IMAGE=ghcr.io/feanorscodesl/thorondor-orchestrator@sha256:...
```

The SearXNG and Crawl4AI refs are pinned upstream images by default. If the
production host cannot pull Docker Hub directly, mirror those exact pinned
images into GHCR and update `THORONDOR_SEARXNG_IMAGE` and
`THORONDOR_CRAWL4AI_IMAGE`.

The production slice joins Tengwar's external network through
`THORONDOR_APP_NETWORK` (default `app-network`) and expects model servers named
`embedding` and `reranker` on that network. It also expects a copied SearXNG
config directory via `THORONDOR_SEARXNG_CONFIG_DIR`; do not mount the Thorondor
source tree on the production host.

Crawl4AI's API uses `thorondor-crawl-control`, an internal network shared only
with the orchestrator. Its built-in DNS-pinning proxy reaches target sites
through the dedicated `thorondor-crawl-egress` network, whose gateway priority
makes it the deterministic default route. This requires Docker Engine 28+ and
Docker Compose 2.33.1+. SearXNG retains separate provider-plane egress for
search engines, while configured model providers use the operator-owned
application network. The release guard renders all three first-party Compose
variants and verifies these roles.

For the Tengwar development integration, the Tengwar repository owns the wrapper
recipe. Run `just thorondor-deploy` from the Tengwar checkout. It forces
`THORONDOR_APP_NETWORK=tengwar-shared`, points the orchestrator and chunker at
Tengwar's `embedding` and `reranker` services, builds configured `:local`
first-party images from the sibling checkout, generates `CRAWL4AI_API_KEY` when
needed, and waits for `/health`. This is a development Compose workflow; the
immutable production candidate path remains the source-free image slice above.

The wrapper recreates only the Thorondor Compose project. Preserve the external
network and all volumes; do not use `down -v`, volume pruning, or a whole-host
Compose shutdown to change Thorondor versions.

The orchestrator mounts the named `thorondor-page-cache` volume at
`/var/lib/thorondor`; the image pre-creates that directory for its non-root
runtime user. Persistence remains inactive while `PAGE_CACHE_ENABLED=false`.
The optional crawl-job database uses the same mounted directory but remains
inactive while `CRAWL_JOBS_ENABLED=false`. When enabled, deploy exactly one
orchestrator replica: the durable crawl-job worker and SQLite store are deliberately
single-process and do not require Redis or a queue service. Set the job attempt
deadline, retained-record cap, retention windows, and raw-HTML policy before
enabling it. One running background job consumes a shared crawl-admission slot.
Before enabling the page cache, set the TTL, stale window, absolute retention, raw-HTML
policy, and diff bounds in the deployment environment. Inspect or maintain the
database without exposing an unauthenticated administration endpoint:

Keep these shared values in `.env`; the later `.env.llamacpp` and
`.env.production` overlays intentionally contain only mode-specific settings so
they cannot silently replace the operator's page-cache policy.

```bash
docker exec thorondor python -m orchestrator.page_cache \
  --path /var/lib/thorondor/page-cache.sqlite3 stats
docker exec thorondor python -m orchestrator.page_cache \
  --path /var/lib/thorondor/page-cache.sqlite3 clear-url https://example.com/private
docker exec thorondor python -m orchestrator.page_cache \
  --path /var/lib/thorondor/page-cache.sqlite3 cleanup
```

The exact container name may include the Compose project prefix. `clear-url`
deletes every capability and cleaner variant for that conservative URL identity;
it does not clear other URLs. Removing the named volume is a separate destructive
operation and is never part of routine deployment.

Validate the production config with:

```powershell
docker compose `
  --env-file .env --env-file .env.production `
  -f docker-compose.production.yml `
  config
```

## 2. Environment Files

### `.env`

Primary runtime configuration. Every key used by the Python settings loader must be present — the loader calls `_required()` or `_configured_optional()` for each key and raises `RuntimeError` on any missing or blank required key.

Copy from `.env.example`:

```powershell
Copy-Item .env.example .env
```

**Optional keys** — variables like `SEARXNG_API_KEY`, `LLM_ENDPOINT`, and `DOMAIN_BLOCKLIST` must be present in `.env` but may be blank. Blank means disabled. Do not delete these keys — the loader raises if the key is entirely absent. `CRAWL4AI_API_KEY` is generated for the managed Crawl4AI container; it may remain blank only for an unauthenticated BYO endpoint.

**ORCHESTRATOR_HOST** — defaults to `127.0.0.1` in `.env.example`, which keeps the public REST/MCP port local to the host. Set it to `0.0.0.0` only when a firewall, TLS, authentication, and rate limiting are already in front of the service.

**SEARXNG_SECRET** — this key must be non-blank when SearXNG starts. The deploy scripts generate a random 32-byte base64 secret when the field is blank. Do not commit a real secret value. Rotate by blanking the key in `.env` and re-running `deploy.ps1`.

**CRAWL4AI_API_KEY** — the deploy scripts and configurator generate a random managed-service credential when this field is blank and preserve existing non-blank values.

**CRAWL4AI_ALLOW_INTERNAL_URLS** — must remain `false` for the managed Crawl4AI 0.9.2 container. This keeps its connect-time DNS-pinning proxy restricted to globally routable targets. Do not use the upstream escape hatch to reach private or link-local destinations.

**Crawler identity** — `CRAWLER_USER_AGENT` is the stable outbound identity sent through Crawl4AI and must contain a contact URL. `CRAWLER_ROBOTS_USER_AGENT` is the matching token reserved for the independent robots implementation. Do not rotate or impersonate browser identities.

### `.env.llamacpp`

llama.cpp profile overrides. Contains the pinned image SHA, GGUF container paths, model aliases, and batch sizes. Copy from `.env.llamacpp.example`:

```powershell
Copy-Item .env.llamacpp.example .env.llamacpp
```

Edit `LLAMACPP_EMBEDDING_MODEL` and `LLAMACPP_RERANKER_MODEL` if your GGUF files have different names.

### `.env.production`

Production image overlay for `docker-compose.production.yml`. Copy from
`.env.production.example`, then replace the first-party image tag refs with the
digest refs emitted by the GitHub release workflow. This file also changes
production service URLs so the orchestrator uses `thorondor-chunker` and
the dedicated `thorondor-crawl-control` network to reach Crawl4AI.

### Strict key-presence validation

The Python settings loader validates every key at process startup. Compose validates that every `${VAR}` interpolation has a value (using `?` suffix for optional Compose variables). Running `docker compose config` before `up` is the recommended way to surface missing keys before a deployment attempt.

## 3. Textual Configurator

`thorondor` is the interactive host entrypoint. The installer creates the
command with:

```bash
curl -LsSf https://raw.githubusercontent.com/FeanorsCodeSL/thorondor/main/scripts/install.sh | sh
```

The first `thorondor` run initializes a managed deployment directory under
`~/.thorondor`, writes packaged Compose/env/SearXNG assets there, and launches a
full-screen Textual dashboard. The dashboard is a sibling of the Imladris
configurator: black-on-gold chrome with `◆`/`✦` ornaments, an action rail on
the left, a live components table on the right, and arrow-key navigation
(`↑/↓` to move, `Enter` to open, `←/→` to switch panels, `Esc` to back out).
The actions are:

| Action | Writes or checks |
|---|---|
| `Mode` | Selects BYO endpoints, bundled TEI containers, or llama.cpp containers. |
| `Endpoints` | Edits embedding, reranker, and optional LLM-operation endpoints. |
| `Search/crawl` | Edits ports, budgets, crawl limits, robots, and domain filters. |
| `Validate` | Reports env completeness and the Compose command that will run. |
| `Deploy` | Runs `docker compose config`, `build`, `up -d`, `/health`, and smoke search. |
| `Wire MCP` | Wires Claude Code, Codex, or OpenCode to stdio `thorondor-mcp` or HTTP `/mcp`. |
| `Refresh state` | Re-reads `.env` and re-detects harnesses without restarting. |
| `Quit` | Exits the dashboard. |

The dashboard does not keep unsaved state. Save/apply actions write complete env
files at `0600`, re-read from disk, and return to the dashboard. `Esc` from a
sub-screen discards in-progress edits.

`thorondor doctor` is the plain-text status command for shells and automation.
`thorondor uninstall` stops the managed stack, removes `~/.thorondor`, and
uninstalls the local tool unless `--keep-tool` is passed. `thorondor-mcp` is the
native stdio MCP proxy. It forwards `web_search`, `web_fetch`, `web_map`, and
`web_crawl` to the running stack's matching versioned REST endpoints; the
Dockerized streamable HTTP MCP endpoint continues to be served at `/mcp`.
When the stack overrides `MAX_RESPONSE_BODY_BYTES`, pass the same value in the
native proxy process environment so its REST-read and final MCP-result bounds
remain aligned with the orchestrator.

### MCP ingress and conformance evidence

The MCP endpoint is an application protocol surface, not an authentication or
TLS boundary. Keep the published orchestrator port on loopback for local use.
When it must be exposed, place a reverse proxy or equivalent protected ingress
in front of `/mcp` and the REST routes to terminate TLS, authenticate callers,
authorize access, and apply rate limiting. MCP Host/Origin checks and protocol
header validation protect transport routing; they do not establish caller
identity. The native stdio proxy is local process access and forwards the same
REST contracts, so its host deployment must protect the configured REST base
URL as well.

The selected MCP evidence can be reproduced without deployment or external
services after installing the pinned runner:

```bash
npm ci --prefix tools/mcp-conformance --ignore-scripts
PYTHONPATH=. .venv/bin/python scripts/run-mcp-conformance.py \
  --output-dir /tmp/thorondor-mcp-conformance
```

Use a fresh output directory. The runner stores logs, a manifest, per-scenario
artifacts, and evaluation; it rejects stale output and does not let
fixture-dependent nonzero runner statuses turn into a production claim.

### External and host model endpoints

The base Compose topology keeps `chunker` on the `internal` network only. That
is correct for bundled and llama.cpp model containers, where the embedding
server is also internal. A BYO embedding endpoint outside the internal network
requires a generated overlay:

```text
docker-compose.host-endpoints.yml
```

The overlay adds `egress` to `chunker` so it can call the external embedding
server. If an operator entered `localhost` or `127.0.0.1`, the configurator
rewrites it to `host.docker.internal` and the overlay adds
`host.docker.internal:host-gateway` to `chunker` and `orchestrator` for Linux
Docker Engine. The base Compose files remain unchanged.

`scripts/deploy.ps1`, `scripts/deploy.sh`, and `scripts/smoke.*` remain the
non-interactive and CI entrypoints.

## 4. Service Startup Order and Health Dependencies

Compose waits for each local API dependency to become healthy before
starting the orchestrator:

```
orchestrator
  └── depends_on service_healthy: searxng, crawl4ai, chunker
  └── healthcheck: GET /health (interval 30s, 3 retries)

chunker
  └── healthcheck: GET /health (interval 30s, 3 retries)

searxng
  └── healthcheck: GET /healthz (interval 30s, 3 retries)

crawl4ai
  └── healthcheck: curl /health (interval 30s, 3 retries, 40s start_period)
```

The orchestrator and chunker expose only `GET /health`. It reports local process
availability and never probes SearXNG, Crawl4AI, an embedding provider, or a
reranker. The SearXNG image's built-in `/healthz` returns a local constant
response and does not perform a search. Crawl4AI's `/health` reports local server
metadata and does not crawl. These are internal Compose implementation details;
Thorondor consumers use `/health` only.

The deploy script waits up to 120 seconds (60 attempts x 2s sleep) for the
orchestrator `/health` response to report `status: ok`. The optional smoke step
then performs an explicit live search; it is functional verification rather than
a health probe and may contact configured search and model providers.

Structured fetch profiles are opt-in request capabilities and require no extra
container or environment variable. `links`, `tables`, and `json_ld` use the
live Crawl4AI source and return bounded source references. `json_schema` also
uses the configured `LLM_ENDPOINT`, `LLM_MODEL`, and optional `LLM_API_KEY`; a
blank LLM endpoint leaves only that profile unsupported. Structured requests
bypass page-cache persistence and cannot be combined with watches, change/diff
semantics, stale reads, or conditional revalidation.

## 5. The Deploy Script

`scripts/deploy.ps1` is the base deploy script. `scripts/deploy-llamacpp.ps1` is a thin wrapper that sets llama.cpp-specific arguments and calls `deploy.ps1`. Both accept the same parameters.

### deploy.ps1 step by step

1. **Parameter defaults** — `$Profile` defaults to `bundled-models`; override with `-Profile llamacpp-models` or `-Profile ""` (no profile). `$HealthUrl` defaults to `http://localhost:8080/health`.

2. **Env file bootstrap** — if `.env` does not exist, it is copied from `.env.example`. This means the first run always produces a valid `.env` from the example.

3. **Internal secret generation** — `Ensure-DotEnvValue` generates non-blank `SEARXNG_SECRET` and `CRAWL4AI_API_KEY` values with `System.Security.Cryptography.RandomNumberGenerator`. Existing non-blank values are never overwritten.

4. **Compose validation** — `docker compose config` is run. Any misconfiguration (missing variable, bad override) raises a non-zero exit and stops the script.

5. **Image build** — `docker compose build` builds first-party images (`orchestrator`, `chunker`, `egress-proxy`). Pulled images are not rebuilt.

6. **Service start** — `docker compose up -d` starts all containers in the selected profile.

7. **Health poll** — the script polls `GET $HealthUrl` every 2 seconds for up to 120 seconds. It succeeds when the local response reports `status: ok` and never invokes a dependency.

8. **Smoke test** — unless `-SkipSmoke` is passed, `smoke.ps1` is called with the same Compose files, env files, and profile.

### deploy-llamacpp.ps1 additions

Before calling `deploy.ps1`, this script:

1. Copies `.env.llamacpp.example` to `.env.llamacpp` if not present.
2. Reads `LLAMACPP_EMBEDDING_MODEL` and `LLAMACPP_RERANKER_MODEL` from `.env.llamacpp`.
3. Converts container paths (`/models/<name>`) to host paths (`models\<name>`) and checks they exist. If any are missing, it calls `Write-Error` and stops.

## 6. The Smoke Test

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

## 7. Upgrading

Before upgrading an existing installation, synchronize its secret-bearing `.env`
with the tracked environment template. Add the required `PAGE_CACHE_*` and
`PAGE_DIFF_*` values while keeping `PAGE_CACHE_ENABLED=false` unless persistence
is intended. Add all `CRAWL_JOB_*` values plus `CRAWL_JOBS_ENABLED=false` and
`CRAWL_SYNC_MAX_PAGES=10`; enable jobs only after accepting local result
retention and the single-replica requirement. Add `CRAWLER_USER_AGENT` and
`CRAWLER_ROBOTS_USER_AGENT`, and remove the retired
`CRAWL_VALIDATE_REDIRECTS` and `CRAWL_MAX_PREFLIGHT_REDIRECTS` keys. Promote the
orchestrator and semantic chunker images together because exact evidence uses the
`ORCHESTRATOR_MARKDOWN` chunking contract. A mismatched older chunker now causes an
explicit dependency failure instead of an empty successful search response.

**Publish first-party production images:**

Create and push a release tag such as `v0.1.0`, or run the `publish-images`
workflow manually with `image_tag=0.1.0`. The workflow builds
`thorondor-orchestrator`, `thorondor-chunker`, and `thorondor-egress-proxy` for
`linux/amd64` and `linux/arm64`, pushes them to GHCR, and emits digest refs in
`THORONDOR_IMAGE_DIGESTS.md`.

**Promote a production image release:**

Update `.env.production` or Tengwar's environment store with the new digest
refs, then rerun:

```powershell
docker compose `
  --env-file .env --env-file .env.production `
  -f docker-compose.production.yml `
  up -d
```

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

**Rotate managed service secrets:**

Blank `SEARXNG_SECRET=` or `CRAWL4AI_API_KEY=` in `.env`, then re-run `deploy.ps1`. The script generates a new value and restarts the affected service with it.

## 8. Running on ARM64 / DGX Spark

The llama.cpp build `b10276` image SHA `bde659bfc300ee7d4d2e558e8a97e06211bc2bf079e31d22b61497f4f2cd85b1` is a multi-arch manifest that includes `linux/arm64`. The pinned TEI revision `4150561` is AMD64-only, so ARM64 deployments must use the llama.cpp profile or BYO model endpoints.

No ARM64-specific code changes are needed. Run the same `deploy-llamacpp.ps1` command. Docker Desktop or Docker Engine on the ARM64 host will pull the correct architecture layer.

Known considerations:
- CPU inference is the default. CUDA or Metal acceleration in llama.cpp requires rebuilding the image with GPU support — beyond the scope of this deployment guide.
- The SearXNG image (`sha256:f4c8e59d...`) and Crawl4AI image (`sha256:bd36741e...`) are pulled from Docker Hub; both pinned indexes include AMD64 and ARM64 manifests.
- The bundled TEI profile cannot run natively on ARM64 with the pinned image.

## 9. Production Hardening Checklist

- [ ] **Host binding** — keep `ORCHESTRATOR_HOST=127.0.0.1` for local use. Set `ORCHESTRATOR_HOST=0.0.0.0` only when the service is behind firewall, TLS, authentication, and rate limiting.
- [ ] **TLS termination** — place a reverse proxy (nginx, Caddy, Traefik) in front of port `ORCHESTRATOR_PORT` with a valid TLS certificate before exposing it beyond localhost. The orchestrator does not terminate TLS itself.
- [ ] **Access control** — restrict the orchestrator port to authorized clients. No authentication is built into the REST or MCP endpoints.
- [ ] **Rotate SEARXNG_SECRET** — ensure `SEARXNG_SECRET` is a strong random value (the deploy script generates one; verify it is set in `.env` before first production start).
- [ ] **Enable ALLOWLIST_ONLY** — set `ALLOWLIST_ONLY=true` and populate `DOMAIN_ALLOWLIST` for deployments where crawling should be restricted to known domains.
- [ ] **Crawl egress** — verify Crawl4AI has only its isolated control and dedicated default-gateway egress networks, `CRAWL4AI_ALLOW_INTERNAL_URLS=false`, no external proxy variables, no published port, and the upstream read-only/non-root hardening.
- [ ] **API keys on internal seams** — verify the generated `CRAWL4AI_API_KEY`; set `SEARXNG_API_KEY`, `CHUNKER_API_KEY`, `RERANKER_API_KEY`, and `EMBEDDING_API_KEY` if the corresponding services are accessible beyond the internal Docker network.
- [ ] **Log shipping** — the orchestrator emits JSON logs to stdout. Configure a log driver or sidecar to ship to your log aggregation system.
- [ ] **Container resource limits** — set memory limits for all containers, especially `CHUNKER_MEM_LIMIT` (default `768m`) for large documents with many segments. The DP chunker allocates O(N²) during similarity matrix computation.
- [ ] **Health monitoring** — use `/health`. It is local-only and safe for frequent polling; observe dependency failures through real request outcomes and service-specific Compose health state.
- [ ] **Docker socket exposure** — Thorondor does not require access to the Docker socket. Verify no container has it mounted.
- [ ] **Secrets management** — do not commit `.env` or `.env.llamacpp` files containing secrets. Use a secrets manager or CI/CD vault to inject values at deploy time.
