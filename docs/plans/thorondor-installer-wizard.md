# Thorondor Installer + TUI Configurator

## Goal

Give Thorondor an `imladris`-style operator experience: a **single-line installer**
that exposes two console commands, and a **stateful, full-screen Textual TUI**
(`thorondor`) that loads the current repo/env state, lets the operator inspect and
edit it in place, validates model endpoints live, writes complete and valid env
files, and brings up the existing Docker stack — without hand-editing env files or
memorizing `docker compose` invocations.

The base Docker architecture stays unchanged (three Compose files, SSRF egress
proxy, `internal: true` network, every invariant intact). The TUI *drives* the
stack; it does not restructure the base files. The only topology change is the
D2 generated overlay, applied only when external/host embedding requires it.

Two installed commands (mirroring imladris's `imladris` + `imladris-mcp`):

- `thorondor` — the Textual configurator dashboard / deployer.
- `thorondor-mcp` — a **thin proxy** MCP stdio server that forwards `web_search`
  to the running stack's `POST /v1/search`. This is the "MCP runs natively by
  default" path. The Dockerized HTTP `/mcp` (already served at
  `orchestrator/app.py:158`) remains the offered alternative.

## Open decisions for review (resolve before implementation)

These genuinely change the build. Recommendations are given; please confirm or
override during review.

### D1 — Clone model (headline decision)

How does the TUI reach the Compose files, `searxng/` config, and first-party
image builds?

- **A — Repo-local (recommended for v1).** The operator clones the repo, then
  the one-liner installs the global `thorondor`/`thorondor-mcp` commands. `thorondor`
  is run from the repo root: it writes the repo-root `.env`/`.env.llamacpp` and
  drives the **existing** `docker-compose.yml` (+ `llamacpp` overlay), building
  first-party images from source as today.
  - *Pros:* faithful to "keep Docker as-is"; reuses the guard-covered Compose
    files; smallest new surface; no coupling to the GHCR release cadence.
  - *Cons:* still requires a `git clone`; the global command operates on the
    current directory; first run builds images (slower).
- **B — Zero-clone published images.** `thorondor` ships a self-contained,
  image-only `docker-compose.standalone.yml` + `searxng/` config + env templates
  as package data, writes a working directory (`~/.thorondor/`), and runs the
  stack from the **published GHCR images** (`ghcr.io/feanorscodesl/thorondor-*`)
  — no clone, no source build.
  - *Pros:* truest imladris "install one line, run anywhere" UX.
  - *Cons:* new `docker-compose.standalone.yml` to maintain and guard; couples to
    the release-image pipeline; more package-data shipping.

**Recommendation: A for v1, with B as a clean additive follow-up** (B only adds a
standalone compose asset + package-data shipping on top of the same TUI).
Phases below are written for **A**; spots that differ under **B** are flagged
`[D1=B]`.

### D2 — Reachability of external / host-run model endpoints

The `chunker` is the **only** first-party service that is internal-only
(`docker-compose.yml:93-94` inherits `networks: [internal]` from the anchor at
`:5` and never adds `egress`; `internal` is `internal: true` at `:163-164`), and
it is exactly the service that calls the embedding endpoint (`EMBEDDING_ENDPOINT`
at `:102`). Consequences:

- An external/BYO embedding endpoint — **remote or host-run** — is unreachable
  from the chunker as the stock topology stands. An `internal: true` network has
  no route off-bridge, and `extra_hosts` only adds a name→IP mapping, not a route.
  This looks like a **pre-existing gap** in the documented "BYO embedding"
  quickstart, not something this work introduces.
- The reranker side is fine: it is called by the `orchestrator`, which is on
  `egress`. A host-run reranker still needs the name mapping below.

**Fix — a generated, opt-in overlay; base Compose files untouched.** When the
chosen embedding endpoint is external or host-rewritten, the deploy adds
`docker-compose.host-endpoints.yml`, which:

- adds `egress` to the `chunker` network list (so it can reach an off-box
  embedding server at all); and
- for a host-rewritten endpoint only, adds
  `extra_hosts: ["host.docker.internal:host-gateway"]` to `chunker` (embedding)
  and `orchestrator` (reranker) so the name resolves on **Linux** (it is
  automatic on Docker Desktop).

In the bundled-models / llamacpp modes the embedding server is in-Docker on the
`internal` network, so the overlay is **not** applied and the chunker stays
internal-only.

- **Recommendation: yes, mode-aware.** Granting the chunker `egress` is a
  deliberate posture change (it can then reach arbitrary external hosts), so it
  is applied only in the external/host embedding modes, surfaced in the dashboard
  and save summaries, and gated behind a Linux host-stub reachability test
  (Phase 7). If
  that test shows the topology cannot reach the host, document the limitation
  instead.

### D3 — Fate of `deploy.ps1` / `deploy.sh` / `smoke.*`

The TUI subsumes their logic (config → `up` → health poll → smoke) in one
cross-platform Python path.

- **Recommendation: keep** `deploy.{ps1,sh}` + `smoke.{ps1,sh}` as the
  **non-interactive / CI** entrypoints; the TUI becomes the **interactive**
  entrypoint. No duplication beyond what already exists.

