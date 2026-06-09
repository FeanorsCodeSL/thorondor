# Deployment & Integration — Implementation Plan

> **Execution.** Phase-by-phase, implementation agent → independent verification
> agent. **Depends on Plans 01 and 02** (both service images must build first).
> This plan wires the containers together and proves the full pipeline end-to-end.

## Goal

Compose the whole stack — first-party `orchestrator` + `chunker`, upstream
container services `SearXNG` + `Crawl4AI`, and optional bundled model servers — into one
`docker-compose.yml` with the default core stack plus the optional
`bundled-models` profile, a Windows-testable llama.cpp override
(`docker-compose.llamacpp.yml`), a complete `.env.example`, the SearXNG
configuration it needs, and a green end-to-end smoke test where a real
`POST /search` returns reranked, cited, budgeted passages.

## References

- `docs/foundational design/03-deployment.md` — §1 topology, §3 the full
  compose file (`name: thorondor`), §4 env reference, §6 SearXNG
  config, §7 sizing, §8 healthchecks/startup order.
- `docs/foundational design/05-licensing-and-sovereignty.md` — §2 Chroma attribution,
  §3 the SearXNG AGPL boundary, §4 model-server license drift, §5 crawl posture.
- `docs/foundational design/04-api-and-agent-integration.md` §1/§3 — the `/search`
  request and the end-to-end agent example used as the smoke test.
- `docs/foundational design/README.md` — the Quickstart that must work as written.

## Build & run

- **Containerized:** yes (this plan *is* the container wiring)
- **Build command:** `docker compose build`
- **Validate command:** `docker compose config` (and `--profile bundled-models config`)
- **llama.cpp validate command:** `docker compose --env-file .env.example --env-file .env.llamacpp.example -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config`
- **Smoke command:** `docker compose --profile bundled-models up -d` then the `curl /search` in Phase 3
- **Windows llama.cpp smoke command:** `.\scripts\deploy-llamacpp.ps1`
- **Test command:** the end-to-end smoke script `scripts/smoke.sh` (Phase 3)

---

