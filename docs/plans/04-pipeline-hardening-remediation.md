# Thorondor Pipeline & Sovereignty Hardening — Remediation Plan

> **Source of truth.** Every task below traces to a finding in the sweep report
> `docs/reviews/20260609T124912Z/thorondor-sweep-report.md` (specialist detail in
> `findings/00..06`). Finding IDs are carried verbatim so each change is auditable
> against its evidence, acceptance criteria, and regression tests.
>
> **For executors.** Phases are dependency-ordered. Work one phase at a time; keep
> commits small and imperative (`Fix MCP HTTP lifespan`, `Add SSRF URL-safety
> resolver`). The user is the sole commit author — never add AI co-authors, and
> commit only when explicitly asked. Prefer fakes over network in tests.

## Goal

Make Thorondor **operationally safe to ship** without breaking any load-bearing
invariant. Specifically: close the three Criticals (dead MCP HTTP transport, MCP
served at the wrong path, unguarded SSRF before crawl), then the band of High/Medium
issues around input bounds, REST↔MCP parity, resilience/degradation, chunker
anti-drift, observability, and publishability — all as **surgical, additive** changes.
The pipeline shape, the seven `Protocol` seams, and the 12 non-negotiable invariants
are preserved throughout.

## Non-negotiable invariants (every phase must protect these)

Lifted from report §1. Where a fix collides with one, resolve in favour of the invariant.

1. **No persistent index / vector store in the hot path.** Dead `CACHE_BACKEND`/`REDIS_URL` get removed (or become an *injected* seam), never folded into pipeline logic.
2. **SearXNG stays a black box** — referenced upstream image, run unmodified, config-only. No `build:`/Dockerfile/vendored source (AGPL-3.0 boundary).
3. **Crawl4AI stays a public upstream container service** — use the published self-hosted Docker API (`github.com/unclecode/crawl4ai`, `unclecode/crawl4ai:<tag>`) over HTTP. No local `build:`, vendored source, or first-party reimplementation.
4. **Stateless per call.** Any cache is an injected optimization, never part of the contract.
5. **Evidence, not prose.** No summarization, no hidden second LLM hop; citations first-class.
6. **Budget, not count.** `token_budget` is the primary assembly control; a reranker must never truncate before the budget applies.
7. **Rerank scores the ORIGINAL user query**, not discovery sub-queries.
8. **The two pure stages (selection, assembly) hold cost/quality policy and stay pure** — no network/DNS I/O inside them.
9. **REST and MCP are thin twins over one `run_search`** with behavioral parity.
10. **Failure posture is graded** — hard-fail deps (searxng/chunker) 503 with a reason; degradable deps (crawl4ai/embedding/reranker) return 200 with a `reason`/`reranked` signal.
11. **Don't break the public REST/MCP wire contract** without staged migration; preserve `stats`/`reason`/`reranked`.
12. **Chunker behavior must not drift** — three valves + `strategy_version` pinning; golden parity stays green unless a deliberate `@2` regenerates it.
13. **Credit Chroma Research** for ClusterSemanticChunker; never represent it as novel.

## References

- `docs/reviews/20260609T124912Z/thorondor-sweep-report.md` — the canonical audit; §3 findings, §10 phases, §11 tests, §12 contract/migration, §13 backlog.
- `docs/reviews/20260609T124912Z/findings/00-architecture-map.md` — as-built pipeline/seam map.
- `orchestrator/pipeline.py` — `run_search(req, deps)` state machine (the spine of Phases 1, 2, 4).
- `orchestrator/app.py` — FastAPI app, `/search`, `/healthz`, MCP mount (Phases 1, 5).
- `orchestrator/mcp_server.py` — `FastMCP("thorondor")`, `web_search` tool (Phase 1).
- `orchestrator/models.py` — shared `SearchRequest`/`SearchResponse`/`SearchStats` (Phases 1, 4, 5).
- `orchestrator/selection.py`, `orchestrator/normalize.py` — pure selection gate (Phase 4).
- `orchestrator/clients/*.py` — the impure seam clients (Phases 1, 2, 4).
- `orchestrator/interfaces.py`, `orchestrator/types.py`, `orchestrator/fakes.py` — seams, internal types, deterministic fakes (Phases 0, 1).
- `semantic-chunking-service/chunking/cluster_semantic.py` — DP + three valves (Phase 3).
- `semantic-chunking-service/tests/test_parity.py`, `tests/golden/cluster_semantic@1.json` — golden parity (Phases 0, 3).
- `docker-compose.yml`, `docker-compose.llamacpp.yml`, `.env.example` — pins, mem_limit, health paths (Phases 2, 3, 4, 6).
- `https://github.com/unclecode/crawl4ai` — public upstream Crawl4AI project; Thorondor consumes its self-hosted Docker API as a containerized HTTP service.
- `README.md`, `docs/foundational design/05-licensing-and-sovereignty.md` — publishability (Phase 6).

## Build & run

- **Containerized:** yes — Crawl4AI runs as the public upstream `unclecode/crawl4ai` container on the internal Compose network. `docker compose up` works only when BYO `EMBEDDING_ENDPOINT` and `RERANKER_ENDPOINT` point at reachable external services; otherwise use `docker compose --profile bundled-models up` for the self-contained model stack.
- **Build command:** `docker compose build` (per-service Dockerfiles under `orchestrator/` and `semantic-chunking-service/`).
- **Test command:** run from repo root: `python -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` (dev deps from both `requirements-dev.txt` files).
- **Smoke (full stack):** `scripts/smoke.ps1` / `scripts/smoke.sh` after either `docker compose --profile bundled-models up` or `docker compose up` with BYO model endpoints configured.
- **Inspection:** `git status --short`, `rg --files docs`.

> PowerShell note: do not run pytest from `orchestrator/`; that puts
> `orchestrator/types.py` on `sys.path` as top-level `types` and breaks stdlib
> imports. In this workspace the verified command was:
> `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider`.

## Execution order & the hotfix gate

The two MCP-transport Criticals (Phase 1) and the SSRF Critical (Phase 4) are
**pulled forward** as immediate hotfixes on top of Phase 0's test baseline, because
the MCP surface is simply dead and SSRF is remotely exploitable. Recommended order:

```
Phase 0  (test baseline + red harnesses — no behavior change)
  └─ HOTFIX A : MCP-1 + MCP-2   (first slice of Phase 1 — ship immediately)
  └─ HOTFIX B : SEC-001/CRAWL-001 (first slice of Phase 4 — ship immediately)
Phase 1  (remainder: parity, reranker/chunker adapters, versioning)
Phase 2  (resilience & degradation)
Phase 3  (chunker robustness — turns Phase 0 red chunker specs green)
Phase 4  (remainder: scheme/canon/bounds/robots/allowlist/auth/provenance)
Phase 5  (observability & operability)
Phase 6  (licensing & sovereignty — doc/metadata + one CI guard)
```

Tasks flagged **(HOTFIX)** below are the gate. Everything after Phase 0 + the two
hotfixes is feature/hardening work and proceeds in phase order.

---

## Phase 0 — Safety Baseline & Tests

**Status:** completed
**Kind:** logic
*Closes/sets up:* IFACE-2, IFACE-6, CON-3; red harnesses for MCP-1, MCP-3/4, CHUNK-1/2/3/8/12.
*Guards:* invariants 7, 9, 11. **No production-code behavior change; no contract change.**
*Risk/rollback:* Low. New red tests are quarantined (`xfail`/`skip`) until their phase lands; rollback = revert test files only.