## References

Repo files:

- `docker-compose.yml`, `docker-compose.llamacpp.yml`, `docker-compose.production.yml`
  — the stack the TUI drives; service env contract.
- `.env.example`, `.env.llamacpp.example` — canonical key set the TUI must
  emit complete (the settings loader raises on any missing key).
- `orchestrator/mcp_server.py` — current in-process MCP (`web_search` →
  `run_search`, stdio `main()` at line 90); the proxy must match its tool
  signature.
- `orchestrator/app.py:158` — existing Dockerized HTTP `/mcp` mount.
- `orchestrator/settings.py` — required backend URL keys (`SEARXNG_URL`,
  `CRAWL4AI_URL`, `CHUNKER_URL`, `RERANKER_ENDPOINT`).
- `orchestrator/requirements.txt` — dependency pins to match: `mcp==1.27.2`,
  `httpx==0.28.1`, `pydantic==2.13.4`.
- `scripts/deploy.ps1`, `scripts/deploy.sh`, `scripts/smoke.ps1` — health-poll +
  SEARXNG_SECRET + smoke logic to port to Python.
- `scripts/check-release-guard.sh`, `.github/workflows/release-guard.yml` — guards
  to keep in parity.

imladris templates (`/Users/sergio/git/feanorsCode/imladris`) to mirror:

- `pyproject.toml` — `[project.scripts]`, lean deps, `packages.find`.
- `scripts/install.sh`, `scripts/install.ps1` — uv bootstrap + `uv tool install git+`.
- `docs/plans/configurator-tui.md` — Textual dashboard shape, navigation rules,
  gold-on-black branding, immediate validated writes, and sub-screen structure to
  adapt for Thorondor.
- `orchestrator/cli/app.py` — CLI dispatch shape (TUI for no-arg launch; plain
  `doctor`/`--help` paths).
- `orchestrator/cli/probe.py` — token-safe live endpoint probing + status mapping.
- `orchestrator/cli/secrets.py` — merge-not-clobber `.env` writer at `0600`.
- `orchestrator/cli/harness/{claude_code,codex,opencode,common}.py` — harness wiring.

## Build & run

- **Containerized:** runtime stack — yes (unchanged). The TUI/installer itself
  — no (host `uv tool`).
- **Build command:** `uv tool install .` (install the tool from source);
  `docker compose --env-file .env -f docker-compose.yml --profile bundled-models config`
  (validate the stack the TUI emits).
- **Test command:**
  `python -m pytest semantic-chunking-service/tests orchestrator/tests thorondor_cli/tests -v`
- **Manual run:** `thorondor` launches the full-screen TUI in a real terminal;
  `thorondor doctor` and `thorondor --help` stay plain-text commands.

## Implementation note — 2026-06-18

Initial implementation is now in the repo:

- Added `pyproject.toml`, `thorondor_cli/`, package templates, managed runtime
  assets, installer scripts, env/state/probe/deploy helpers, native
  `thorondor-mcp`, harness writers, and a first functional Textual
  dashboard/screen set.
- Added focused tests under `thorondor_cli/tests` for CLI dispatch, env writes,
  probes, state/build helpers, deploy command assembly, harness writers, MCP
  proxy forwarding, and dashboard rendering.
- Updated README, deployment docs, notices, CI workflows, and release guards.

Verification run locally:

- `PYTHON=/opt/homebrew/bin/python3.11 bash scripts/check-release-guard.sh` — pass.
- `sh -n scripts/install.sh` — pass; installer is compatible with the requested
  `curl ... | sh` invocation.
- `/opt/homebrew/bin/python3.11 -m compileall -q thorondor_cli` — pass.
- Direct Python smoke checks for `thorondor --help`, env/overlay persistence, and
  Codex harness rewiring — pass.
- `UV_TOOL_DIR=/tmp/thorondor-tool-dir UV_TOOL_BIN_DIR=/tmp/thorondor-tool-bin
  UV_CACHE_DIR=/tmp/thorondor-uv-cache uv tool install --force --python
  .venv/bin/python .` — pass; installed `thorondor doctor` and `thorondor --help`
  run from `/tmp/thorondor-tool-bin`.
- `THORONDOR_HOME=/tmp/thorondor-installed-home
  /tmp/thorondor-tool-bin/thorondor doctor` — pass; creates the managed runtime
  directory without a checkout.
- `.venv/bin/python -m pytest thorondor_cli/tests -v` — pass (`34 passed`).

Remaining verification not yet done:

- Full repo regression command
  `python -m pytest semantic-chunking-service/tests orchestrator/tests thorondor_cli/tests -v`.
- Raw-GitHub one-line installer check after these changes are on `main`
  (`curl -LsSf https://raw.githubusercontent.com/FeanorsCodeSL/thorondor/main/scripts/install.sh | sh`).
- Manual TTY walkthrough, live Docker deploy/smoke, and D2 Linux host reachability proof.

## Invariants to preserve (must not regress)