## Phase 1 — Compose file + bundled-models profile
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `docker-compose.yml` at the repo root from doc 03 §3: `name: thorondor`; default core services `orchestrator` (build `orchestrator/Dockerfile`, the **only** published port `8080:8080`), `chunker` (build `./semantic-chunking-service`), `searxng` (`searxng/searxng:latest`, read-only `./searxng:/etc/searxng`), and `crawl4ai` as the public upstream self-hosted Docker API (`unclecode/crawl4ai:0.8.9`, `shm_size: "1g"`, image-only/no local build); optional `embedding` + `reranker` under `profiles: ["bundled-models"]`; a single internal `networks: [internal]`. There is no separate `core` compose profile: core is the default stack when no profile is supplied.
- [x] Wire the orchestrator env block to the internal service URLs (`SEARXNG_URL=http://searxng:8080`, `CRAWL4AI_URL=http://crawl4ai:11235`, `CHUNKER_URL=http://chunker:8000`) and pass the BYO/knob vars through from `.env` with the documented defaults.
- [x] Add `depends_on: [searxng, crawl4ai, chunker]` to the orchestrator (startup order only — readiness is handled by the degrade-don't-crash posture from Plan 02 Phase 5/6).

### Verification
- [x] `docker compose -f docker-compose.yml config` → **valid** (no errors), shows `name: thorondor`, and confirms only `8080` is published (no other `ports:`).
- [x] `docker compose -f docker-compose.yml --profile bundled-models config` → **valid** and additionally includes the `embedding` and `reranker` services; `docker compose -f docker-compose.yml config` (no profile) **includes the core services and excludes only** the model services.

---

## Phase 2 — Environment template + SearXNG configuration
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `.env.example` enumerating every var from doc 03 §4 with safe placeholders and the documented defaults: chunker (`EMBEDDING_ENDPOINT`, `EMBEDDING_MODEL`, `CHUNKER_MAX_SEGMENTS_DP=10000`, `REWARD_CACHE_MAX_SIZE=100000`) and orchestrator (`RERANKER_ENDPOINT`, `RERANKER_MODEL`, `LLM_ENDPOINT`, `LLM_MODEL`, `MAX_URLS=6`, `CRAWL_CONCURRENCY=4`, `CRAWL_TIMEOUT_S=15`, `DEFAULT_TOKEN_BUDGET=4000`, `CACHE_BACKEND=memory`, `REDIS_URL`, `DOMAIN_BLOCKLIST`). No real secrets.
- [x] Add a commented `bundled-models` block to `.env.example` pointing the BYO vars at the bundled servers (doc 03 §3): `EMBEDDING_ENDPOINT=http://embedding:80`, `EMBEDDING_MODEL=BAAI/bge-m3`, `RERANKER_ENDPOINT=http://reranker:80`, `RERANKER_MODEL=BAAI/bge-reranker-v2-m3`.
- [x] Create `searxng/settings.yml` that (a) **enables the JSON response format** and (b) selects a sensible engine set, per doc 03 §6. Add a comment showing where to plug an API-backed engine (e.g. Brave Search API key) for agent-loop reliability, marked as an optional operator choice (doc 05 §5).
- [x] Mount note: confirm the compose `searxng` volume is `:ro` so SearXNG stays an unmodified, configured-only opaque dependency (the AGPL boundary, doc 05 §3).

### Verification
- [x] `docker compose -f docker-compose.yml config` → **valid** with `.env.example` defaults represented through Compose substitutions.
- [ ] Bring up only SearXNG (`docker compose up -d searxng`) and `curl -s 'http://localhost:PORT/search?q=test&format=json'` from inside the network (or a temporarily published port) → **returns JSON** (proves the JSON format is enabled). Tear it back down.

---

## Phase 3 — End-to-end smoke
**Status:** in_progress
**Kind:** mixed

### Tasks
- [x] Create `scripts/deploy.sh` and `scripts/deploy.ps1`: validate Compose config, build, start the selected profile (`bundled-models` by default), poll `/healthz`, then run the smoke script unless skipped.
- [x] Create `scripts/smoke.sh` and `scripts/smoke.ps1`: brings up `docker compose --profile bundled-models up -d`, polls `http://localhost:8080/healthz` until every dependency reports reachable (bounded retries), then issues the doc-04 §1 request:
  ```bash
  curl -s localhost:8080/search -H 'content-type: application/json' \
    -d '{"query":"what changed in the EU AI Act timeline in 2025","token_budget":4000}'
  ```
  and asserts the response has non-empty `passages`, a `citations` array, `stats.reranked == true`, and `tokens_returned <= 4000`. Exit non-zero on any failed assertion.
- [x] Document in the script header that the bundled model servers want a GPU; note the CPU-image fallback for low-volume/dev (doc 03 §7) so the smoke run is reproducible on a dev box.

### Verification
- [ ] `bash scripts/smoke.sh` → **exit 0**: `/healthz` goes all-green, the live `/search` returns reranked, cited, budget-fit passages (real proof the whole pipeline — discovery → crawl → chunk → rerank → assemble — works against live SearXNG + Crawl4AI + bundled models).
- [ ] An MCP client pointed at `http://localhost:8080/mcp` (doc 04 §2 config) lists a `web_search` tool and a call returns the `{passages, citations}` shape.
- [ ] Negative path: set `DOMAIN_BLOCKLIST` to the domains the query would hit, re-run, and confirm a graceful empty/degraded response with a `reason` (not a crash).

Verification note: PowerShell script syntax was checked with the PowerShell
parser. Bash syntax could not be checked because `bash.exe` routes to WSL and no
WSL distribution is installed. Live build/smoke remains blocked until Docker
Desktop's `dockerDesktopLinuxEngine` is running.

---

## Phase 3A — Windows llama.cpp local workflow
**Status:** in_progress
**Kind:** mixed

### Tasks
- [x] Add `docker-compose.llamacpp.yml` as an override that keeps the core stack unchanged and swaps the `embedding` and `reranker` services to `ghcr.io/ggml-org/llama.cpp:server`.
- [x] Configure the chunker to call `http://embedding:8080/v1/embeddings` and the orchestrator to call `http://reranker:8080/reranking` with `/health` readiness checks under the `llamacpp-models` profile.
- [x] Add `.env.llamacpp.example` for GGUF model paths, aliases, context sizes, and the llama.cpp image tag.
- [x] Extend `scripts/deploy.ps1` and `scripts/smoke.ps1` to accept multiple Compose files and env files.
- [x] Add `scripts/deploy-llamacpp.ps1` for Windows: create `.env`/`.env.llamacpp` from examples when missing, validate referenced `/models/*.gguf` files exist under `models\`, then delegate to the shared deploy script.
- [x] Add root `README.md` covering Windows llama.cpp startup, REST, MCP, development checks, configuration, and troubleshooting.
- [x] Add `.agents/skills/thorondor-web-search/SKILL.md` so agents have a repo-local workflow for using Thorondor.
- [x] Narrow `.gitignore` so `.agents/skills/**` is trackable while `.env.llamacpp` and `models/` stay ignored.

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest semantic-chunking-service\tests orchestrator\tests -v -s -p no:cacheprovider` -> **PASS**: 64 passed, 1 FastAPI/TestClient deprecation warning.
- [x] PowerShell parser check for `scripts/deploy.ps1`, `scripts/smoke.ps1`, and `scripts/deploy-llamacpp.ps1` -> **PASS**.
- [x] `docker compose -f docker-compose.yml config --services` -> **valid**, default services are `crawl4ai`, `chunker`, `searxng`, `orchestrator`.
- [x] `docker compose --env-file .env.example --env-file .env.llamacpp.example -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config --services` -> **valid**, includes `embedding` and `reranker`.
- [x] Rendered Compose config shows only the orchestrator `8080` port is published; llama.cpp model servers remain internal.
- [ ] Live `.\scripts\deploy-llamacpp.ps1` smoke -> blocked until Docker Desktop's `dockerDesktopLinuxEngine` daemon is running and GGUF model files exist under `models\`.

---

## Phase 4 — Licensing, attribution & Quickstart validation
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Add the first-party `LICENSE` (permissive — MIT/Apache-2.0 per doc 05 §1; confirm choice with the owner before committing).
- [ ] Credit **Chroma Research** for the ClusterSemanticChunker algorithm in the repo README and as a header comment in `chunking/cluster_semantic.py` (doc 05 §2) — do not represent the algorithm as novel.
- [ ] Add a `NOTICE`/attribution for Crawl4AI (Apache-2.0, public upstream Docker image consumed over HTTP, no vendored source) and a short **SearXNG AGPL-3.0 boundary** note in the README pointing to doc 05 §3 (run unmodified, configured-only).
- [ ] Pin the bundled model-server image **tags** in `docker-compose.yaml` and record, next to each, that its `LICENSE` for that tag was verified (doc 05 §4 — TEI license drift; Infinity/vLLM as permissive alternatives).
- [ ] Walk the README Quickstart top-to-bottom on a clean checkout; fix any command that no longer matches the built artifacts.

### Verification
- [ ] Fresh-clone dry run: `cp .env.example .env` → `docker compose --profile bundled-models up` → the Quickstart `curl` returns passages **exactly as the README claims** (README is accurate, not aspirational).
- [ ] `LICENSE`, Chroma attribution, Crawl4AI NOTICE, and the SearXNG AGPL note are all present; bundled image tags are pinned (no `:latest` in a published bundle) with a verified-license note.