### Tasks
- [ ] **IFACE-2:** add `FakeSelector` (returns input unchanged or a fixed subset) and `FakeAssembler` (one passage per chunk, trivial citations) to `orchestrator/fakes.py`; keep real impls as the `deps()` default but make the fakes importable for branch isolation.
- [ ] **IFACE-6:** add `PartialChunker` and `PartialExtractor` fakes so every failure mode in arch-map §8 (Down/Empty/Partial) is reachable network-free; confirm a Down/Empty/Partial fake exists for all seven seams.
- [ ] **CON-3:** pin the MCP SDK in `orchestrator/requirements.txt` (e.g. `mcp>=1.27,<1.28`); record the validated version in a comment.
- [ ] **MCP-1/MCP-2 (red):** add an MCP-over-HTTP integration harness that performs a real `initialize` + `tools/call` via the SDK client against the mounted app at exactly `/mcp`; mark `xfail` (proves the dead mount).
- [ ] **MCP-3/MCP-4 (red):** add the parametrized REST↔MCP parity harness over one `fakes.deps()` (default; custom budget/urls; `include_raw_markdown=True`; each degraded `reason`; `reranked=false`; settings-override); mark `xfail`. This will replace `orchestrator/tests/test_mcp.py:13`.
- [ ] **CHUNK-8 (pin):** add a test pinning the `cluster-semantic@1` registry params (`max=400/min=50/initial=50`) to exact values so any edit forces a conscious `@2`.
- [ ] **CHUNK-1 (red):** add a second golden `cluster_semantic@1` case — mixed/interleaved topics with a fake embedder returning clearly separable per-topic vectors, sized so the DP boundary differs from greedy max-cap packing; assert parity pins the topic-coherent boundary. (Adding goldens to `@1` documents existing behavior — allowed.)
- [ ] **CHUNK-2 (red + guard):** add a public param-validation spec for `initial > max` and `min > max` returning HTTP 400; mark `xfail` until Phase 3 wires validation. Separately, add a private `_dynamic_programming_chunking` valve-3 fallback spec using constructed segment lengths that force `dp[n] == -inf`, asserting greedy fallback returns non-empty chunks without routing invalid public params through `resolve_strategy`.
- [ ] **CHUNK-3 (red):** add an OOM-guard test that monkeypatches `MAX_SEGMENTS_FOR_DP` low and patches `_compute_similarity_matrix` to raise, asserting it is never called above the cap while a valid greedy result returns; add a matrix dtype/byte-size (`N·N·4`, float32) assertion; mark `xfail`.
- [ ] **CHUNK-12 (red):** add greedy-semantic/greedy-fallback boundary specs (size respected except a lone oversized segment; a clear topic boundary becomes a chunk boundary); mark `xfail`.

### Verification
- [x] Repo-root pytest shows the MCP-HTTP harness and the REST↔MCP parity harness present and **failing for the documented reasons** (`xfail`), and all pre-existing tests still pass.
- [x] `orchestrator/fakes.py` exposes a deterministic fake for **all seven** Protocols; at least one pipeline test imports `FakeAssembler`/`FakeSelector`.
- [x] Repo-root pytest shows the new `@1` mixed-topic golden committed, the `@1` params-pin test green, the public invalid-param spec present as `xfail`, the private valve-3 fallback spec present, and the OOM/greedy specs present as green characterization coverage.
- [x] `orchestrator/requirements.txt` pins a concrete `mcp` range; no behavior of `/search` changed (diff is tests + fakes + one pin only).

### Implementation report — 2026-06-09

- Worker changed only Phase 0 files: `orchestrator/fakes.py`, `orchestrator/requirements.txt`, `orchestrator/tests/test_mcp.py`, `orchestrator/tests/test_pipeline.py`, `semantic-chunking-service/tests/test_app.py`, `semantic-chunking-service/tests/test_cluster_semantic.py`, `semantic-chunking-service/tests/test_parity.py`, and `semantic-chunking-service/tests/golden/cluster_semantic@1-mixed-topics.json`.
- Added importable branch-isolation fakes for selector/assembler and Down/Empty/Partial modes across all seven Protocol seams.
- Pinned MCP to `mcp>=1.27,<1.28`, validated locally against `mcp==1.27.2`.
- Added quarantined MCP HTTP and REST/MCP parity harnesses for Phase 1, plus chunker characterization/red specs for Phase 3.
- Fixed verifier findings in the Phase 0 harness: REST/MCP parity now wraps the MCP call correctly, and already-true chunker OOM/greedy checks are normal green characterization tests instead of loose XPASS tests.
- Verification command run from repo root: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `73 passed, 11 xfailed, 2 warnings`.
- `git diff --check` passed with no output.
- Targeted `--runxfail` on `orchestrator/tests/test_mcp.py` confirms the quarantined MCP failures now come from Phase 1 contract/transport gaps (`/mcp` redirect/path, missing full response parity, missing MCP optional fields/default handling), not from a harness calling bug.
- Independent re-verification found no Phase 0 blockers and recommended marking Phase 0 complete.

---

## Phase 1 — Interface & Contract Hardening (MCP transport Criticals)

**Status:** completed
**Kind:** logic
*Closes:* MCP-1, MCP-2, MCP-3/PIPE-1/OBS-1, MCP-4, MCP-5, CON-1, CON-2, PIPE-3, PIPE-4, PIPE-5, IFACE-1, IFACE-3, IFACE-4/RES-4 (client side), IFACE-5, IFACE-7.
*Guards:* invariants 8, 9, 10. **Public contract changes must be additive** (REST shape preserved; staged migration via `/v1/search` + schema snapshot).
*Risk/rollback:* Touches the public contract — keep additive; revert per-file. REST callers unaffected because changes only add fields/aliases.