- No persistent index / vector store / cache; restart stays non-destructive.
- SearXNG remains an unmodified, digest-pinned AGPL image (never built).
- Crawl4AI remains a digest-pinned upstream image consumed over HTTP, egressing
  only through the SSRF proxy; `internal: true` network preserved.
- Model deps are operator-configured HTTP endpoints; all three modes (BYO /
  bundled-models / llamacpp) stay viable.
- The emitted `.env` contains **every** key the loader requires (optional keys
  blank-but-present); no Compose interpolation fallbacks.
- No `:latest`; first-party images non-root and pinned.
- No endpoint auth/TLS/rate-limiting introduced; expose posture unchanged.
- Commit only when the user explicitly asks; no AI co-authors.

## New components (package layout)

A **self-contained, lean** CLI package — it must NOT import `orchestrator/`
(importing it would drag the pipeline's heavy deps onto the host). Shared facts
(the required-key set) are loaded at runtime from env templates bundled under
`thorondor_cli/templates/` via `importlib.resources` (preferring a repo-root copy
when present under D1=A), never from a Python import.

```
pyproject.toml                 # NEW — root; defines the `thorondor` distribution
thorondor_cli/
  __init__.py
  app.py                       # CLI dispatch: no-arg TUI, plus `doctor`/`--help`
  project.py                   # resolve/validate project dir (--project-dir; D1=A guard)
  envfile.py                   # read / merge-not-clobber / write .env at 0600
  secret.py                    # SEARXNG_SECRET generation (matches deploy.ps1)
  catalog.py                   # preset embedding/reranker/LLM servers + defaults
  probe.py                     # token-safe live probes + localhost→host rewrite
  deploy.py                    # docker compose build/up + /healthz poll + smoke (Python)
  state.py                     # load draft config, compute issues, persist env changes
  mcp_proxy.py                 # `thorondor-mcp`: stdio proxy → POST /v1/search
  harness/                     # MCP wiring for Claude Code / Codex / OpenCode
  tui/
    app.py                     # ThorondorApp (Textual)
    screens/
      dashboard.py             # current stack/env status + action bar
      model_mode.py            # BYO / bundled-models / llamacpp mode screen
      endpoint_form.py         # embedding/reranker/LLM endpoint editor + probes
      search_settings.py       # crawl/search/safety settings editor
      validate.py              # endpoint + compose validation screen
      deploy.py                # compose build/up + health/smoke progress screen
      harness.py               # MCP delivery + harness wiring
    thorondor.tcss             # gold-on-black theme aligned with imladris
  templates/                   # bundled .env.example + .env.llamacpp.example (package data)
  tests/                       # pytest + respx
scripts/install.sh             # NEW — uv bootstrap + uv tool install git+
scripts/install.ps1            # NEW — Windows equivalent
# generated at deploy time (not committed): docker-compose.host-endpoints.yml (D2)
```

Runtime deps for the package: `textual>=8.2.7,<9.0` (full-screen TUI),
`rich>=15.0,<16.0` (formatting used outside Textual too), `httpx==0.28.1`,
`pydantic==2.13.4`, `mcp==1.27.2`.
Dev/test deps for the package: `pytest`, `respx`, `ruff`.

## UI structure (target)

This mirrors `/Users/sergio/git/feanorsCode/imladris/docs/plans/configurator-tui.md`
in interaction model and visual tone, but the content is Thorondor-specific. Exact
column widths and widget choices are implementation details; the visible
information, navigation, and action model are fixed by this plan.

### Visual style & branding

- **Toolkit:** Textual full-screen app. `thorondor --help` and `thorondor doctor`
  remain plain-text paths; no-arg `thorondor` launches the TUI.
- **Palette:** same terminal brand direction as imladris: brand gold on near-black,
  dim gold for secondary text, brighter gold for focus/selection. Use a `.tcss`
  theme, not ad hoc inline styling.
- **Header band:** compact, not a splash. It carries the product name, purpose,
  company, and logo badge in one row:

```
┌─◈───────────────────────────────────────────────────────────────────────────◈──┐
│   thorondor   ·  Search Stack Configurator             Fëanor's Code  ⟦ O(log n) ⟧ │
└─◈───────────────────────────────────────────────────────────────────────────◈──┘
```

- **Ornamentation:** box-drawing/divider glyphs are allowed in the same restrained
  style as imladris; they must not make endpoint/status tables harder to scan.
- **Responsive rule:** the dashboard must remain usable at `80x24`. At narrow
  widths, abbreviate long URLs/model names and collapse the header to product name
  plus one compact brand mark before allowing tables or action labels to overlap.
  At `120x36`, show fuller URL/model/detail columns.
- The imladris reference image's generic menu items are **style filler only**. The
  real Thorondor action bar is the stack dashboard below.

### Screen hierarchy & navigation

```
ThorondorApp  (full-screen Textual; launched by `thorondor`)
│
│  Esc ──▶ pop back to Dashboard (discard in-progress edit)
│  Every Save/Apply ──▶ validate → write .env / .env.llamacpp / generated overlay
│                      → re-read from disk → re-render Dashboard
│
└── ① DashboardScreen  ◀──────────────── (home; always returned to)
    │   project/env paths · mode/profile · dependency table · issues/status
    │   deploy status · MCP/harness status · action bar
    │
    ├──▶ ② ModelModeScreen
    │      ├ BYO endpoints
    │      ├ bundled-models (TEI containers)
    │      └ llamacpp (GGUF files under models/)
    │          (Save writes complete env values and any .env.llamacpp changes)
    │
    ├──▶ ③ EndpointFormScreen
    │      ├ component ▼ (embedding · reranker · optional LLM planner)
    │      ├ catalog preset ▼ (TEI · llama.cpp server · Infinity · vLLM · Ollama · OpenAI · Custom)
    │      ├ base URL · model · token (password) · health/rerank path where relevant
    │      ├ host rewrite preview + D2 overlay warning when applicable
    │      └ [Fetch models] · [Test connection] · [Save] · [Cancel / Esc]
    │           (all fields editable; probes run in workers; token never displayed)
    │
    ├──▶ ④ SearchSettingsScreen
    │      ├ ORCHESTRATOR_HOST/PORT · DEFAULT_TOKEN_BUDGET
    │      ├ search profile budgets/caps · MAX_URLS · max passages
    │      ├ ALLOWLIST_ONLY · DOMAIN_ALLOWLIST · DOMAIN_BLOCKLIST
    │      └ crawl robots/freshness/safety defaults
    │
    ├──▶ ⑤ ValidateScreen
    │      └ endpoint probes + env completeness + docker compose config
    │          (shows exact failing component and suggested action)
    │
    ├──▶ ⑥ DeployScreen
    │      └ config → build/pull → up -d → /healthz poll → smoke search
    │          (streamed progress log; failure shows `docker compose ps` + logs hint)
    │
    ├──▶ ⑦ HarnessScreen
    │      └ native stdio vs Docker HTTP MCP delivery; wire Claude Code / Codex / OpenCode
    │
    └──▶ ⑧ Exit
```

### Dashboard — fully configured

Status is green only when `compute_issues` is empty and the last validation/deploy
status is healthy. The dashboard always shows the on-disk truth after re-reading
the env files; there is no unsaved-changes state.

```
┌─◈──────────────────────────────────────────────────────────────────────────◈──┐
│   thorondor   ·  Search Stack Configurator            Fëanor's Code  ⟦ O(log n) ⟧ │
│ project: ~/git/feanorsCode/thorondor     env: .env (0600)   mode: BYO endpoints │
├────────────────────────────────────────────────────────────────────────────────┤
│ Components                                                                        │
│ component      endpoint / model                              status      notes    │
│ ──────────────────────────────────────────────────────────────────────────────── │
│ searxng        http://searxng:8080                          configured  internal │
│ crawl4ai       http://crawl4ai:11235                        configured  proxied  │
│ chunker        http://chunker:8000                           configured  internal │
│ embedding      http://host.docker.internal:8082 · bge-m3     ✓ probed    overlay  │
│ reranker       http://host.docker.internal:8081 · bge-rerank ✓ probed    host-gw  │
│ llm planner    —                                             disabled    optional │
├────────────────────────────────────────────────────────────────────────────────┤
│ Search   default budget 4000 · robots true · allowlist off · max_urls 6          │
│ Status   ✓ Ready — env complete · endpoints probed · compose config valid        │
│ Deploy   last smoke passed · /healthz ok                                         │
│ MCP      stdio thorondor-mcp ✓    http /mcp available    codex ✓ claude-code ✗   │
└────────────────────────────────────────────────────────────────────────────────┘
  [m] Mode   [e] Endpoints   [s] Search/crawl   [v] Validate   [d] Deploy   [w] MCP   [q] Quit
  ↑/↓ select · Enter = open selected · Esc = back
```

### Dashboard — nothing configured yet

When `.env` is missing or incomplete, the green Status line is replaced by an
issue list. Each issue names the action that fixes it.

```
│ Components   — not configured yet                                                │
│ Status       ⚠ 5 issues                                                          │
│   • Missing .env — choose Mode or Endpoints to seed from template                 │
│   • SEARXNG_SECRET blank — generated on save                                     │
│   • Embedding endpoint not validated → Endpoints                                  │
│   • Reranker endpoint not validated → Endpoints                                   │
│   • No MCP harness wired → MCP                                                    │
```

---

## Phase 1 — Packaging & one-line installer
**Status:** in_progress
**Kind:** logic

Progress: implementation is present and covered by package tests; isolated
`uv tool install .` verification passes and the installed command creates a
managed runtime directory outside the checkout. shellcheck/PSScriptAnalyzer and
raw-GitHub one-line installer validation remain. The Unix installer syntax is
validated with `sh -n` because the documented one-liner pipes into `sh`.

### Tasks
- [x] Add root `pyproject.toml`: name `thorondor`, `requires-python >=3.13`,
      runtime deps `textual>=8.2.7,<9.0`,
      `rich>=15.0,<16.0`, `httpx==0.28.1`, `pydantic==2.13.4`,
      `mcp==1.27.2`; `[project.scripts]` → `thorondor = "thorondor_cli.app:main"`,
      `thorondor-mcp = "thorondor_cli.mcp_proxy:main"`;
      `[tool.setuptools.packages.find]` include `thorondor_cli*`, exclude
      `thorondor_cli.tests*`; ruff config mirroring imladris.
- [x] Bundle env templates as package data: copy `.env.example` and
      `.env.llamacpp.example` into `thorondor_cli/templates/`, declare them via
      `[tool.setuptools.package-data]` (or `MANIFEST.in`), and load them with
      `importlib.resources` — root-level dotfiles do **not** become package data
      by implication. A Phase 7 guard check keeps them in sync with the repo-root
      copies.
- [x] Create `thorondor_cli/__init__.py` and a minimal `app.py:main` (help text +
      argument dispatch stub) so the tool installs and runs.
- [x] Add `scripts/install.sh` + `scripts/install.ps1` mirroring imladris:
      bootstrap `uv` if absent, then
      `uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor`;
      print the two installed commands and the "run `thorondor` next" hint.
- [x] Add dev deps (`pytest`, `respx`, `ruff`) and wire `thorondor_cli/tests` into
      the repo test command.
- [x] `[D1=B]` ship image-based managed Compose + `searxng/` as package data.

### Verification
- [x] `uv tool install .` succeeds; `thorondor` and `thorondor-mcp` resolve on PATH
      (verified in isolated `/tmp` tool/bin dirs).
- [x] New test `test_app_cli.py`: `app.main(["--help"])` returns 0 and prints both
      command names + usage; unknown subcommand returns non-zero.
- [ ] `install.sh`/`install.ps1` pass shellcheck/PSScriptAnalyzer and contain the
      exact `uv tool install git+…` invocation (assert via a content test; network
      install validated manually).
- [x] Install-outside-repo test: from a temp dir with no repo checkout, the
      bundled templates and managed runtime assets load via `importlib.resources`
      and initialize a usable Thorondor home (proves package-data shipping, not
      cwd reliance).

## Phase 2 — Env config model (read / merge / write)
**Status:** completed
**Kind:** logic

### Tasks
- [x] `thorondor_cli/envfile.py`: `read_env(path)->dict`,
      `write_env(path, values, *, mode=0o600)` with **merge-not-clobber** and value
      quoting (port imladris `secrets.py`), `seed_from_example(example_path)->dict`
      returning every key with example defaults.
- [x] Guarantee completeness: the written `.env` contains every key present in
      `.env.example` (blank if optional, value if collected); keys are never dropped.
- [x] `thorondor_cli/secret.py`: `generate_searxng_secret()` — 32 random bytes,
      base64, strip `+/=` (parity with `deploy.ps1`'s `New-SecretValue`); generate
      only when the existing value is blank.
- [x] Support a second target `.env.llamacpp` seeded from `.env.llamacpp.example`.

### Verification
- [x] `test_envfile.py`:
      - round-trip preserves all keys; re-run preserves an existing non-blank
        `SEARXNG_SECRET` and fills a blank one;
      - values containing spaces/`#` are quoted and re-read intact;
      - `seed_from_example` yields the full required-key set;
      - missing target file is created at mode `0600` (POSIX assert).
- [x] `test_secret.py`: generated secret is non-empty, contains no `+/=`, differs
      across calls.

## Phase 3 — Endpoint catalog + live probes
**Status:** completed
**Kind:** logic

### Tasks
- [x] `thorondor_cli/catalog.py`: preset entries per dependency kind
      (`embedding` | `reranker` | `llm`) — TEI, llama.cpp server, Infinity, vLLM,
      Ollama, OpenAI — each with `display_name`, `kind`, `default_base_url`,
      `default_model`, `rerank_path`/`health_path` where relevant, and
      `needs_token` default.
- [x] `thorondor_cli/probe.py` (token-safe — token sent as bearer, never logged or
      returned):
      - `list_models(base_url, token)` → ids or `None` (free-text fallback);
      - `probe_embedding(base_url, model, token)` → `/v1/embeddings` minimal call;
      - `probe_reranker(base_url, health_path, rerank_path, model, token)` →
        health probe **plus** a minimal real rerank `POST {base}{rerank_path}`
        with the production body shape
        `{"query": "ping", "documents": ["ping"], "model": model}` (matches
        `orchestrator/clients/reranker_client.py:63-67`, which requires HTTP 200);
        a health-only probe can pass while real searches fail on a bad/blank model;
      - `probe_llm(base_url, model, token)` → `/v1/chat/completions` ping
        (port imladris `check_member`);
      - status enum `ok | auth_error | unreachable | model_not_found | other`;
      - `rewrite_host_for_docker(url)` → `localhost`/`127.0.0.1` →
        `host.docker.internal`, returning `(new_url, rewritten: bool)` for display;
        only applied when the run mode is Docker.

### Verification
- [x] `test_probe.py` (respx): each probe maps 200→ok, 401/403→auth_error,
      404 / 400-with-"model"→model_not_found, transport error→unreachable,
      5xx→other; the reranker probe sends the production `{query, documents, model}`
      body and flags a bad/blank model (400/404 from the rerank call) as
      `model_not_found`, not `ok`; `list_models` parses `{ "data": [{"id":…}] }`
      and a bare list, returns `None` on malformed/error; tokens never appear in
      returned `detail`.
- [x] `test_host_rewrite.py`: `localhost:8082`/`127.0.0.1` rewritten with
      scheme/port/path preserved; a public host is unchanged.

## Phase 4 — Proxy-mode `thorondor-mcp`
**Status:** completed
**Kind:** logic

### Tasks
- [x] `thorondor_cli/mcp_proxy.py`: `FastMCP("thorondor")` with a `web_search` tool
      whose signature/docstring match `orchestrator/mcp_server.py` exactly, but
      whose body POSTs the (non-None) args to
      `${THORONDOR_BASE_URL:-http://localhost:8080}/v1/search` via httpx and
      returns the JSON dict unchanged. Optional `THORONDOR_API_KEY` bearer for
      operators who front the orchestrator with auth. `main()` →
      `mcp.run(transport="stdio")`.
- [x] Generous timeout aligned with the `deep` profile; on an unreachable/5xx
      orchestrator, return a clear structured error payload (no crash, no stack
      trace to the harness).
- [x] No import of `orchestrator/` — keep host deps lean.

### Verification
- [x] `test_mcp_proxy.py` (respx): tool forwards only non-None args; returns the
      mocked `SearchResponse` dict verbatim; unreachable orchestrator yields the
      structured error, not an exception.
- [x] Signature-parity test: `web_search` parameter names/types match
      `orchestrator/mcp_server.py`.
- [x] `main` importable and constructs the stdio server (smoke).

## Phase 5 — Textual TUI dashboard + configuration screens
**Status:** in_progress
**Kind:** mixed

Progress: first functional dashboard and sub-screens exist, with the elven
gold-on-black ornamental style and responsive dashboard tests passing. Endpoint
catalog selection/fetch workers, full ValidateScreen probes/compose execution, worker
streaming, richer Pilot workflows, and manual TTY proof remain.

### Tasks
- [x] `thorondor_cli/app.py`: thin CLI shell. No-arg `thorondor` launches
      `ThorondorApp(...).run()`; `thorondor doctor` and `thorondor --help` stay
      plain-text. The shell resolves `--project-dir` before launching the TUI.
- [x] `thorondor_cli/state.py` pure layer:
      `load_draft(project_dir) -> Draft` reads repo-root env files when present
      (falling back to bundled templates), tolerates missing/partial env files,
      records `.env` / `.env.llamacpp` paths, token-present booleans, selected
      mode/profile, generated-overlay need, last-known validation/deploy status,
      and parse/type errors without crashing the TUI.
- [ ] `compute_issues(draft, harness_status) -> list[Issue]`: missing project-dir
      requirements; missing required env keys; blank `SEARXNG_SECRET`; invalid
      numeric/bool env values; BYO endpoint not probed or failed; external/host
      embedding needs the D2 overlay; missing GGUF files in llamacpp mode; Docker
      unavailable or compose config failed; no MCP harness wired. Each issue has a
      short label and the action that fixes it (`Mode`, `Endpoints`,
      `Search/crawl`, `Validate`, `Deploy`, `MCP`, or `Manual repair`).
- [x] Pure helpers (testable core):
      `build_env_values(answers) -> dict` produces the complete env mapping;
      `compose_overlays(answers) -> list[str]` returns extra `-f` overlays —
      including `docker-compose.host-endpoints.yml` when the embedding endpoint is
      external/host (D2) — plus a flag so the dashboard warns that the chunker
      gains `egress`; `persist_env_changes(...)` validates then writes `.env` and
      `.env.llamacpp` at `0600`, preserving existing non-blank secrets.
- [x] `thorondor_cli/tui/app.py` + `thorondor.tcss`: Textual `ThorondorApp` with
      the gold-on-black theme, compact branded header, app-level bindings, and
      responsive layout rules from **UI structure**.
- [x] `DashboardScreen`: render project/env paths, selected mode/profile,
      dependency table (SearXNG, Crawl4AI, chunker, embedding, reranker, optional
      LLM), search/crawl summary, issues/status panel, last deploy/smoke status,
      MCP/harness status, and the action bar:
      `[m] Mode [e] Endpoints [s] Search/crawl [v] Validate [d] Deploy [w] MCP [q] Quit`.
- [x] `ModelModeScreen`: choose BYO endpoints / bundled-models (TEI) / llamacpp
      (GGUF). Saving writes complete mode-specific env values. For llamacpp,
      validate GGUF paths using the same `/models/...` to `models/...` conversion
      as `deploy-llamacpp.ps1`; surface guidance instead of writing a half-valid
      `.env.llamacpp`.
- [ ] `EndpointFormScreen`: edit embedding, reranker, and optional LLM planner.
      Fields: catalog preset, base URL, model, token (`Input(password=True)`),
      rerank/health path where relevant, host rewrite preview, and D2 overlay
      warning. `Fetch models` and `Test connection` run via Textual workers so the
      UI does not block. Tokens are sent as bearer values, never rendered.
- [ ] `SearchSettingsScreen`: edit Thorondor core settings:
      `ORCHESTRATOR_HOST/PORT`, `DEFAULT_TOKEN_BUDGET`, search profile budgets and
      caps, `MAX_URLS`, `ALLOWLIST_ONLY`, `DOMAIN_ALLOWLIST`, `DOMAIN_BLOCKLIST`,
      `CRAWL_RESPECT_ROBOTS_TXT`, crawl timeout/concurrency, and freshness-related
      defaults. Everything else keeps template defaults unless already customized.
- [ ] `ValidateScreen`: run env completeness checks, endpoint probes, and
      `docker compose ... config` using the same arg builder as deploy. Show exact
      failing component and suggested action; return to Dashboard on `Esc`.
- [ ] `DeployScreen`: call Phase 6 deploy helpers from a worker, stream progress
      (`config`, `build`/`pull`, `up -d`, `/healthz`, smoke), and persist the
      last-known result for the dashboard.
- [ ] `HarnessScreen`: native stdio `thorondor-mcp` vs Docker HTTP `/mcp` choice,
      plus Claude Code / Codex / OpenCode wiring. Applying writes harness config
      and refreshes the dashboard.
- [ ] Navigation rule: `Esc` anywhere in a sub-screen returns to the dashboard and
      discards in-progress edits. Every completed Save/Apply validates and writes
      immediately, then re-reads from disk so the dashboard reflects on-disk truth;
      there is no unsaved-changes state.

### Verification
- [x] logic — `test_tui_state.py`: `load_draft` returns a usable draft for a
      complete `.env`, a missing `.env`, a partial `.env`, malformed numeric/bool
      values, and a missing `.env.llamacpp`; none crash the dashboard layer.
- [x] logic — `test_tui_build.py`: for each mode, `build_env_values` yields a
      complete env dict (every required key present, correct profile, correct
      endpoints, host-rewritten URLs, secret set); BYO-with-token path stores the
      token key; llamacpp-with-missing-GGUF surfaces guidance (raises/returns
      error), not a half-written file. Covered in `test_tui_state.py`.
- [x] logic — `compose_overlays` returns `docker-compose.host-endpoints.yml` for
      external/host embedding and an empty list for bundled-models/llamacpp;
      project-dir validation rejects a directory missing `docker-compose.yml` or
      `searxng/`.
- [x] ui — `test_tui_dashboard.py` with
      `async with ThorondorApp(...).run_test() as pilot`: a complete BYO config
      renders the header (`thorondor`, `Fëanor's Code`, `O(log n)`), project/env
      paths, selected mode, endpoint rows, status, deploy line, and MCP/harness
      line; empty config renders the "nothing configured" issue list.
- [x] ui — layout checks run the dashboard at `80x24` and `120x36`: action keys
      remain visible, long URLs/model names truncate cleanly, and no header/status
      or table text overlaps.
- [ ] ui — Pilot tests cover Mode save, Endpoint test/save with `respx` probes,
      Search/crawl save, `Esc` cancellation with no file change, and Harness apply
      refreshing dashboard status.
- [ ] observable — a scripted Textual run with canned Pilot interactions writes a
      `.env` that **passes `docker compose --env-file .env -f docker-compose.yml
      config`**; assert the dashboard lists the exact files written and the chosen
      mode/endpoints.
- [ ] Documented manual TTY walkthrough of one BYO run (screenshots/log of the
      green-check probe, dashboard status, and deploy progress).

## Phase 6 — Deploy handoff (compose up + health + smoke)
**Status:** in_progress
**Kind:** logic

Progress: D1=A command assembly, health/smoke helpers, and generated D2 overlay are
implemented and unit-tested. Live Docker deploy/smoke and richer progress/log handling
remain.

### Tasks
- [x] `thorondor_cli/deploy.py` (cross-platform, no PowerShell/Bash dependency):
      assemble the `docker compose` arg vector for the chosen mode — base file +
      `--env-file`(s) + `--profile` + any overlays from `compose_overlays(answers)`
      (e.g. `docker-compose.host-endpoints.yml`), rooted at the resolved
      `--project-dir`. Run `config` → **`build`** (D1=A — parity with
      `deploy.ps1:146`; or `up --build`) → `up -d` → poll
      `GET {host}:{port}/healthz` for ≤120 s (all dependency values `true`;
      degraded fails) → run the smoke search (`POST /v1/search`, assert non-empty
      `passages`, `stats.reranked == true`, `tokens_returned <= token_budget`).
      Stream progress with rich; on failure surface `docker compose ps` + a logs
      hint. Health/search URLs derived from `ORCHESTRATOR_HOST/PORT` (port
      `deploy.ps1`).
- [x] Generate `docker-compose.host-endpoints.yml` (D2) when the embedding
      endpoint is external/host: add `egress` to the `chunker` networks, and —
      host-rewritten only — `extra_hosts: ["host.docker.internal:host-gateway"]`
      to `chunker` + `orchestrator`. The base Compose files stay untouched.
- [ ] `[D1=B]` `pull` instead of `build`: `config` → `pull` → `up -d` with
      `--project-directory ~/.thorondor` against `docker-compose.standalone.yml`
      and the published images.

### Verification
- [ ] `test_deploy.py` (subprocess + httpx mocked): the command sequence is
      `config` → `build` → `up -d` for D1=A and `config` → `pull` → `up -d` for
      D1=B; health-poll passes on all-true, fails on any-false, fails on degraded,
      and handles the timeout path; the compose arg vector for base /
      bundled-models / llamacpp / external-embedding (with the host-endpoints
      overlay) matches expected `-f`/`--env-file`/`--profile`; smoke assertions
      pass on a good `SearchResponse` and fail on each degraded/empty case.
      D1=A coverage is present and passing; D1=B remains pending because D1=A is
      the selected v1 path.

## Phase 7 — Harness wiring, docs, and guard parity
**Status:** in_progress
**Kind:** mixed

Progress: harness writers, docs, notices, CI workflow edits, and release guards are
implemented. GitHub CI logs, D2 Linux host proof, and end-to-end manual deploy proof
remain.

### Tasks
- [x] `thorondor_cli/harness/` (mirror imladris): write the MCP entry for
      Claude Code / Codex / OpenCode — stdio:
      `{ "command": "thorondor-mcp", "env": { "THORONDOR_BASE_URL": "http://localhost:8080" } }`;
      http: `{ "type": "http", "url": "http://localhost:8080/mcp" }`. Best-effort
      detection of an already-wired entry; preserve unrelated entries.
- [x] README: replace/augment the quickstart with the one-liner install +
      `thorondor`; describe the Textual dashboard and its action bar
      (Mode / Endpoints / Search/crawl / Validate / Deploy / MCP); document the
      two `thorondor-mcp` delivery modes; keep the BYO / bundled / llamacpp
      guidance.
- [x] `docs/architecture/deployment.md`: add Textual TUI + installer +
      MCP-delivery section; note `deploy.{ps1,sh}` are now the
      non-interactive/CI path (D3).
- [x] `[D2]` document the external/host embedding reachability behavior and the
      generated `docker-compose.host-endpoints.yml` (chunker gains `egress`;
      host-rewritten adds `extra_hosts: host-gateway` to `chunker`/`orchestrator`);
      note the pre-existing stock-topology gap for off-box embedding.
- [x] CI: update `.github/workflows/release-guard.yml` (and `publish-images.yml`'s
      validate job) to install the CLI package + dev deps and run
      `thorondor_cli/tests` alongside the existing service test dirs.
- [x] Extend `scripts/check-release-guard.{sh,ps1}` + `release-guard.yml`: keep the
      no-`:latest`/digest-pin rules; generate a deterministic
      `docker-compose.host-endpoints.yml` fixture via the same helper used by
      deploy, then `docker compose … config`-validate that overlay; assert the
      bundled `thorondor_cli/templates/*.example` match the repo-root templates;
      validate any new compose asset `[D1=B]`; assert the install scripts pin the
      canonical repo.
- [x] `THIRD-PARTY-NOTICES.md`: add `textual` and `rich`.

### Verification
- [x] logic — `test_harness.py` (mirror imladris `test_cli_harness`): correct JSON
      for stdio vs http; idempotent re-wire; unrelated MCP entries preserved.
- [ ] CI — `release-guard.yml` and `publish-images.yml` install the CLI deps and
      collect `thorondor_cli/tests` (job logs show the new test dir).
- [x] observable/docs — README and `deployment.md` contain the new
      install/Textual-dashboard/MCP sections (heading + command checks);
      `check-release-guard` passes; `release-guard.yml` `docker compose … config`
      validations pass for every compose file (including the generated
      host-endpoints overlay fixture). Verified locally with
      `PYTHON=/opt/homebrew/bin/python3.11 bash scripts/check-release-guard.sh`.
- [ ] D2 Linux reachability proof — on a Linux Docker Engine host, start a stub
      OpenAI-embeddings server on the host, run the TUI against that host
      endpoint, and confirm the `chunker` reaches it through the generated overlay
      (a real search embeds; `embedding_degraded` stays false). If it cannot be
      made to work, document the limitation explicitly.
- [ ] End-to-end (documented manual): fresh host → one-liner install → `thorondor`
      (BYO mode) → deploy reaches healthy `/healthz` + smoke pass → wire Claude
      Code → `web_search` over the stdio proxy returns cited passages.

---

## Out of scope

- Any Docker-less / bare-metal "native pipeline" run mode (explicitly dropped).
- Building or executing llama.cpp / vLLM directly — models are always HTTP
  endpoints (BYO) or the existing Docker model profiles.
- Endpoint authentication, TLS, or rate limiting in Thorondor itself.
- Restructuring the existing Compose topology or editing the base Compose files.
  The D2 reachability fix is a generated overlay
  (`docker-compose.host-endpoints.yml`), applied only for external/host embedding
  modes; the base files stay untouched.