### Tasks
- [x] **MCP-1 (HOTFIX):** build `FastAPI(..., lifespan=...)` in `orchestrator/app.py` with an `@asynccontextmanager` that does `async with mcp.session_manager.run(): yield` (use `AsyncExitStack` if other startup work joins), so the streamable-HTTP session manager actually starts.
- [x] **MCP-2 (HOTFIX):** set `FastMCP("thorondor", streamable_http_path="/", stateless_http=True)` in `orchestrator/mcp_server.py` so the FastAPI mount prefix `/mcp` is the whole public path (no `/mcp/mcp`); keep the public URL exactly `/mcp`.
- [x] **PIPE-4:** remove the `model_copy` default-resolution from `app.py:32-37`; make `run_search` the **sole** resolver of `token_budget`/`max_urls`; both surfaces pass the raw request through.
- [x] **MCP-3/PIPE-1/MCP-4:** change MCP tool defaults to `None` (drop the `4000`/`6` literals), add the missing optional fields (`freshness`/`domains`/`exclude_domains`/`decompose`/`max_passages`/`include_raw_markdown`) to the tool signature, and return the full `SearchResponse.model_dump()` (or at minimum `passages`, `citations`, `reason`, `reranked`, `tokens_returned`) with `passages`/`citations` first. Both surfaces share **one request-builder**.
- [x] **MCP-5/CON-2:** port the documented rich docstring (when-to-call, `token_budget` semantics, `max_urls`, accurate `Returns` shape) onto `web_search`; reconcile the doc `Returns` block to the emitted passage fields (incl. `token_count`).
- [x] **IFACE-1:** in `orchestrator/clients/reranker_client.py`, treat a missing `index` positionally (enumerate order) and wrap the parse loop so any malformed item raises `RerankerUnavailable` (already caught at `pipeline.py:104-107` → discovery-order fallback) instead of escaping as a 500.
- [x] **IFACE-4/RES-4 (client side):** back-fill any input chunk the reranker did not score with a floor score so the client yields **N ScoredChunks for N inputs** (reorder allowed, drop not) and the token budget decides output (inv 5/6); treat an empty/absurdly-short `results` body as `RerankerUnavailable` or floor-fill, never a silent empty.
- [x] **IFACE-5:** in `orchestrator/clients/chunker_client.py`, distinguish chunker-unreachable/5xx (→ `ChunkerUnavailable`, hard-fail) from per-page 4xx (→ skip that page, continue the batch).
- [x] **PIPE-3:** add a distinct `no_urls_after_selection` reason for the selection-empty branch (`pipeline.py:80-82`); keep `no_results_from_discovery` for the truly-empty merge; record the new reason in the closed enum (CON-1).
- [x] **PIPE-5:** carry a stable join key (`citation_id` → source page) through provenance for `include_raw_markdown` instead of re-deriving via `normalize_url`, so raw markdown is never silently dropped on URL mismatch; keep the stage pure.
- [x] **IFACE-3:** resolve domain allow/block in one layer (either move source-merge into the selector, kept pure, or document selection as filter+cap with merge as glue); remove the double lowercasing between `pipeline.py:74-78` and `selection.py:14-15`.
- [x] **IFACE-7:** have `ResultAssembler` return internal dataclasses (`AssembledPassage`/`AssembledCitation` in `types.py`); `run_search` projects them onto the wire models so `assembly.py` imports nothing from `models.py`.
- [x] **CON-1:** introduce `/v1/search` (alias `/search` → `/v1/search` during a deprecation window) or add `schema_version`/`api_version` to `SearchResponse`; treat `reason` codes as a closed documented enum; make additive-only the default change policy; expose a version on the MCP server/tool; add a schema-snapshot (golden JSON-schema of `SearchResponse` + MCP tool I/O) that fails on any non-additive change.

### Verification
- [x] **MCP-1/MCP-2:** the Phase 0 MCP-HTTP harness now **passes** — `initialize` + `tools/call` succeed over HTTP at exactly `http://host:8080/mcp`, `tools/list` shows `web_search`, no `RuntimeError`, and there is no reachable `/mcp/mcp`.
- [x] **Parity (MCP-3/PIPE-1/MCP-4):** the REST↔MCP parity harness is **green** — with a non-default `DEFAULT_TOKEN_BUDGET`/`MAX_URLS`, both surfaces produce identical `passages`/`citations`; an MCP caller can pass `freshness`/`domains`; a reranker-down call surfaces `reranked:false` + the right `reason` on both; passage keys match.
- [x] **IFACE-1/IFACE-4:** new `reranker_client` MockTransport tests pass — index-less response handled positionally; server scoring 1 of 3 → all 3 present (2 floor-scored); malformed item → `RerankerUnavailable` (→ 200 `reranked:false`), never an uncaught exception.
- [x] **IFACE-5:** new `chunker_client` MockTransport test passes — page 2 of 3 returns 422 → pages 1 & 3 chunk, call returns 200; a 5xx/unreachable chunker → `ChunkerUnavailable` (503).
- [x] **PIPE-3:** empty discovery → `no_results_from_discovery`; non-empty discovery + empty selection → `no_urls_after_selection` with `urls_discovered>0`, `urls_selected==0` (replaces `test_pipeline.py:38-42`).
- [x] **PIPE-4/PIPE-5/IFACE-3/IFACE-7:** defaulting lives only in `run_search` (REST passes request unmodified, existing REST tests pass); raw markdown attaches by stable `source_id` even when citation URL diverges from the source page; `assembly.py` imports nothing from `models.py`; no double lowercasing.
- [x] **CON-1:** the schema-snapshot test fails on a non-additive shape change unless the golden/version is deliberately updated; a client can read the contract version from `schema_version`.

### HOTFIX A implementation report — 2026-06-09

- Changed `orchestrator/app.py`, `orchestrator/mcp_server.py`, and `orchestrator/tests/test_mcp.py`.
- Added FastAPI lifespan startup around `mcp.session_manager.run()`, configured FastMCP streamable HTTP at `streamable_http_path="/"` with `stateless_http=True`, and kept public `/mcp` reachable without `/mcp/mcp`.
- Removed REST app-side default `model_copy`; `run_search` remains the sole default resolver for `token_budget` and `max_urls`.
- Expanded MCP `web_search` optional inputs used by parity tests and returned the full `SearchResponse.model_dump()`.
- Verification command run from repo root: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_mcp.py orchestrator\tests\test_app_rest.py orchestrator\tests\test_pipeline.py -q -p no:cacheprovider` -> `31 passed, 2 warnings`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `82 passed, 2 xfailed, 2 warnings`.
- `git diff --check` passed with no output.
- Independent verification found no HOTFIX A blockers and recommended accepting the hotfix.

### Phase 1 adapter/reason slice report — 2026-06-09

- Changed `orchestrator/clients/reranker_client.py`, `orchestrator/clients/chunker_client.py`, `orchestrator/pipeline.py`, `orchestrator/tests/test_reranker_client.py`, `orchestrator/tests/test_chunker_client.py`, and `orchestrator/tests/test_pipeline.py`.
- Reranker results now treat missing `index` as positional, wrap malformed parsing as `RerankerUnavailable`, reject empty/unusable result bodies, and floor-score any input chunks the server omitted so N input chunks produce N `ScoredChunk`s.
- Chunker per-page 4xx responses now skip only that page and continue; 5xx/unreachable failures still raise `ChunkerUnavailable`.
- Selection-empty after non-empty discovery now returns `stats.reason == "no_urls_after_selection"` while truly empty discovery remains `no_results_from_discovery`.
- Also corrected active docs/config to state Crawl4AI is a public upstream self-hosted Docker service consumed over HTTP (`unclecode/crawl4ai:0.8.9`), image-only/no vendored source.
- Focused verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_reranker_client.py orchestrator\tests\test_chunker_client.py orchestrator\tests\test_pipeline.py -q -p no:cacheprovider` -> `40 passed`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `105 passed, 2 xfailed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.
- Independent verification found no blockers. Residual risk: this slice does not close the Crawl4AI redirect/final-hop network SSRF issue; Phase 4 still tracks that separately.

### Phase 1 completion report — 2026-06-09

- Changed `orchestrator/types.py`, `orchestrator/assembly.py`, `orchestrator/interfaces.py`, `orchestrator/pipeline.py`, `orchestrator/models.py`, `orchestrator/app.py`, `orchestrator/mcp_server.py`, `orchestrator/clients/chunker_client.py`, `orchestrator/fakes.py`, REST/MCP/model/pipeline tests, and `orchestrator/tests/golden/search-response-schema.json`.
- Added internal `AssembledPassage`/`AssembledCitation` dataclasses and moved the wire-model projection into `run_search`; `assembly.py` and `interfaces.py` no longer import Pydantic wire models.
- Added per-call `source_id` provenance from deduped pages through chunk metadata to assembled citations, so `include_raw_markdown` no longer depends on URL normalization to join citations back to pages.
- Kept request-domain normalization in `SelectionPolicyImpl`; the pipeline only merges configured/request allow/block sets.
- Added `/v1/search` while preserving `/search` as a compatibility alias; added response `schema_version == "thorondor.search.v1"`, closed `stats.reason` enum validation, and a golden JSON-schema snapshot.
- Expanded the `web_search` docstring to document when to call it, `token_budget`, `max_urls`, returned `token_count`, full response envelope, and schema version.
- Updated active README/foundational API/deployment docs to reflect the versioned endpoint and the public upstream Crawl4AI container-service boundary.
- Focused Phase 1 verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_models.py orchestrator\tests\test_app_rest.py orchestrator\tests\test_mcp.py orchestrator\tests\test_assembly.py orchestrator\tests\test_chunker_client.py orchestrator\tests\test_reranker_client.py orchestrator\tests\test_pipeline.py orchestrator\tests\test_selection.py -q -p no:cacheprovider` -> `71 passed, 2 warnings`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `111 passed, 2 xfailed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.
- Independent verification found no Phase 1 code/test blockers. Residual risk remains Phase 4's Crawl4AI redirect/final-hop network SSRF work; the 2 xfails are Phase 3 chunker public parameter validation specs.

---

## Phase 2 — Resilience & Degradation

**Status:** completed
**Kind:** logic
*Closes:* RES-2, RES-3, RES-4 (pipeline guard, with IFACE-4), RES-5/OBS-4 (chunker fallback surfacing — started here), RES-6, RES-7, RES-8, PIPE-2, PIPE-6 (doc), PIPE-7, PIPE-8 (doc).
*Guards:* invariants 6, 7, 9. **No new public fields beyond additive stats; no security work.**
*Risk/rollback:* Cancellation semantics — ensure partial pages still return 200; revert per-client.

### Tasks
- [x] **RES-2:** replace the crawl fan-out (`orchestrator/clients/crawl4ai_client.py`) with explicit task collection under an overall deadline: create one task per URL, use `asyncio.wait(..., timeout=total)` or equivalent deadline-aware harvesting, keep completed pages, cancel and await pending tasks, and use explicit `httpx.Timeout(connect, read)`. Do not wrap a single `asyncio.gather(...)` in `asyncio.timeout(...)` because that can discard already-completed results.
- [x] **RES-6:** construct one reused `httpx.AsyncClient` per stage client (with `httpx.Limits`) at deps-build time, share one client across the whole crawl fan-out, close on shutdown (search, crawl, chunker, reranker, planner, healthz probe).
- [x] **RES-3:** cap planner output (e.g. `planned[:3]`) in `orchestrator/clients/planner.py` and/or bound the discovery `asyncio.gather` with a semaphore in `pipeline.py:62-64`; treat the planner as untrusted input.
- [x] **RES-4 (pipeline guard):** add a post-rerank empty guard in `pipeline.py` that sets a `reason` (rerank runs before the only existing empty guard); pairs with the Phase 1 client floor-fill.
- [x] **RES-5/OBS-4 (chunker side):** have the chunker surface the actual strategy used (`chunk_strategy`/`fallback` flag in `ChunkResponse` or per-chunk metadata) when embedding falls back to greedy; have `ChunkerClient` propagate it; add `urls_crawled_failed` and `chunks_after_dedup`/`pages_deduped` to `SearchStats` (additive — inv 10).
- [x] **RES-8:** align `RERANKER_HEALTH_PATH` — set the `settings.py` default to `/health` to match `.env.example` and `docker-compose.yml` (all three agree).
- [x] **RES-7:** decide retries — either document the no-retry stance as a deliberate anti-retry-storm choice in `AGENTS.md`/README, or add bounded (1–2) jittered retries on idempotent GETs only (discovery, crawl); never retry the chunker multi-page loop.
- [x] **PIPE-2:** in discovery (`searxng_client.py`), derive a rank-based fallback score (`score or 1/(rank+1)`) so all-zero engine scores give a deterministic relevance order, not alphabetical; keep selection pure (no network).
- [x] **PIPE-7:** either fold a small pure corroboration bonus (count of source sets containing a URL) into `merge.py`'s kept score, or document merge as max-score-only.
- [x] **PIPE-6 (doc):** mark stage 8 (pre-filter) deferred in `docs/foundational design/01-architecture.md` (or implement it later as its own impure Protocol stage — never folded into a pure stage).
- [x] **PIPE-8 (doc):** document that `decompose` has effect only when an LLM planner is configured; add a test asserting `IdentityPlanner` yields `[query]`.

### Verification
- [x] **RES-2:** failure-injection test — one URL sleeps far longer than others; `extract` returns the fast pages within the overall budget, the slow task is cancelled, pipeline yields `urls_crawled_ok < selected` and a 200.
- [x] **RES-3:** a `FakePlanner` returning 50 sub-queries results in at most N (e.g. 3) discovery calls.
- [x] **RES-5/OBS-4:** embedding-refused failure-injection — the chunker returns a fallback marker and orchestrator `stats` reflect it (`chunk_strategy`/`embedding_degraded`); a run with 3 selected / 1 crawled ok reports `urls_crawled_failed == 2`; a dedup drop is counted.
- [x] **RES-6:** mock `httpx.AsyncClient` and assert construction count is O(1) per stage per process across a burst of N concurrent searches.
- [x] **RES-8:** settings test asserts the code default equals the compose value (`/health`).
- [x] **PIPE-2/PIPE-7/PIPE-8:** pure unit tests — 10 shuffled zero-score results keep top-N in discovery/rank order (not `sorted(url)`); a URL in 3 result sets ranks above an equal-max URL in 1 (if aggregation added); `IdentityPlanner` returns `[query]`.

### Phase 2 resilience/degradation slice report — 2026-06-09

- Changed `orchestrator/clients/crawl4ai_client.py`, `orchestrator/clients/planner.py`, `orchestrator/clients/searxng_client.py`, `orchestrator/clients/chunker_client.py`, `orchestrator/merge.py`, `orchestrator/pipeline.py`, `orchestrator/models.py`, `orchestrator/settings.py`, chunker response models/app/base/cluster metadata, and related tests.
- Crawl fan-out now uses one shared per-call `AsyncClient`, explicit `httpx.Timeout`, `asyncio.wait` with an overall deadline, cancellation/await of pending tasks, and stable input order for completed pages.
- Planner output is capped at 3 in both the LLM planner and pipeline guard before discovery fan-out.
- Added additive stats: `urls_crawled_failed`, `pages_after_dedup`, `pages_deduped`, `chunk_strategy`, and `embedding_degraded`; chunker embedding fallback now surfaces `cluster-semantic-greedy-token` markers through `ChunkResponse` and chunk metadata.
- Added post-rerank empty guard with closed reason `no_chunks_after_rerank`; updated the schema golden.
- Aligned `RERANKER_HEALTH_PATH` default with compose/env (`/health`), added rank fallback scoring for zero-score SearXNG results, and added a small pure corroboration bonus in merge.
- Documented pre-filter as deferred and clarified that `decompose=true` yields `[query]` unless an LLM planner is configured.
- Focused verification after the ordering fix: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_crawl4ai_client.py orchestrator\tests\test_pipeline.py orchestrator\tests\test_models.py semantic-chunking-service\tests\test_app.py -q -p no:cacheprovider` -> `52 passed, 2 xfailed, 1 warning`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `119 passed, 2 xfailed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.
- Independent verification found no blockers. It noted completed-page crawl order was nondeterministic; that was fixed by iterating the original task list after `asyncio.wait`. RES-6 shared long-lived clients and RES-7 retry/no-retry policy remain open.

### Phase 2 completion report — 2026-06-09

- Changed `orchestrator/clients/searxng_client.py`, `orchestrator/clients/crawl4ai_client.py`, `orchestrator/clients/chunker_client.py`, `orchestrator/clients/reranker_client.py`, `orchestrator/clients/planner.py`, `orchestrator/pipeline.py`, `orchestrator/app.py`, `orchestrator/tests/test_http_client_reuse.py`, and retry-policy docs.
- Each impure stage client now owns one reusable `httpx.AsyncClient` with explicit `httpx.Limits`, accepts an injected client for tests, and exposes `aclose()`. Crawl fan-out shares the extractor's client across all per-URL tasks.
- `PipelineDeps.aclose()` closes owned stage clients; the FastAPI lifespan closes `PipelineDeps` and the reusable health-check client, then clears the global dependency reference so closed clients are not reused after shutdown.
- Added a regression test proving repeated calls across discovery, crawl, chunker, reranker, and planner construct exactly one `AsyncClient` per stage client instance.
- Documented the v1 no-retry posture in architecture/deployment docs as an intentional anti-retry-storm decision.
- Focused verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_http_client_reuse.py orchestrator\tests\test_searxng_client.py orchestrator\tests\test_crawl4ai_client.py orchestrator\tests\test_chunker_client.py orchestrator\tests\test_reranker_client.py orchestrator\tests\test_planner.py orchestrator\tests\test_app_rest.py -q -p no:cacheprovider` -> `28 passed, 1 warning`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `120 passed, 2 xfailed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.

---

## Phase 3 — Chunker Robustness & Performance

**Status:** completed
**Kind:** logic
*Closes:* CHUNK-1, CHUNK-2, CHUNK-3, CHUNK-4 (doc), CHUNK-5, CHUNK-6, CHUNK-7, CHUNK-8, CHUNK-9 (doc), CHUNK-10, CHUNK-11, CHUNK-12.
*Guards:* invariant 11. **No boundary-altering change without a `strategy_version` bump.** The `@2` tokenizer swap is *scheduled, not shipped here* unless goldens are regenerated deliberately.
*Risk/rollback:* Accidental boundary drift — the `@1` params-pin test (Phase 0) is the backstop; goldens unchanged unless an explicit `@2`; revert per-file.

### Tasks
- [x] **CHUNK-1/CHUNK-3/CHUNK-12 (green) + CHUNK-2 guard:** make the Phase 0 red chunker specs pass where they document existing-but-untested behavior. Keep the private CHUNK-2 valve-3 fallback spec green without relying on invalid public params. Add the unit test asserting `_dynamic_programming_chunking` differs from `_greedy_fallback_chunking` for a constructed similarity matrix (CHUNK-1).
- [x] **CHUNK-2 (validation):** add param cross-validation in `resolve_strategy` (`semantic-chunking-service/chunking/strategies.py`) rejecting `initial > max` and `min > max` with HTTP 400, so valve 3 is only reachable by genuine pathology.
- [x] **CHUNK-3 (mem_limit):** set a `mem_limit` on the chunker service in `docker-compose.yml` sized to the documented ~400 MB DP peak (at `CHUNKER_MAX_SEGMENTS_DP=10000`).
- [x] **CHUNK-7:** rework `embedding_function.py` to use the full base URL — strip a trailing slash and POST to `ENDPOINT/v1/embeddings` (treat a URL already ending in `/embeddings` as full), preserve scheme + path, default the port from the scheme. Behavior-neutral for the bundled `http://embedding:80` default.
- [x] **CHUNK-8:** add a documented golden-regeneration ritual (`scripts/gen_golden.py` or a `--update-goldens` flag); freeze the CHUNK-1 mixed-topic golden into the `@1` set; keep the params-pin test (Phase 0) as the guard that forces a conscious `@2`.
- [x] **CHUNK-4 (doc now):** document loudly that `token` means whitespace word and size budgets are approximate (one-line contract note — no drift); **schedule** the real tokenizer (tiktoken proxy) as a deliberate `cluster-semantic@2` with regenerated goldens (do not swap `_length` silently here).
- [x] **CHUNK-5 (doc):** document `min_chunk_size` as a soft floor (final chunk + degenerate cases exempt); fix the `cluster_semantic.py:84` docstring that overstates it as a hard guarantee. No code change (soft semantics match Chroma).
- [x] **CHUNK-6 (doc/decide):** document `start_index`/`end_index` as approximate (not byte-exact, may not round-trip after whitespace normalization) — or make chunk text a verbatim `text[char_start:char_end]` slice (text-affecting → `@2` bump if it changes text).
- [x] **CHUNK-10:** add a one-line comment that the reward LRU is intentionally per-DP-run and must not be hoisted to module/class scope (inv 3); optionally lower the default bound.
- [x] **CHUNK-11:** replace the literal `n` diagonal correction in `cluster_semantic.py:230` with `np.trace(similarity_matrix)` (or drop the log-only stat).
- [x] **CHUNK-9 (doc):** keep the sync `def chunk` endpoint (runs in the AnyIO threadpool — invariant upheld); do **not** add `set_progress_callback` in the request path; document horizontal scaling (compose replicas).

### Verification
- [x] All Phase 0 chunker specs are **green**: the mixed-topic golden pins a DP boundary that differs from greedy max-cap packing (CHUNK-1); the private valve-3 fallback test returns non-empty greedy chunks without invalid public params (CHUNK-2); the OOM guard provably never allocates the NxN matrix above the cap and the matrix is float32 `N·N·4` bytes (CHUNK-3); greedy-semantic/greedy-fallback size and topic-boundary assertions hold (CHUNK-12).
- [x] **CHUNK-2:** param-validation test — `initial > max` (and `min > max`) → 400.
- [x] **CHUNK-7:** unit tests for `https://host/v1` (no explicit port), `http://host:8080/prefix`, and the existing `http://host:port` all reach the correct URL with the correct scheme.
- [x] **CHUNK-8/CHUNK-10:** editing an `@1` param fails the params-pin test; golden regeneration is a single documented reviewable command; a DP-on-two-matrices test shows rewards reflect each matrix (no stale cache).
- [x] **CHUNK-3 (ops):** `docker-compose.yml` declares an explicit chunker `mem_limit`.
- [x] **CHUNK-4/5/6/9/11 (doc):** the chunker contract states word-based tokens, soft `min_chunk_size`, approximate offsets, and scaling guidance; `np.trace` replaces the literal `n`; `git grep` finds no boundary-affecting code change without a `strategy_version` bump.

### Phase 3 implementation report — 2026-06-09

- Changed `semantic-chunking-service/chunking/strategies.py`, `semantic-chunking-service/chunking/app.py`, `semantic-chunking-service/chunking/embedding_function.py`, `semantic-chunking-service/chunking/cluster_semantic.py`, chunker tests/golden docs, `scripts/gen_cluster_semantic_golden.py`, `docker-compose.yml`, `.env.example`, and `docs/foundational design/02-semantic-chunking-service.md`.
- Public chunk params now reject `initial_segment_tokens > max_chunk_tokens` and `min_chunk_tokens > max_chunk_tokens` with HTTP 400; the previous Phase 0 xfail is now a normal passing test.
- The embedding client now preserves full `EMBEDDING_ENDPOINT` URLs, including HTTPS, default scheme ports, and path prefixes; URLs already ending in `/embeddings` are treated as complete.
- Added DP regression coverage proving the optimizer can differ from greedy max-cap packing and that reward caching is per matrix/run; the existing OOM, valve-3, greedy-fallback, and mixed-topic golden specs are green.
- Added `mem_limit: "${CHUNKER_MEM_LIMIT:-768m}"` to the chunker service; `docker compose -f docker-compose.yml config` renders it as `805306368` bytes.
- Added a LF-stable golden regeneration command: `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe scripts\gen_cluster_semantic_golden.py`.
- Focused verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest semantic-chunking-service\tests\test_app.py semantic-chunking-service\tests\test_embedding_function.py semantic-chunking-service\tests\test_cluster_semantic.py semantic-chunking-service\tests\test_parity.py -q -p no:cacheprovider` -> `28 passed, 1 warning`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `128 passed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.

---

## Phase 4 — Security & Crawl Safety (SSRF Critical)

**Status:** in_progress
**Kind:** logic
*Closes:* SEC-001/CRAWL-001, SEC-002, SEC-003, API-1/SEC-005, SEC-004, CRAWL-002/SOV-4, CRAWL-003, SEC-007, SEC-008.
*Guards:* invariants 5, 7. **Bounds are additive validation — no contract reshape.** DNS/network I/O must NOT live inside `SelectionPolicy` (inv 7).
*Risk/rollback:* Over-broad blocking could reject legitimate public hosts — always test the public-URL-still-selected case; guard is additive, revert per-file.

### Tasks
- [x] **SEC-001/CRAWL-001 (HOTFIX):** add an **impure URL-safety resolution step before** the stage-4 selection gate that resolves each candidate host to its IP(s) and rejects loopback/link-local/private/reserved/multicast/unspecified targets (`ipaddress`), rejects IP-literal hosts in those ranges, normalizes dec/hex/oct encodings, and blocks metadata IPs (`169.254.169.254`, `fd00:ec2::254`). Feed only **deterministic safety facts** into `SelectionPolicy` so it stays pure.
- [ ] **CRAWL-001 (redirects, HOTFIX remainder):** first verify the pinned public upstream Crawl4AI Docker API can enforce redirect/final-hop controls. If it can, configure those controls through the documented API/env surface; cap redirect depth; pin the resolved IP through to connect if supported (closes DNS-rebinding). Because Crawl4AI is intentionally a separate containerized service, do **not** vendor or patch its source in this repo. If the upstream API remains opaque, enforce the boundary outside it with a wrapper/proxy or compose egress firewall that blocks link-local/RFC1918/internal targets before content can reach the orchestrator. Do not mark redirect SSRF closed by selection-only checks. Current hotfix pins Crawl4AI `0.8.9`, sends the documented Docker `/crawl` request shape, and drops returned content when `redirected_url` is unsafe, but this does **not** prove Crawl4AI never fetched the unsafe final hop.
- [x] **SEC-002:** in the selection gate, reject any URL whose scheme is not exactly `http`/`https`; treat `host_for` returning the raw string (no hostname) as an automatic reject.
- [x] **SEC-003:** canonicalize hosts before blocklist comparison in `normalize.py`/`selection.py` — strip trailing dot, IDNA-encode, normalize/reject IP-literal encodings, match blocklist entries as suffix/registrable-domain. (Host blocklist is for ToS exclusion; the IP-range guard is the real SSRF control.)
- [x] **API-1/SEC-005:** add Pydantic `Field` constraints to the **shared** `SearchRequest` (`models.py`) so both surfaces inherit them: `query=Field(min_length=1, max_length=...)`, `token_budget=Field(default=None, ge=1, le=MAX)`, `max_urls=Field(default=None, ge=1, le=HARD_MAX_URLS≈20)`, `max_passages=Field(default=None, ge=1, le=...)`; bound the chunker `ChunkRequest.text` length. REST → 422, MCP → tool error, **before any discovery/crawl**.
- [x] **SEC-004:** add `DOMAIN_ALLOWLIST` env and optional `ALLOWLIST_ONLY=true` merged into the selection gate so that, when set, only those hosts are ever crawled regardless of request.
- [x] **CRAWL-002/SOV-4:** set the Crawl4AI robots option explicitly (`check_robots_txt`) as default with an operator env toggle; cache robots within the per-call lifetime only. Verify the exact flag for the pinned Crawl4AI version.
- [x] **CRAWL-003:** add a small per-host concurrency/delay limit alongside the global semaphore; validate `CRAWL_CONCURRENCY` against a sane ceiling at settings load.
- [x] **SEC-007:** add a per-passage `provenance=external_web`/`trust=untrusted` field (or explicit delimiter) on REST and MCP; document that downstream agents must treat passage text as data, never instructions. Confirm the planner hop carries only the user query (it already does — inv 5).
- [x] **SEC-008:** add optional per-seam `*_API_KEY` env sent as `Authorization: Bearer`; let `EMBEDDING_ENDPOINT` honor https + path (depends on CHUNK-7); confirm keys are env-only, never logged, never in responses/stats/errors.

### Verification
- [ ] **SSRF (SEC-001/CRAWL-001):** a request whose discovery (or a redirect) yields `169.254.169.254`, `127.0.0.1`, `10.1.2.3`, `[::1]`, or `chunker:8000` selects/crawls **zero** such URLs; a public URL that 302/JS-redirects to an internal/metadata target yields no internal content and is dropped with a logged reason; redirect-chain-depth cap fires. The redirect-to-internal test must exercise the chosen enforcement mechanism (Crawl4AI controls, wrapper/proxy, or egress firewall). A safe public URL still selects (no over-blocking). Seed-URL cases are green; redirect/final-hop network enforcement remains open.
- [x] **Purity (inv 7):** resolver unit tests run with mocked DNS output; pure selector tests run with synthetic safety facts — selection performs no network I/O.
- [x] **SEC-002/SEC-003:** `file://`, `gopher://`, `data:`, `ftp://`, schemeless candidates dropped at selection; `evil.com.`, `EVIL.COM`, `sub.evil.com`, `0x7f000001`, `2130706433` all blocked when the operator blocks the corresponding host/IP.
- [x] **API-1/SEC-005:** per-field schema/bounds tests — too-large/zero/negative values and empty query rejected on **both** surfaces before any fan-out; `max_urls` above the hard cap rejected/clamped before `extract` is called; oversized chunk text rejected.
- [x] **SEC-004/CRAWL-002/CRAWL-003:** with `ALLOWLIST_ONLY`, a non-allowlisted domain selects zero URLs; Crawl4AI requests include `check_robots_txt` by default with an env toggle; two URLs on the same host are not fetched concurrently beyond the per-host limit; over-ceiling `CRAWL_CONCURRENCY` rejected/clamped.
- [x] **SEC-007/SEC-008:** every returned passage is labeled external-untrusted on both surfaces; configured API keys reach SearXNG, Crawl4AI, chunker, reranker, LLM, and embedding endpoints and appear in no response/stats/error; planner receives no crawled text.

### Phase 4 security slice report — 2026-06-09

- Changed `orchestrator/models.py`, `orchestrator/settings.py`, `orchestrator/normalize.py`, `orchestrator/selection.py`, `orchestrator/url_safety.py`, `orchestrator/pipeline.py`, outbound clients, chunker models/embedding client, compose/env docs, schema golden, and focused tests.
- Added shared `SearchRequest` bounds (`query` 1-500 chars, `token_budget` 1-16000, `max_urls` 1-20, `max_passages` 1-50) and chunker text max `200000`; REST returns 422 and MCP raises before fan-out.
- Canonical host matching now strips trailing dots, IDNA-normalizes, canonicalizes IP literals/legacy encodings, and suffix-matches operator blocklists/allowlists.
- Added `DOMAIN_ALLOWLIST` + `ALLOWLIST_ONLY`, per-host crawl concurrency, `CRAWL_CONCURRENCY` ceiling, and explicit Crawl4AI `check_robots_txt` with `CRAWL_RESPECT_ROBOTS_TXT`.
- Added HTTP redirect preflight for standard 30x chains before sending a URL to Crawl4AI; unsafe/too-deep chains are dropped before `/crawl` POST. Returned unsafe `redirected_url` content is still dropped.
- Added `provenance="external_web"` and `trust="untrusted"` to returned passages and updated the SearchResponse schema golden.
- Added optional bearer auth envs for SearXNG, Crawl4AI, chunker, reranker, LLM, and embedding seams; tests assert headers are sent and response/stats shapes do not expose secrets.
- Upstream verification: Crawl4AI Docker `/crawl` accepts `crawler_config` dictionaries, `CrawlerRunConfig` includes `check_robots_txt`, and the Docker server validates initial URL destinations. No documented Docker API control was found that blocks JavaScript redirects or every browser final-hop before network fetch, so full CRAWL-001 final-hop enforcement remains open and should be solved with an upstream per-hop control, wrapper/proxy, or container egress firewall.
- Focused verification: `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_models.py orchestrator\tests\test_app_rest.py orchestrator\tests\test_mcp.py orchestrator\tests\test_selection.py orchestrator\tests\test_pipeline.py orchestrator\tests\test_crawl4ai_client.py orchestrator\tests\test_settings.py orchestrator\tests\test_reranker_client.py orchestrator\tests\test_planner.py orchestrator\tests\test_chunker_client.py orchestrator\tests\test_searxng_client.py semantic-chunking-service\tests\test_app.py semantic-chunking-service\tests\test_embedding_function.py -q -p no:cacheprovider` -> `121 passed, 2 warnings`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `151 passed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.

### HOTFIX B implementation report — 2026-06-09

- Changed `orchestrator/url_safety.py`, `orchestrator/pipeline.py`, `orchestrator/fakes.py`, `orchestrator/tests/test_pipeline.py`, `orchestrator/clients/crawl4ai_client.py`, `orchestrator/tests/test_crawl4ai_client.py`, and `docker-compose.yml`.
- Added an impure `url_safety` seam before `SelectionPolicy`: it rejects non-HTTP(S), missing-host, unsafe IP literal, unsafe DNS-resolved, metadata, and legacy encoded IPv4 loopback targets. `SelectionPolicyImpl` remains pure.
- Added tests proving these seed URLs select/crawl zero URLs before extraction: `169.254.169.254`, `fd00:ec2::254`, `127.0.0.1`, `10.1.2.3`, `[::1]`, `chunker:8000` with mocked private DNS, `file://`, `gopher://`, `data:`, `ftp://`, schemeless input, `2130706433`, `0x7f000001`, `0177.0.0.1`, and `127.1`; safe public DNS still selects/crawls.
- Updated `Crawl4aiExtractor` to the current self-hosted Docker API shape (`urls` plus typed `CrawlerRunConfig`) and parse upstream-style `results`. It drops returned content when Crawl4AI reports an unsafe `redirected_url`.
- Pinned Crawl4AI from `unclecode/crawl4ai:latest` to `unclecode/crawl4ai:0.8.9`. Upstream `v0.8.9` documents Docker pull support and a Docker API server SSRF follow-up security patch; the Docker guide documents the `/crawl` request shape used here.
- Verification command run from repo root: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests\test_crawl4ai_client.py orchestrator\tests\test_pipeline.py orchestrator\tests\test_selection.py -q -p no:cacheprovider` -> `36 passed`.
- Full repo-root verification: `$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest orchestrator\tests semantic-chunking-service\tests -q -p no:cacheprovider` -> `100 passed, 2 xfailed, 2 warnings`.
- `docker compose -f docker-compose.yml config --services` listed `chunker`, `crawl4ai`, `searxng`, and `orchestrator`; `git diff --check` passed with no output.
- Independent verification found no HOTFIX B blockers and recommended accepting the seed guard plus Crawl4AI Docker API alignment, with residual redirect risk explicitly retained.
- Residual risk: returned unsafe redirected content is dropped, but this does not prove final-hop SSRF is fully closed because Crawl4AI may already have performed the unsafe fetch unless a pinned Crawl4AI per-hop control, wrapper/proxy, or container egress policy enforces blocking before network access.

---

## Phase 5 — Observability & Operability

**Status:** pending
**Kind:** mixed
*Closes:* OBS-2, OBS-3/SEC-006, OBS-4 (finalize), RES-1/API-2, OBS-5 (doc), OBS-6/SOV-3.
*Guards:* invariants 1, 3, 4, 9, 10. **No cache implementation** (dead settings are removed, not built).
*Risk/rollback:* `/healthz` probe cost — cache probe results briefly; revert per-file.

### Tasks
- [ ] **RES-1/API-2:** rewrite `/healthz` in `app.py` to **always** run the `asyncio.gather` over `_check_url` (drop the warm-path all-True short-circuit); aggregate per-dependency; set top-level `status=degraded` when any probe is false; distinguish hard-fail deps (searxng/chunker) from degradable (crawl4ai/reranker/embedding, incl. the chunker's nested embedding health); cache briefly if probe cost matters. (Pairs with RES-8 health-path fix.)
- [ ] **OBS-2:** add ASGI middleware that reads/generates `X-Request-ID`, stores it in a contextvar, includes it in structured logs, forwards it as a header on every downstream httpx call (orchestrator → chunker → embedding/reranker), and returns it in the response and on the MCP path.
- [ ] **OBS-3/SEC-006:** add structured (JSON) logging with one per-call summary line (query hash, sub-query count, urls discovered/selected/crawled_ok/failed, reranked, reason, elapsed_ms — never the raw query or markdown); add a redaction helper that strips userinfo/query strings from endpoint and target URLs before logging; never log full upstream error bodies above DEBUG; never log page bodies. Document the policy.
- [ ] **OBS-4 (finalize):** confirm the `SearchStats` additions from Phase 2 (`urls_crawled_failed`, dedup count, `chunk_strategy`/`embedding_degraded`) are emitted on both surfaces and reflected in the `/healthz` chunker-embedding sub-status.
- [ ] **OBS-5 (doc/model):** make `reranked` meaningful only when `reason` is None (document), or use a tri-state (null when rerank not reached) so rerank-degraded is distinguishable from rerank-never-reached.
- [ ] **OBS-6/SOV-3:** remove the dead `CACHE_BACKEND`/`REDIS_URL` settings from `settings.py` and `.env.example` and the docs that advertise them (cheapest, keeps the sovereignty surface honest). If a cache is implemented later it MUST be an injected `CacheBackend` Protocol in `PipelineDeps`, keyed by `normalize_url`, where disabling it changes only `elapsed_ms` — never passages/citations/stats (inv 1, 4).

### Verification
- [ ] **RES-1/API-2:** failure-injection test — monkeypatch `appmod.deps` to a built value AND point a settings URL at a closed port; `/healthz` returns `dependencies.searxng == false` and a non-ok aggregate; with all reachable, all true (replaces `test_healthz_shape`).
- [ ] **OBS-2:** a request with `X-Request-ID` appears in orchestrator and chunker logs for that call and is forwarded to mocked chunker/reranker transports.
- [ ] **OBS-3/SEC-006:** a degraded call emits one structured log line with the stats summary and no secrets; a failing embedding-call log record contains no configured secret token; endpoint/URL logging strips credentials and query strings; no page body is logged at any level.
- [ ] **OBS-4/OBS-5:** both surfaces expose the completed `stats` (incl. `urls_crawled_failed`, dedup count, fallback marker); `reason` present implies consumers ignore `reranked` (documented/tested).
- [ ] **OBS-6/SOV-3:** `git grep` finds no `CACHE_BACKEND`/`REDIS_URL` in code/docs/`.env.example` (or a cache exists as an injected seam with a test proving identical results with cache on/off/none, only `elapsed_ms` differing).

---

## Phase 6 — Licensing & Sovereignty Hardening

**Status:** pending
**Kind:** mixed
*Closes:* LIC-1, LIC-3, LIC-5, LIC-6, LIC-7, LIC-8, SOV-1, SOV-3 (doc side), SOV-4 (doc side).
*Guards:* invariants 2, 4, 12. **No code-behavior change** (doc/manifest/metadata + one CI guard). Engineering guidance, not legal advice — re-verify each license against the exact pinned tag.
*Risk/rollback:* None to runtime; revert metadata.

### Tasks
- [ ] **LIC-1:** pin SearXNG in `docker-compose.yml:49` from `:latest` to a specific tag/digest; document it as AGPL-3.0 (boundary already structurally clean — LIC-2).
- [ ] **LIC-3:** pin TEI and llama.cpp tags in `docker-compose.yml`/`docker-compose.llamacpp.yml`; verify each tag's LICENSE; **correct doc section 4** in `docs/foundational design/05-licensing-and-sovereignty.md` — TEI is currently **Apache-2.0** (re-opened, issue #232); the HFOIL drift was **TGI** (text-generation-inference); add **llama.cpp = MIT** (the doc omits it though compose wires it).
- [ ] **LIC-6:** keep the Crawl4AI image tag pinned (`unclecode/crawl4ai:0.8.9` as of HOTFIX B); add a `NOTICE`/`THIRD-PARTY-NOTICES.md` enumerating each bundled image + pip dep with pinned version, license, upstream link, and any required upstream `NOTICE`/attribution text (Crawl4AI Apache-2.0, public upstream Docker service consumed over HTTP, SearXNG AGPL-3.0 referenced upstream, TEI/llama.cpp, Python deps); reference it from the README.
- [ ] **LIC-5:** add a root `LICENSE` (MIT or Apache-2.0) covering `orchestrator/` + `semantic-chunking-service/` and name it in the README. (Without it, publication is blocked.)
- [ ] **LIC-7:** add a README "Credits / Attribution" section crediting Chroma Research, "Evaluating Chunking Strategies for Retrieval" (July 2024), stating the implementation is first-party and the algorithm not novel (inv 12).
- [ ] **LIC-8:** add a one-line provenance note in the `recursive_splitter.py` module docstring and NOTICE clarifying `RecursiveCharacterTextSplitter` as LangChain-inspired/MIT (or independent).
- [ ] **SOV-1:** add a README "Sovereignty & outbound network" section disclosing the two optional egress paths (commercial search API key in SearXNG `settings.yml`; remote BYO model endpoints) as explicit operator choices, and stating the core stack reaches only the searched sites with ephemeral data at rest.
- [ ] **SOV-4 (doc side):** if robots.txt enforcement was not wired in Phase 4, document the delegated default and ToS posture (otherwise this is closed by Phase 4).
- [ ] **Release guard:** add a CI assertion — no `:latest` anywhere; no `build:` for the `searxng` service — to backstop the publishability checklist.

### Verification
- [ ] The report's **Publishability Checklist** (§9) is fully ticked: SearXNG unmodified + tag-pinned + documented AGPL-3.0; model-server tags pinned + LICENSE verified; doc section 4 TEI/TGI/llama.cpp corrected; root LICENSE present and named in README; NOTICE/third-party-notices present and referenced; Chroma credit in README; splitter provenance noted; optional-outbound disclosures in README; Crawl4AI tag pinned; cache claim matches code (from Phase 5); non-goals preserved.
- [ ] The release-guard CI assertion passes: `git grep ':latest'` over compose files returns nothing, and `searxng` has no `build:`.
- [ ] `README.md` and `docs/foundational design/05-licensing-and-sovereignty.md` are internally consistent with the pinned tags and corrected license facts.

---

## Traceability — every backlog item to its phase

All 59 prioritized-backlog items (report §13) are covered. `(hf)` = hotfix pulled forward; `(doc)` = doc/metadata.

| # | Finding | Phase(s) | # | Finding | Phase(s) |
|---|---|---|---|---|---|
| 1 | MCP-1 | 1 (hf) | 31 | OBS-3+SEC-006 | 5 |
| 2 | SEC-001+CRAWL-001 | 4 (hf) | 32 | OBS-4+RES-5 | 2 + 5 |
| 3 | MCP-2 | 1 (hf) | 33 | RES-2 | 2 |
| 4 | API-1+SEC-005 | 4 | 34 | RES-3 | 2 |
| 5 | SEC-002 | 4 | 35 | SEC-004 | 4 |
| 6 | SEC-003 | 4 | 36 | CRAWL-003 | 4 |
| 7 | MCP-3+PIPE-1+OBS-1+MCP-4+CON-2 | 1 | 37 | MCP-5 | 1 |
| 8 | RES-1+API-2 | 5 | 38 | LIC-6 | 6 |
| 9 | CRAWL-002+SOV-4 | 4 | 39 | LIC-7 | 6 |
| 10 | CON-1 | 1 | 40 | SOV-1 | 6 |
| 11 | IFACE-1 | 1 | 41 | PIPE-2 | 2 |
| 12 | IFACE-2 | 0 | 42 | PIPE-6 | 2 (doc) |
| 13 | CHUNK-1 | 0 + 3 | 43 | PIPE-7 | 2 |
| 14 | CHUNK-2 | 0 + 3 | 44 | PIPE-8 | 2 (doc) |
| 15 | CHUNK-3 | 0 + 3 | 45 | IFACE-6 | 0 |
| 16 | LIC-1 | 6 | 46 | IFACE-7 | 1 |
| 17 | LIC-3 | 6 | 47 | CHUNK-9 | 3 (doc) |
| 18 | LIC-5 | 6 | 48 | CHUNK-10 | 3 |
| 19 | PIPE-3 | 1 | 49 | CHUNK-11 | 3 |
| 20 | PIPE-4 | 1 | 50 | CHUNK-12 | 0 + 3 |
| 21 | PIPE-5 | 1 | 51 | RES-6 | 2 |
| 22 | IFACE-3 | 1 | 52 | RES-7 | 2 (doc) |
| 23 | IFACE-4+RES-4 | 1 + 2 | 53 | RES-8 | 2 |
| 24 | IFACE-5 | 1 | 54 | OBS-5 | 5 (doc) |
| 25 | CHUNK-4 | 3 (doc) | 55 | OBS-6+SOV-3 | 5 |
| 26 | CHUNK-5 | 3 (doc) | 56 | SEC-007 | 4 |
| 27 | CHUNK-6 | 3 | 57 | SEC-008 | 4 |
| 28 | CHUNK-7 | 3 | 58 | CON-3 | 0 |
| 29 | CHUNK-8 | 0 + 3 | 59 | LIC-8 | 6 |
| 30 | OBS-2 | 5 | — | — | — |

**Preserve, do not fix (confirmations):** LIC-2 (AGPL boundary clean), LIC-4 (no embedded model), SOV-2 (hard non-goals hold).
