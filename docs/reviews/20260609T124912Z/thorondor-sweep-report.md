# Thorondor Pipeline & Sovereignty Sweep Report

Run directory: `C:\git\FEANORS-CODE\thorondor\docs\reviews\20260609T124912Z`
Specialist inputs: `findings/00-architecture-map.md` through `findings/06-licensing-sovereignty.md` (all seven present; no coverage gaps).
Status: as-built audit. The project `CLAUDE.md` claim "design-first, no code yet" is **stale** — both first-party services (`orchestrator/`, `semantic-chunking-service/`) are fully implemented with tests, Dockerfiles, Compose, and deploy scripts.

---

## 1. Executive Summary

### What Thorondor is
A self-hosted, agent-ready **semantic web search service** — a sovereign alternative to hosted search-for-agents APIs (Exa/Tavily). It runs an ordered, fault-tolerant pipeline (plan → discover → merge → select → extract → content-dedup → chunk → rerank → assemble) over bundled SearXNG + Crawl4AI with bring-your-own embedding/reranker/planner HTTP endpoints, and exposes two thin surfaces over one shared `run_search`: REST `POST /search` and an MCP `web_search` tool. It returns reranked, citation-bearing, token-budgeted passages — evidence, not prose — and keeps no persistent corpus.

### Overall health verdict
**Structurally sound, operationally unsafe to ship as-is.** The architecture honors its hardest invariants: the pure stages are genuinely pure, the pipeline is stateless per call, no vector store exists, the chunker's DP core and three safety valves are correctly implemented, and the SearXNG AGPL boundary is clean. But there are **three Critical defects** that make the product either dead or dangerous at runtime, plus a band of High issues around input bounds, contract parity, and observability. None of the Criticals require a rewrite; all are surgical.

The three Criticals, lead with these:

1. **The MCP HTTP transport is dead on arrival (MCP-1).** `streamable_http_app()` is mounted on a FastAPI app with no lifespan running `mcp.session_manager.run()`, so the first real MCP request raises `RuntimeError: Task group is not initialized`. CI is green only because `test_mcp.py` calls the `web_search` coroutine directly and asserts the route name exists — no test drives a real MCP HTTP session, so the failure is unobserved. The primary agent surface does not work over HTTP.
2. **The MCP endpoint is served at the wrong path (MCP-2).** `FastMCP` already serves under `/mcp`; mounting it under the FastAPI prefix `/mcp` composes to `/mcp/mcp`. Even after MCP-1 is fixed, agents configured per the documented `http://host:8080/mcp` get a 404; the working endpoint is undocumented.
3. **No SSRF guard before crawl (SEC-001 + CRAWL-001).** The stage-4 selection gate filters only by exact host string. Any discovered URL pointing at `169.254.169.254` (cloud metadata), loopback, RFC1918, or sibling containers (`chunker:8000`, `searxng:8080`) is crawled and its body returned to the agent. Redirects are followed with no post-redirect re-validation, so even an allowlisted public URL can 30x/JS-redirect to an internal target. One `/search` call exfiltrates IAM credentials on cloud or performs internal recon on-prem.

### Top 10 risks
1. **MCP-1** (Critical) — HTTP MCP transport fails at request time; surface is dead.
2. **SEC-001 / CRAWL-001** (Critical) — full SSRF to internal/metadata endpoints, including via redirects.
3. **MCP-2** (Critical) — MCP served at `/mcp/mcp`; documented `/mcp` 404s.
4. **API-1 / SEC-005** (High) — no upper bounds on `token_budget`/`max_urls`/`max_passages`/`query`/chunk text; one request can fan out 100k crawls or exhaust memory (DoS).
5. **SEC-002 / SEC-003** (High) — no scheme allowlist (`file://`, `gopher://`, `data:` pass the gate); host-exact-match blocklist bypassable by trailing dot, subdomain, dec/hex IP encodings.
6. **MCP-3 / PIPE-1 / OBS-1** (High) — MCP drops `stats`/`reason`/`reranked`; degraded-200 contract invisible on the primary agent surface; REST/MCP parity broken.
7. **RES-1 / API-2** (High) — `/healthz` returns hardcoded all-True once deps are warm; load balancers route to dead nodes; container healthcheck false-green.
8. **CRAWL-002 / SOV-4** (High) — `robots.txt` never enforced; polite-crawl invariant (inv 7) and ToS posture violated.
9. **CHUNK-1 / CHUNK-2 / CHUNK-3** (High) — the golden test pins size-packing not the semantic DP path; valve-3 (DP-no-solution) untested; the 400 MB OOM ceiling has no test lock and no container `mem_limit` — boundary-affecting drift can ship green.
10. **CON-1** (High) — no API/tool version or staged-migration mechanism; any future change to the wire shape breaks all agents at once (blocks safe evolution of inv 10).

Honorable mentions just below the line: **IFACE-1** (reranker parse crash escapes as 500, breaking graded degrade), **MCP-4** (MCP hardcodes `token_budget=4000`/`max_urls=6`, ignoring operator settings), **LIC-1/LIC-3/LIC-5** (unpinned `:latest` tags, no `LICENSE` — blocks publication).

### Top 10 recommended refactors
1. **Add an impure URL-safety resolution step before the pure selection gate** that resolves each candidate host to its IP(s), canonicalizes scheme/host, and rejects loopback/link-local/private/reserved/metadata targets; the selector then consumes deterministic safety facts. Re-validate the final hop after redirects and pin the resolved IP through to connect.
2. **Wire the MCP HTTP transport correctly**: build FastAPI with a lifespan entering `mcp.session_manager.run()`; set `streamable_http_path="/"` so the public URL is exactly `/mcp`; enable `stateless_http=True`.
3. **Add Pydantic `Field` bounds on the shared `SearchRequest`** so both surfaces inherit them (`query` length, `token_budget`/`max_urls`/`max_passages` with `ge=1`/`le=cap`), plus a chunker `text` length bound.
4. **Make REST and MCP true twins over one request-builder**: resolve all defaults in `run_search` only (remove the REST `model_copy` pre-resolution and the MCP signature literals), and have MCP return the full `SearchResponse` envelope (or at minimum `reason` + `reranked` + `stats`).
5. **Always probe in `/healthz`** (drop the warm-path short-circuit); aggregate per-dependency, distinguishing hard-fail deps (searxng/chunker) from degradable (crawl4ai/reranker/embedding).
6. **Harden the reranker adapter**: treat missing `index` positionally, back-fill missing chunks with a floor score so the token budget (not the reranker) decides output, and wrap the parse loop so any malformed shape raises `RerankerUnavailable` (graceful degrade) rather than escaping as a 500.
7. **Strengthen the chunker test net**: add a mixed-topic golden that exercises the semantic DP boundary, a forced valve-3 test, an OOM-guard "matrix never allocated above cap" test, and a pin on the `cluster-semantic@1` params — and decide the tokenizer (word-count vs real tokenizer behind a `strategy_version` bump).
8. **Add observability spine**: `X-Request-ID` contextvar middleware forwarded on every downstream httpx call, one structured per-call summary log line with secret/URL redaction, and complete the `stats` block (`urls_crawled_failed`, dedup count, `chunk_strategy`/`embedding_degraded`).
9. **Wire crawl politeness**: set Crawl4AI `check_robots_txt` by default with an operator toggle; add a per-host throttle; clamp `CRAWL_CONCURRENCY` to a sane ceiling.
10. **Publishability hygiene**: pin all `:latest` image tags (SearXNG, Crawl4AI, TEI, llama.cpp) to a tag/digest; add a root `LICENSE` (MIT/Apache-2.0), a `NOTICE`/third-party-notices file, a README Chroma-Research credit, and a sovereignty/optional-outbound disclosure; correct the doc's TEI-vs-TGI license claim.

### What must NOT change — Non-Negotiable Invariants and hard non-goals
Any refactor below must protect these. Where a specialist recommendation collided with one, the report resolves in favor of the invariant (noted at each conflict).

1. **No persistent web index / vector store in the hot path.** Hard non-goal. Crawled content is chunked, reranked, returned, and discarded per call. Do not add a vector store. (Upheld today; the dead `CACHE_BACKEND`/`REDIS_URL` settings must either be implemented as an *injected* optimization or removed — never folded into pipeline logic.)
2. **SearXNG stays a black box** — referenced upstream image, run unmodified, configured only via its own `settings.yml`, called over HTTP behind the `SearchDiscovery` Protocol. Never add a `build:`/Dockerfile/vendored source. This is the AGPL-3.0 network-copyleft boundary. (Confirmed clean — LIC-2.)
3. **Stateless per call.** No server-side session; any cache is an injected optimization, never part of the contract.
4. **The tool returns evidence, not prose.** No summarization, no hidden second LLM hop. The only LLM call is the optional query planner (a discovery device). Citations are first-class.
5. **Budget, not count.** `token_budget` is the primary assembly control, not `top_k`. (IFACE-4 / RES-4 must not let a reranker truncate before the budget applies.)
6. **Rerank scores the ORIGINAL user query**, not the discovery sub-queries. (Upheld at `pipeline.py:101`.)
7. **The two pure stages (selection, assembly) hold the cost/quality policy** and stay pure (no network I/O). DNS/IP resolution happens before selection and passes deterministic URL-safety facts into selector tests; do not hide network I/O inside `SelectionPolicy`.
8. **REST and MCP are thin twins over one `run_search`** with behavioral parity. (Violated on MCP today — PIPE-1/MCP-3/MCP-4.)
9. **Failure posture is graded, not all-or-nothing.** SearXNG/chunker down → hard-fail 503 with a reason; Crawl4AI partial → proceed on what fetched; embedding down → chunker falls back to greedy token chunking; reranker down → discovery order with `reranked: false`. Degraded calls still return 200 with a `reason`.
10. **Do not break the public REST/MCP wire contract** without a staged migration; preserve the `stats` block, `reason` codes, and `reranked` flag.
11. **Chunker behavior must not drift.** Three safety valves + `strategy_version` pinning; golden-file parity (deterministic stub embedding) stays green unless a deliberate `strategy_version` bump regenerates it.
12. **Credit Chroma Research** for the ClusterSemanticChunker; never represent it as novel. (Code header correct; README missing — LIC-7.)

### Coverage gaps
None. All seven findings files were present and read in full. The only meta-note is that several specialists independently flagged the same root issues (MCP parity, `/healthz`, dead cache, input bounds, word-count token proxy, robots.txt); these are merged below into single canonical findings and cross-referenced rather than duplicated.

---

## 2. Current Architecture Map (distilled)

Full detail: `findings/00-architecture-map.md`.

**Pipeline (single async entrypoint `run_search(req, deps)` in `orchestrator/pipeline.py:52`), as built:**

| # | Stage | Pure/Impure | Seam |
|---|---|---|---|
| 1 | Plan | impure or no-op | `QueryPlanner` — `IdentityPlanner` (default) / `LlmPlanner` |
| 2 | Discover | impure (HTTP) | `SearchDiscovery` — `SearxngDiscovery` (fan-out via `asyncio.gather`) |
| 3 | Merge/dedup | **pure** | free fn `merge.py` (dict by `normalize_url`, keep max score) |
| 4 | Select/budget | **pure** | `SelectionPolicy` — `SelectionPolicyImpl` (blocklist/allowlist + sort + `[:max_urls]`) — *the crawl-protecting gate* |
| 5 | Extract | impure (HTTP), partial-tolerant | `ContentExtractor` — `Crawl4aiExtractor` |
| 6 | Content dedup | **pure** | free fn `content_dedup.py` (sha256 of first 2000 chars) |
| 7 | Chunk | impure (HTTP) | `SemanticChunker` — `ChunkerClient` (per-page sequential loop) |
| 8 | Pre-filter | **NOT IMPLEMENTED** | documented optional stage absent |
| 9 | Rerank | impure (HTTP), graceful-degrade | `Reranker` — `RerankerClient` (scores ORIGINAL query) |
| 10 | Assemble | **pure** | `ResultAssembler` — greedy by score under `token_budget` |
| 11 | Return | pure | builds `SearchResponse` (+ optional `raw_markdown`) |

Seven `typing.Protocol` seams (`interfaces.py`); concrete clients in `clients/`; deterministic fakes in `fakes.py` (five of seven seams have fakes — selection and assembly run the real impls in pipeline tests — IFACE-2).

**BYO model seams** (env → `Settings` → `build_deps_from_settings` → client): embedding (`/v1/embeddings`, chunker only), reranker (`/rerank`), optional LLM planner (`/v1/chat/completions`). No server hardcoded. The chunker's `EmbeddingFunction` is the lone seam that reduces its endpoint to `http://host:port` (forces http, requires explicit port, drops path — CHUNK-7).

**Two surfaces, one pipeline:** REST `POST /search` (full `SearchResponse`, 503 on hard-fail deps); MCP `web_search` (returns only `{passages, citations}`, hardcodes `token_budget=4000`/`max_urls=6`). MCP mounted at `/mcp` via `streamable_http_app()` (broken — MCP-1/MCP-2) plus a stdio transport for standalone.

**State/cache:** none implemented. `CACHE_BACKEND`/`REDIS_URL` are dead settings. Per-call `SearchStats`; only process-global state is the lazily-built singleton deps and the per-process chunker embedder. No persistent corpus (inv 1 upheld).

**As-built failure posture:** SearXNG down → 503 `searxng_unavailable`; chunker unreachable → 503 `chunker_unavailable`; Crawl4AI partial → drop failed URLs, 200 (or `all_crawls_failed` if zero); embedding down → chunker greedy fallback (invisible to orchestrator — RES-5); reranker down → discovery order, `reranked=false`; planner errors → silent identity. MCP discards all of `stats`/`reason`/`reranked`.

**Doc divergences (map §9):** D1 cache + pre-filter absent; D2 "no code" stale; D4 reranker health-path mismatch (`/healthz` code vs `/health` compose); D5 embedding endpoint reduction; D6 MCP drops stats; D8 chunker per-page not batched; D9 word-count token proxy; D10 two BYO compose recipes (TEI / llama.cpp).

---

## 3. Findings (consolidated, de-duplicated, sorted by severity then category)

Specialist IDs are carried forward. Merged findings list all contributing IDs; cross-references are noted inline. No renumbering was needed (no genuine ID collisions across categories).

### CRITICAL

#### MCP-1 — Streamable-HTTP MCP mount has no session-manager lifespan; `/mcp` fails at request time
- **Severity:** Critical · **Category:** MCP / Transport
- **Evidence:** `orchestrator/app.py:11` (`FastAPI(...)` with no `lifespan=`); `app.py:96` `app.mount("/mcp", mcp.streamable_http_app())`; no `mcp.session_manager.run()` anywhere.
- **Problem:** In MCP Python SDK 1.x, `streamable_http_app()` returns a Starlette app whose `StreamableHTTPSessionManager` must be started inside the **parent** app's lifespan. Nested sub-app lifespans are not auto-invoked by the parent, so the first request raises `RuntimeError: Task group is not initialized. Make sure to use run().`
- **Impact:** The HTTP MCP transport — the surface remote agents use — is broken on first request. **CI masks it:** `test_mcp.py:19` calls the `web_search` coroutine directly and only asserts a route literally named `/mcp` exists; no test performs a real MCP HTTP `initialize`/`tools/call`, so the runtime failure is unobserved.
- **Recommendation:** Build FastAPI with `lifespan=` an `@asynccontextmanager` that does `async with mcp.session_manager.run(): yield` (use an `AsyncExitStack` if other startup work is added). Pin the MCP SDK (CON-3) and verify against the pin.
- **Acceptance Criteria:** An MCP `initialize` handshake over HTTP succeeds (returns a session) with no `RuntimeError`; `tools/list` shows `web_search`.
- **Regression Tests:** MCP-over-HTTP integration test performing `initialize` + `tools/call` against the mounted app via the SDK client, asserting a passage/citation payload returns (this is the test that would have caught the dead mount).

#### MCP-2 — Double-mount: tool served at `/mcp/mcp`, not the documented `/mcp`
- **Severity:** Critical · **Category:** MCP / Transport
- **Evidence:** `orchestrator/mcp_server.py:7` `FastMCP("thorondor")` (default `streamable_http_path="/mcp"`); `app.py:96` mounts under prefix `/mcp` → effective `/mcp/mcp`. Docs advertise `http://host:8080/mcp`.
- **Problem:** The mount prefix composes with FastMCP's own internal `/mcp` path. Agents configured per the documented URL hit `/mcp` and 404; the working endpoint is undocumented. `test_mcp.py:19` checks only the mount-prefix route name, so it passes regardless.
- **Recommendation:** Set `FastMCP("thorondor", streamable_http_path="/")` so the mount prefix `/mcp` is the whole public path. Also set `stateless_http=True` (project is stateless-per-call, inv 3; SDK-recommended for a horizontally-scaled mount). Keep the public URL exactly `/mcp`.
- **Acceptance Criteria:** A request to `http://host:8080/mcp` reaches the MCP transport; no reachable `/mcp/mcp`.
- **Regression Tests:** Route/transport test asserting the MCP handshake resolves at exactly `/mcp`.

#### SEC-001 + CRAWL-001 — No SSRF guard before crawl, and redirects not re-validated (merged)
- **Severity:** Critical · **Category:** Security
- **Cross-references:** ties to SEC-002 (scheme), SEC-003 (blocklist weakness), API-1/SEC-005 (input bounds amplify fan-out), inv 7 (selection gate protects the crawl).
- **Evidence:** `orchestrator/selection.py:6-22` filters only by exact host-string membership; grep for `169.254`/`127.0.0.1`/`is_private`/`ip_address` across `**/*.py` returns zero guard code. `pipeline.py:78,84` feeds selected URLs straight to `Crawl4aiExtractor.extract` (`crawl4ai_client.py:18-38`), which POSTs the URL to Crawl4AI with no host validation and no redirect policy; Crawl4AI follows HTTP/JS redirects by default and nothing re-checks the final hop.
- **Problem:** SearXNG returns attacker-influenceable URLs. Any discovered or redirected-to URL pointing at `http://169.254.169.254/latest/meta-data/`, loopback, RFC1918, or sibling containers (`chunker:8000`, `searxng:8080`) is crawled and its body returned. The host-string gate validates only the pre-redirect seed; the final target is fetched unchecked (canonical public→internal redirect SSRF).
- **Impact:** Full SSRF via one `/search` or MCP `web_search` call: cloud IAM/credential theft via the metadata endpoint; internal recon and access to sibling containers on-prem. The stated first-class risk, unmitigated. Violates inv 7 (the selection gate does not protect the crawl).
- **Recommendation:** Add an impure URL-safety resolution step before the stage-4 selection gate. Resolve each candidate host to its IP(s) and reject if any resolved address is loopback/link-local/private/reserved/multicast/unspecified (`ipaddress`); reject IP-literal hosts in those ranges and normalize dec/hex/oct encodings; block metadata IPs (`169.254.169.254`, `fd00:ec2::254`). Feed only deterministic safety facts into the selector so `SelectionPolicy` remains pure. **Re-validate the final hop:** disable redirect-following and re-run the guard on each `Location`, or use a connection-time hook for every hop; cap redirect count; pin the resolved IP through to connect (closes DNS-rebinding). As defense-in-depth, egress-firewall the `crawl4ai` container off link-local + RFC1918.
- **Acceptance Criteria:** A request whose discovery (or a redirect) yields `169.254.169.254`, `127.0.0.1`, `10.1.2.3`, `[::1]`, or `chunker:8000` selects/crawls zero such URLs; a public URL that redirects to an internal/metadata target yields no internal content and is dropped with a logged reason.
- **Regression Tests:** URL-safety resolver tests (metadata IP, loopback, each RFC1918 block, IPv6 ULA/loopback, internal Compose hostnames, dec/hex IP encodings) + pure selector tests with synthetic safety facts proving safe public URLs still select and unsafe candidates are excluded; redirect-to-internal test (mock 302 → `169.254.169.254`/`127.0.0.1`) asserting the final fetch is blocked; redirect-chain-depth cap test.

### HIGH

#### API-1 + SEC-005 — No input bounds on `token_budget`/`max_urls`/`max_passages`/`query`/chunk text (merged)
- **Severity:** High · **Category:** API / Security
- **Cross-references:** amplifies SEC-001 (fan-out blast radius), CHUNK-3 (NxN chunker blow-up via unbounded text), inv 6 (budget-not-count), inv 7 (crawl politely).
- **Evidence:** `orchestrator/models.py:39-48` types `token_budget`/`max_urls`/`max_passages` as `int | None` with no `Field(ge/le)`; `query: str` has no length bound. REST default-resolution only treats falsy values (`req.x or default`), so any positive int passes unchecked. `max_urls` flows into selection slice `[:max_urls]` → crawl fan-out. MCP (`mcp_server.py:24-25`) takes ints with no cap. Chunker `ChunkRequest.text` (`semantic-chunking-service/chunking/models.py:19-24`) is unbounded; the DP builds an NxN matrix (OOM guard only at 10000 segments).
- **Problem:** `max_urls: 100000` fans out 100k crawls (gated only by `CRAWL_CONCURRENCY`); `token_budget: 1e12` defeats the assembly cap and returns essentially the whole corpus; unbounded `query`/`text` drives the chunker N² blow-up. One unauthenticated request exhausts orchestrator and chunker memory/CPU.
- **Impact:** Trivial DoS via one request on either surface; defeats the select/budget-protects-the-crawl intent.
- **Recommendation:** Add Pydantic `Field` constraints to the shared `SearchRequest` so both surfaces inherit them: `query=Field(min_length=1, max_length=...)`, `token_budget=Field(default=None, ge=1, le=MAX)`, `max_urls=Field(default=None, ge=1, le=HARD_MAX_URLS≈20)`, `max_passages=Field(default=None, ge=1, le=...)`. Bound chunker `text` length. REST returns 422; MCP returns a tool error — before any discovery/crawl.
- **Acceptance Criteria:** Out-of-range/zero/negative values and empty query rejected on both surfaces before any fan-out; crawl fan-out can never exceed a server hard cap regardless of request value; oversized chunk text rejected.
- **Regression Tests:** Schema/bounds tests per field (too-large, zero, negative, empty query); a test asserting `max_urls` above the hard cap is rejected/clamped before `extract` is called; oversized chunk-text rejected.

#### MCP-3 + PIPE-1 + OBS-1 + MCP-4 + CON-2 — MCP surface breaks parity: drops observability, hardcodes defaults, omits fields, diverges shape (merged)
- **Severity:** High · **Category:** MCP / Pipeline / Observability / Contracts
- **Cross-references:** inv 8 (thin twins), inv 10 (preserve stats/reason/reranked), PIPE-4 (defaulting duplicated — root cause).
- **Evidence:** `orchestrator/mcp_server.py:24-34` vs `app.py:30-44`. MCP (1) hardcodes `token_budget=4000`/`max_urls=6` as signature literals while REST resolves from `deps.default_*` (env `DEFAULT_TOKEN_BUDGET`/`MAX_URLS`); (2) exposes no `freshness`/`domains`/`exclude_domains`/`decompose`/`max_passages`/`include_raw_markdown`; (3) returns only `{passages, citations}`, dropping `stats`, `reason`, `reranked`, `raw_markdown`. The doc MCP `Returns` block also omits `token_count` that the code actually emits (CON-2). `test_mcp.py:13` asserts the result has *exactly* `{passages, citations}`, actively encoding the divergence.
- **Problem:** The two surfaces are meant to be thin twins over one `run_search` (inv 8). They are not: with a non-default `DEFAULT_TOKEN_BUDGET`, MCP and REST give different answers; an MCP agent cannot scope a search; and an MCP agent receiving empty passages cannot distinguish `no_results_from_discovery` (reword) from `all_crawls_failed` from a reranker degrade (`reranked: false`). The degradation signals the design promises agents use for the next hop are stripped on the primary agent surface (inv 10).
- **Impact:** Behavioral parity (inv 8) and the stats/reason/reranked wire contract (inv 10) are broken on MCP. Silent-quality-loss vector precisely where the agent owns the loop.
- **Recommendation (incremental, additive):** Resolve all defaults in `run_search` only (see PIPE-4) and have both surfaces use **one shared request-builder**: change MCP tool defaults to `None` and pass through; add the missing optional fields to the tool signature; return the full `SearchResponse.model_dump()` (or at minimum `passages`, `citations`, `reason`, `reranked`, `tokens_returned`) with `passages`/`citations` first so existing callers do not break. Reconcile the doc `Returns` block to the emitted passage fields.
- **Acceptance Criteria:** With a non-default `DEFAULT_TOKEN_BUDGET`/`MAX_URLS`, MCP and REST produce identical passages/citations for identical inputs; an MCP caller can pass `freshness`/`domains`; a reranker-down call surfaces `reranked: false` and the right `reason` on both surfaces; passage keys match across surfaces.
- **Regression Tests:** The parametrized REST↔MCP parity test (Section 11) over the same `fakes.deps()`: default request; custom budget/urls; `include_raw_markdown=True`; each degraded `reason`; `reranked=false`; settings-override case. Replaces the divergence-encoding `test_mcp.py:13`.

#### RES-1 + API-2 — `/healthz` reports all dependencies healthy without probing once warm (merged)
- **Severity:** High · **Category:** Resilience / API
- **Evidence:** `orchestrator/app.py:61-72` returns a hardcoded all-True dependency map when `deps is not None`. The live `_check_url` probes (`app.py:47-53,85-90`) only run on the cold path (`deps is None`). `get_deps()` is invoked and memoized on the first `/search`, so `deps` is non-None almost immediately in any running container. `test_app_rest.py:43-50` asserts only the shape, locking in the lie. Docker `HEALTHCHECK` depends on this endpoint.
- **Problem:** After warm-up, a dead SearXNG/Crawl4AI/chunker/reranker still reports `True` — the opposite of a readiness probe. Violates the documented requirement (`03-deployment.md:259-262`, `01-architecture.md §7`) that `/healthz` aggregate downstream reachability.
- **Impact:** Load balancers route to nodes whose hard-fail deps are down (every search 503s); orchestration will not restart/de-route a broken instance; operators get no degradation signal. Single biggest observability regression.
- **Recommendation:** Always run the `asyncio.gather` over `_check_url` regardless of whether deps are built; set top-level `status` to `degraded` when any probe is false, distinguishing hard-fail deps (searxng/chunker) from degradable (crawl4ai/reranker/embedding). Probe the same endpoints the pipeline uses; cache briefly if probe cost matters. Also fix RES-8 (`RERANKER_HEALTH_PATH` default `/healthz` vs compose `/health`) so the three sources agree.
- **Acceptance Criteria:** With deps warm and SearXNG unreachable, `/healthz` returns `dependencies.searxng == false` and a non-ok aggregate; with all reachable, all true.
- **Regression Tests:** Failure-injection test monkeypatching `appmod.deps` to a built value AND pointing a settings URL at a closed port, asserting the corresponding dependency is false (replaces `test_healthz_shape`).

#### SEC-002 — Crawl scheme is unrestricted (no http/https allowlist)
- **Severity:** High · **Category:** Security
- **Cross-references:** part of the SEC-001 SSRF defense package.
- **Evidence:** `selection.py` / `normalize.py:5-7` never inspect scheme; `host_for` returns `urlparse(url).hostname or url`, so schemeless/odd inputs pass through; the crawl client forwards any string. `file:///etc/passwd` parses to `hostname=None`, so `host_for` returns the whole string, never matches a blocklist host, and (no allowlist) passes selection.
- **Problem:** Non-web schemes (`file://`, `gopher://`, `ftp://`, `data:`) are not rejected at the gate — classic SSRF/LFI escalation schemes.
- **Recommendation:** In the selection gate, reject any URL whose scheme is not exactly `http` or `https`; treat `host_for` returning the raw string (no hostname) as an automatic reject.
- **Acceptance Criteria:** `file://`, `gopher://`, `data:`, `ftp://`, schemeless candidates dropped at selection and never crawled.
- **Regression Tests:** Scheme-allowlist tests over `select()` for each forbidden scheme.

#### SEC-003 — Blocklist/allowlist is host-exact-match; bypassable and SSRF-ineffective
- **Severity:** High · **Category:** Security
- **Cross-references:** SEC-001 (IP-range guard is the real SSRF control, not the host blocklist).
- **Evidence:** `selection.py:14-21` compares `host_for(result.url)` against a lowercased exact-string set; `host_for` (`normalize.py:5-7`) does not strip a trailing dot or normalize IP encodings. Probed: `EVIL.com./x` → `evil.com.` (≠ `evil.com`); `0x7f000001`/`2130706433` pass as opaque strings (both loopback).
- **Problem:** (a) `internal.corp` is bypassed by `internal.corp.` or a subdomain; (b) dec/hex/oct IP encodings of a blocked IP do not match; (c) exact-host only — `evil.com` does not block `sub.evil.com`.
- **Impact:** Operators believing `DOMAIN_BLOCKLIST` guards internal hosts are wrong; cosmetic for SSRF, weak for ToS exclusion.
- **Recommendation:** Canonicalize hosts before comparison: strip trailing dot, IDNA-encode, normalize/reject IP-literal encodings (`ipaddress`), match blocklist entries as suffix/registrable-domain. Keep SSRF defense as the IP-range guard (SEC-001), not the host blocklist.
- **Acceptance Criteria:** `evil.com.`, `EVIL.COM`, `sub.evil.com`, `0x7f000001`, `2130706433` all blocked when the operator blocks the corresponding host/IP.
- **Regression Tests:** Canonicalization bypass tests (trailing dot, case, subdomain, IP-encoding) on `select()`.

#### CRAWL-002 + SOV-4 — robots.txt never honored; polite-crawl posture unenforced (merged)
- **Severity:** High · **Category:** Crawl-Safety / Sovereignty
- **Cross-references:** inv 7 (crawl politely); ties to CRAWL-003 (per-host rate limit).
- **Evidence:** No robots handling in first-party code; grep for `robots` hits only `05-licensing-and-sovereignty.md:106` (a doc aspiration). `crawl4ai_client.py:24-25` posts `{"url": url}` with no `check_robots_txt` flag, relying on the Crawl4AI default (which does not enforce robots unless configured).
- **Problem:** The service crawls regardless of a site's robots policy, contradicting inv 7 and the sovereignty/ToS posture in doc 05.
- **Impact:** ToS/legal exposure for operators; impolite crawling; IP-block/reputational risk. The compliance-facing posture a publisher advertises is not demonstrably enforced.
- **Recommendation:** Set the Crawl4AI robots-check option explicitly (`check_robots_txt`) as default; expose an env toggle for operators who own the targets; cache robots within the per-call lifetime only. Verify the exact flag for the pinned Crawl4AI version.
- **Acceptance Criteria:** A URL disallowed by its site's robots.txt is not crawled by default.
- **Regression Tests:** Robots-respected test (mock `Disallow` → URL skipped); robots-allowed test.

#### CON-1 — No API/tool version or staged-migration mechanism for the wire contract
- **Severity:** High · **Category:** Contracts
- **Cross-references:** inv 10 (staged migration); enables MCP-3 fix to land safely.
- **Evidence:** REST route is unversioned `@app.post("/search")`; MCP server id `FastMCP("thorondor")`, tool `web_search`, no version; `models.py` carries no schema/contract version field.
- **Problem:** Any future change to `/search` or `web_search` (renaming `reason` codes, changing passage shape) breaks every existing agent at once with no transition window. Agents cannot detect or pin a contract version.
- **Recommendation:** Document the stable surface: version the REST path (`/v1/search`) or add `schema_version`/`api_version` to `SearchResponse`; treat `reason` codes as a closed documented enum; make additive-only the default change policy; version the MCP tool name or expose a version in server info; document which of `stats`/`reason`/`reranked` are stable vs best-effort.
- **Acceptance Criteria:** A documented versioning policy exists; a client can determine the contract version; adding a field does not break existing agents.
- **Regression Tests:** Schema/contract snapshot test (golden JSON schema of `SearchResponse` + the tool I/O schema) that fails on any non-additive change, forcing a deliberate version bump.

#### IFACE-1 — Reranker adapter assumes integer `index`; parse errors escape as uncaught crash
- **Severity:** High · **Category:** Interfaces
- **Cross-references:** inv 9 (graded degrade), IFACE-4 / RES-4 (short/partial rerank).
- **Evidence:** `orchestrator/clients/reranker_client.py:18-36`. The shim absorbs alternate keys/paths but assumes every result item carries an int `index` (line 32). The try/except (18-25) wraps only the HTTP call, not the parse (28-36). Some Jina/Cohere-style `/rerank` responses return results in input order without an explicit index. A missing `index` raises `KeyError` straight out of `run_search`.
- **Problem:** A BYO reranker returning a valid-but-index-less shape turns the graceful-degradation path into an uncaught 500 — violating inv 9 and the contract-over-implementation BYO promise for a common shape.
- **Recommendation:** Treat a missing `index` as positional (enumerate order); wrap the parse loop so any malformed item raises `RerankerUnavailable` (already caught at `pipeline.py:104-107` → discovery-order fallback). Keep normalization behind the interface.
- **Acceptance Criteria:** An index-less `/rerank` response is handled positionally; a structurally broken response raises `RerankerUnavailable` (→ 200 with `reranked:false`), never an uncaught exception.
- **Regression Tests:** `reranker_client` tests (httpx `MockTransport`): results without index; document-echo only; malformed item → `RerankerUnavailable`.

#### IFACE-2 — Pure seams (SelectionPolicy, ResultAssembler) have no deterministic fake; pipeline tests run the real impls
- **Severity:** High · **Category:** Interfaces
- **Evidence:** `fakes.py:85,89` wire `SelectionPolicyImpl()`/`ResultAssemblerImpl()` directly into `deps()`. No `FakeSelector`/`FakeAssembler`. Five of seven seams have fakes.
- **Problem:** Pipeline tests cannot exercise branching independently of the policy impls (cannot force assembly empty to test a downstream path, or force selection to a known set); a bug in `SelectionPolicyImpl` would break unrelated pipeline tests (entanglement). Protocol substitutability is unproven for the two highest-value policy seams.
- **Recommendation:** Add `FakeSelector` (returns input unchanged or a fixed subset) and `FakeAssembler` (one passage per chunk, trivial citations); keep real impls as the default in `deps()` but make fakes available for branch isolation.
- **Acceptance Criteria:** `fakes.py` exposes a deterministic fake for all seven Protocols; at least one pipeline test uses a fake selector/assembler to isolate a branch.
- **Regression Tests:** Pipeline test using `FakeAssembler` returning `[]` asserts the empty-passages path; test using `FakeSelector` returning a single URL asserts crawl receives exactly that URL.

#### CHUNK-1 — Golden-file parity test does not exercise the DP path it claims to protect
- **Severity:** High · **Category:** Testing (Chunker)
- **Cross-references:** inv 11 (chunker no-drift), CHUNK-8 (strategy_version discipline).
- **Evidence:** `tests/test_parity.py:11-16`; golden `tests/golden/cluster_semantic@1.json`. The single case is `"The eagle soared."`×30 + blank + `"Markets fell sharply."`×30, `max=60`/`min=10`. Two blocks of identical repeated sentences → grouping dominated by the hard `max_chunk_size` cap, not semantic reward (output 48/42/48/42 is exactly greedy max-cap packing). The fake embedder gives near-identical vectors within each block, so the reward surface is flat.
- **Problem:** The golden mainly pins the size-packing path, not the semantic-optimization (DP) path. A regression breaking the reward computation, parent backtrack, or `min_chunk_size` branch could still emit this exact output. Drift in the core DP could pass — undermining the project's stated anti-drift guarantee (inv 11).
- **Recommendation:** Add a second golden with mixed, interleaved content and a fake embedder returning clearly separable per-topic vectors, sized so more than one admissible segmentation exists. Assert the DP picks the topic-coherent boundary, not the greedy one. Keep the existing case as the size-cap golden. (Adding goldens to `@1` is allowed — it documents existing behavior, not a behavior change.)
- **Acceptance Criteria:** A golden exists where the DP boundary differs from `_greedy_fallback_chunking` on the same segments+lengths, and parity pins it.
- **Regression Tests:** New mixed-topic golden; unit test asserting `_dynamic_programming_chunking` returns a different grouping than `_greedy_fallback_chunking` for a constructed similarity matrix.

#### CHUNK-2 — DP-no-solution fallback (valve 3) has no test; reachable via param overrides
- **Severity:** High · **Category:** Testing (Chunker)
- **Evidence:** `cluster_semantic.py:436-442` (`dp[n] == -inf` → `_greedy_fallback_chunking`). Valves 1 and 2 have tests; valve 3 does not. `ChunkParams` lets a caller override `initial_segment_tokens > max_chunk_tokens` (`models.py:13-17`, `strategies.py:34-38` with no cross-validation), making segments exceed `max_chunk_size`, so every candidate breaks, `dp[n]` stays `-inf`, valve 3 fires. Untested code on a failure path a public request can reach.
- **Problem:** A safety valve never executed in tests is a latent bug surface; the no-solution path is the worst-quality output and must be correct and bounded.
- **Recommendation:** Add a unit test forcing `dp[n] == -inf` (e.g. `initial=400`, `max=10`) asserting non-empty, token-bounded chunks. Separately validate overrides in `resolve_strategy` (reject `initial > max` and `min > max`) with HTTP 400, so the no-solution path is only reachable by genuine pathology.
- **Acceptance Criteria:** A forced no-solution test is green; override validation rejects inconsistent params with 400.
- **Regression Tests:** Forced-fallback unit test for valve 3; param-validation test (`initial > max` → 400).

#### CHUNK-3 — No memory-bound assertion or container `mem_limit` guarding the 400 MB ceiling
- **Severity:** High · **Category:** Performance (Chunker)
- **Cross-references:** inv 11 (OOM guard is load-bearing), API-1/SEC-005 (unbounded text drives the matrix).
- **Evidence:** Matrix bound `N²·4` bytes; at default `CHUNKER_MAX_SEGMENTS_DP=10000` ≈ 400 MB (`03-deployment.md:245`); peak higher (`np.dot` transient + sub-matrix slices). Chunker container has no `mem_limit` in `docker-compose.yml`; no test asserts the guard ordering. The guard is correct in code (checked at `:171` before `_compute_similarity_matrix` at `:226`; greedy path is O(N)) but nothing locks the ordering.
- **Problem:** A refactor moving matrix work above the guard would silently reintroduce OOM. A cannot-be-bypassed invariant must be enforced by a test, not reviewer inspection.
- **Recommendation:** (1) Unit test monkeypatching `MAX_SEGMENTS_FOR_DP` low and asserting `_compute_similarity_matrix` is never called (patch it to raise) while a valid greedy result returns. (2) Memory-bound test asserting the matrix is float32 and `N·N·4` bytes just under the cap. (3) Set `mem_limit` on the chunker in compose sized to the documented peak.
- **Acceptance Criteria:** Above-cap input provably never allocates the NxN matrix; matrix dtype/size asserted; container has an explicit memory limit.
- **Regression Tests:** Memory-bound assertions (dtype/byte-size); guard-cannot-be-bypassed test (matrix builder patched to raise above cap).

### MEDIUM

#### PIPE-3 — `no_results_from_discovery` reason reused for two distinct empty states
- **Severity:** Medium · **Category:** Pipeline · **Inv:** 10 (reason codes)
- **Evidence:** `pipeline.py:70-72` (empty merge) and `:80-82` (empty selection) both emit `no_results_from_discovery`; `test_pipeline.py:38-42` locks it in. When selection filters everything out, `stats.urls_discovered > 0` while `urls_selected == 0`, contradicting the reason.
- **Recommendation:** Add `no_urls_after_selection` for the selection-empty branch; keep `no_results_from_discovery` for the truly-empty merge. Additive. (Note: this is a new `reason` code — record it in the closed enum per CON-1.)
- **Acceptance Criteria:** Empty discovery → `no_results_from_discovery`; non-empty discovery + empty selection → `no_urls_after_selection` with `urls_discovered > 0`, `urls_selected == 0`.
- **Regression Tests:** Replace `test_pipeline.py:38-42`; add a fake-backed test where allowlist matches nothing.

#### PIPE-4 — `token_budget`/`max_urls` defaulting duplicated in REST and pipeline
- **Severity:** Medium · **Category:** Pipeline · **Inv:** 8 (root cause of MCP drift)
- **Evidence:** `app.py:32-37` resolves into a `model_copy`; `pipeline.py:54-55` resolves again. REST pre-resolution is effectively dead; MCP does not pre-resolve and relies on the pipeline copy.
- **Recommendation:** Remove the `model_copy` defaulting from `app.py:32-37`; let `run_search` be the sole resolver; surfaces pass the raw request through. Also fixes the MCP-3/MCP-4 root cause.
- **Acceptance Criteria:** Defaulting exists only in `run_search`; REST passes the request unmodified; existing REST tests pass.
- **Regression Tests:** REST test asserting an unset `token_budget` yields a response budgeted to `deps.default_token_budget`.

#### PIPE-5 — `include_raw_markdown` re-derives the URL join key and silently drops mismatches
- **Severity:** Medium · **Category:** Pipeline
- **Evidence:** `pipeline.py:113-120`; match via `normalize_url(citation.url)` against `{normalize_url(page.url): page}`. If the chunker echoes a transformed URL or page URL differs after redirects, the guard silently omits raw markdown with no signal.
- **Recommendation:** Carry a stable join key (`citation_id` → source page) through provenance rather than re-deriving via URL normalization. Keep the stage pure.
- **Acceptance Criteria:** Every citation with a crawled page gets `raw_markdown`; a citation legitimately without a page is observable, not silently dropped.
- **Regression Tests:** Fake-backed test where chunker metadata echoes a trailing-slash/utm variant; assert raw markdown attaches for all citations.

#### IFACE-3 — SelectionPolicy takes pre-merged blocklist/allowlist; source-merge lives in the pipeline
- **Severity:** Medium · **Category:** Interfaces
- **Evidence:** `pipeline.py:74-78` builds the effective blocklist/allowlist before `select`; `selection.py:14-15` lower-cases them again (double lowercasing).
- **Recommendation:** Either move source-merging into the selector (pass req fields + settings blocklist, let `select` combine, stays pure) or document selection as filter+cap with source resolution as glue. Remove the double lowercasing.
- **Acceptance Criteria:** Domain allow/block resolution owned by one layer, unit-testable without the full pipeline; no double normalization.
- **Regression Tests:** Pure unit test of merge-and-filter covering `exclude_domains` + operator blocklist + allowlist together.

#### IFACE-4 + RES-4 — Reranker may return fewer chunks than input; degraded mode returns MORE than success mode (merged)
- **Severity:** Medium · **Category:** Interfaces / Resilience · **Inv:** 6 (budget-not-count), 9 (graded degrade)
- **Evidence:** `interfaces.py:34-35` silent on coverage; `reranker_client.py:30-36` appends only items whose `index` is in range; `pipeline.py:101-103` sets `reranked=True` regardless; fallback at `:105` preserves ALL chunks. An empty `results` body (status 200) yields zero scored chunks, and the pipeline does not re-check for empty after rerank (the `no_chunks_after_dedup` guard is before rerank).
- **Problem:** A reranker returning top-k or an empty 200 silently drops chunks with `reranked=true` and no `reason` — indistinguishable from genuinely nothing relevant. Degraded mode (full passthrough) can return more candidates than success mode (an inversion). Undermines inv 6 if the reranker truncates before the assembler applies the budget.
- **Recommendation:** Document the contract: rerank returns a score for every input chunk (reordering allowed, dropping not). In the client, back-fill any missing chunk with a floor score so assembly sees all candidates and the TOKEN BUDGET decides output. Treat a wildly short / empty `results` body as `RerankerUnavailable` (degrade) or back-fill, not a silent empty. Add a post-rerank empty guard that sets a `reason`.
- **Acceptance Criteria:** For N input chunks the client yields N ScoredChunks regardless of how many the server scored; an empty results body produces a degraded `reranked=false` or an explicit `reason`, never `reranked=true` + silent empty.
- **Regression Tests:** `reranker_client` test (server scores 1 of 3 → all 3 present, 2 floor-scored; empty results → degrade/back-fill); pipeline test with a fake reranker returning fewer results than inputs asserting chunk count preserved or `reranked=false`.

#### IFACE-5 — Extractor/Chunker hide partial-failure provenance; one bad page hard-fails the whole chunk batch
- **Severity:** Medium · **Category:** Interfaces · **Inv:** 9
- **Evidence:** `chunker_client.py:15-46` loops one POST `/chunk` per page and aborts the batch on any non-200 → `ChunkerUnavailable` → 503 for all pages. `crawl4ai_client.py:18-38` drops failed URLs silently with no per-URL signal.
- **Problem:** A single per-page 4xx is not "chunker down" but escalates to a full-call 503. Partial tolerance is asymmetric: crawl tolerates, chunk does not.
- **Recommendation:** Distinguish chunker-unreachable/5xx (→ `ChunkerUnavailable`, hard-fail per inv 9) from this-page-failed/4xx (→ skip page, continue). Keep the batch Protocol. Optionally enrich stats with per-URL outcomes.
- **Acceptance Criteria:** A single page returning 422 from `/chunk` is skipped, others still chunk, call returns 200; only an unreachable/5xx chunker hard-fails.
- **Regression Tests:** `chunker_client` test (MockTransport) where page 2 of 3 returns 422 → pages 1 and 3 present; 5xx → `ChunkerUnavailable`.

#### CHUNK-4 — Token proxy is word count, not the embedding/LLM tokenizer; `token_budget` is silently word-budget end-to-end
- **Severity:** Medium · **Category:** Chunker · **Inv:** 6 (budget-not-count) · **Cross-ref:** map D9
- **Evidence:** `app.py:21-24` (`_length = len(text.split())`); `cluster_semantic.py:95`; `recursive_splitter.py:49`. `max_chunk_tokens=400`/`min=50` and the orchestrator `token_budget` are word budgets. Real subword tokenizers (bge-m3) produce ~1.3-1.6 tokens/word (far more for code/CJK/URLs), so a "400-token" chunk can be 600+ real tokens.
- **Problem:** `token_budget` (the primary assembly control) is honored only approximately, with content-dependent error (worst for code/non-Latin). `max_chunk_size` does not actually bound what a downstream model receives.
- **Recommendation:** Either (a) document loudly that `token` means `word` and size budgets are approximate, or (b) wire a real tokenizer (a tiktoken proxy is cheap and tracks subword counts far better). **Any swap of `_length` changes every boundary → must be a `strategy_version` bump (`cluster-semantic@2`) with regenerated goldens** (inv 11, CHUNK-8). *Recommended resolution: ship option (a) now (a one-line doc/contract change, no drift) and schedule option (b) as a deliberate `@2` in the chunker-robustness phase.*
- **Acceptance Criteria:** Token semantics documented as word-based, or replaced by a real tokenizer under a new strategy version with new goldens.
- **Regression Tests:** Golden parity regenerated under the new `strategy_version`; test asserting `token_count` of a known code/CJK string is within tolerance of the chosen tokenizer.

#### CHUNK-5 — Reward objective is size-biased; `min_chunk_size` is a soft floor
- **Severity:** Medium · **Category:** Chunker
- **Evidence:** `cluster_semantic.py:371-389`, `:490-514`, `:147-158`. Reward grows ~quadratically with chunk size (positive cosines), so DP is biased toward fewer/larger chunks capped by `max_chunk_size`. Sub-min chunks are permitted when `i == n` (final) or `dp[j] == -inf`; a whole doc under `min` short-circuits to one chunk. The docstring (`:84`) overstates `min_chunk_size` as a hard guarantee.
- **Problem:** Faithful to Chroma (by design), but callers relying on `min_chunk_tokens` as a hard floor are surprised.
- **Recommendation:** Document `min_chunk_size` as a soft floor (final chunk + degenerate cases exempt). No code change if soft semantics are intended (they match Chroma). Any merge of a sub-min trailing chunk is boundary-affecting → strategy bump.
- **Acceptance Criteria:** `min_chunk_size` documented as soft; behavior matches docs.
- **Regression Tests:** Unit test asserting a short single-paragraph doc returns one sub-min chunk (documented), not an error.

#### CHUNK-6 — `start_index`/`end_index` are approximate and can be wrong on repeated text
- **Severity:** Medium · **Category:** Chunker
- **Evidence:** `cluster_semantic.py:319-345`, `:555`, `:560-561`; `models.py:31-32`. Offsets recovered by `text.find(...)`; chunk text rebuilt by joining stripped segments with a single space, so `text[start:end] != chunk.text` whenever the source had non-single-space whitespace. On repeated content, `find` can land on the wrong occurrence. The test only checks `0 <= start <= end <= len(text)`.
- **Recommendation:** Document offsets as approximate (not byte-exact, may not round-trip), or make chunk text a verbatim `text[char_start:char_end]` slice (text-affecting → strategy bump if it changes text).
- **Acceptance Criteria:** Contract states offsets are approximate, or a test asserts `text[start:end]` round-trips to chunk text.
- **Regression Tests:** Unit test on duplicated-text input asserting offsets are monotonically non-decreasing across chunks.

#### CHUNK-7 — Embedding seam drops scheme + path and requires an explicit port
- **Severity:** Medium · **Category:** Chunker · **Inv:** BYO seams stay HTTP endpoints · **Cross-ref:** map D5, SEC-008
- **Evidence:** `embedding_function.py:21-31` parses to `(hostname, port)` and rebuilds `http://HOST:PORT/v1/embeddings`. Forces `http` (downgrades https), requires an explicit port (ValueError if absent), drops any path prefix. Every orchestrator client uses full base URLs; this seam is the lone exception.
- **Problem:** A TLS-terminated managed embedding endpoint behind a path prefix and implicit 443 cannot be configured without modifying code — undermining contract-over-implementation (and blocking SEC-008's secret-server use case).
- **Recommendation:** Use the full URL: strip a trailing slash and POST to `ENDPOINT/v1/embeddings` (or treat a URL already ending in `/embeddings` as full); preserve scheme and path; default the port from the scheme. Behavior-neutral for the bundled `http://embedding:80` default.
- **Acceptance Criteria:** https, implicit-port, and path-prefixed endpoints reach the correct URL with the correct scheme.
- **Regression Tests:** Unit tests for `https://host/v1` (no explicit port), `http://host:8080/prefix`, and the existing `http://host:port`.

#### CHUNK-8 — Golden regeneration undocumented; no guard ties a `_length`/algorithm change to a `strategy_version` bump
- **Severity:** Medium · **Category:** Testing (Chunker) · **Inv:** 11 · **Cross-ref:** CHUNK-1, CHUNK-4
- **Evidence:** `strategies.py:13-21`; `tests/test_parity.py`. `strategy_version` is stamped correctly and unknown versions 400, but there is no test pinning the `cluster-semantic@1` params (`max=400/min=50/initial=50`) and no documented golden-regeneration procedure — so an edit to params, `_length`, separators, or the reward formula changes boundaries while still being version 1, and the parity test would just be edited to match.
- **Recommendation:** (1) Add a test pinning the `cluster-semantic@1` registry entry to exact values, so changing them forces a conscious `@2`. (2) Add a documented `scripts/gen_golden.py` / `--update-goldens` flag. (3) Make the CHUNK-1 mixed-topic golden part of the frozen `@1` set.
- **Acceptance Criteria:** Editing `@1` params fails a test; golden regeneration is a documented single reviewable command.
- **Regression Tests:** Strategy-params pin test; golden parity (existing + mixed-topic).

#### OBS-2 — No correlation/trace IDs across agent → orchestrator → chunker → embedding/reranker
- **Severity:** Medium · **Category:** Observability
- **Evidence:** Grep for request-id/correlation/trace-id/contextvar: no matches. No middleware; no downstream client forwards a correlation header.
- **Recommendation:** ASGI middleware that reads/generates `X-Request-ID`, stores it in a contextvar, includes it in structured logs, forwards it on every downstream httpx call (orchestrator → chunker → embedding), returns it in the response and on the MCP path.
- **Acceptance Criteria:** A request with `X-Request-ID` appears in orchestrator and chunker logs for that call and is forwarded downstream.
- **Regression Tests:** Assert the header is propagated to a mocked chunker/reranker transport.

#### OBS-3 + SEC-006 — No structured logging; ad-hoc string logs with no redaction policy; full URLs logged (merged)
- **Severity:** Medium · **Category:** Observability / Security
- **Evidence:** Only orchestrator logging is `logger.warning` in `crawl4ai_client.py:27,34` (full failing URL at WARNING). No logging config in `app.py`. The chunker logs embedding endpoint at INFO and raw `error_text`/`response.text` on failure (`embedding_function.py:142,164-168`). Crawled bodies not logged today, but no policy enforces it.
- **Problem:** (1) The orchestrator emits essentially nothing for success/degraded calls, so degradation is invisible in logs; (2) no redaction guarantee — a credential in a BYO endpoint URL/query string or an echoed upstream error can land in logs; (3) full discovered URLs (with sensitive query strings) are logged.
- **Recommendation:** Add structured (JSON) logging with a per-call summary line (query hash, sub-query count, urls discovered/selected/crawled_ok, reranked, reason, elapsed_ms — never the raw query body or markdown). Add a redaction helper that strips userinfo/query from endpoint URLs before logging; log only the URL host (or hashed/elided URL) on crawl failure; never log full upstream error bodies above DEBUG; never log page bodies. Document the policy.
- **Acceptance Criteria:** A degraded call emits one structured log line with the stats summary and no secrets; endpoint/URL logging strips credentials and query strings; no page body is logged at any level.
- **Regression Tests:** Assert a failing embedding-call log record contains no configured secret token; assert the per-call summary line is emitted; scan emitted log records for body/query substrings.

#### OBS-4 + RES-5 — `stats` block incomplete; embedding degradation invisible end-to-end (merged)
- **Severity:** Medium · **Category:** Observability / Resilience · **Inv:** 9, 10
- **Evidence:** `SearchStats` (`models.py:26-36`) lacks explicit `urls_crawled_failed` (only derivable by subtraction), content-dedup drop count (`pipeline.py:90` reassigns with no count), and a chunk-strategy/embedding-degraded signal. When embedding is down the chunker falls back to greedy token chunking and returns 200 chunks; `ChunkerClient` reads only `text/token_count/position`, discarding any signal. `ChunkResponse` carries no degradation flag.
- **Problem:** A partial crawl (6 selected, 1 ok) and dedup drops are invisible; an embedding-down graded degrade (inv 9) is completely unobservable — operators cannot tell a high-quality semantic result from a greedy-token fallback.
- **Recommendation:** Add `urls_crawled_failed` and `chunks_after_dedup` (or `pages_deduped`) to `SearchStats`. Have the chunker surface the actual strategy used (e.g. `chunk_strategy`/`fallback` in the response or per-chunk metadata); have `ChunkerClient` propagate it; add a `chunk_strategy`/`embedding_degraded` field to `SearchStats`. Surface the chunker's nested embedding health in the orchestrator `/healthz` aggregate.
- **Acceptance Criteria:** A run with 3 selected / 1 crawled ok reports `urls_crawled_failed == 2`; a dedup drop is counted; with embedding down, `/search` stats expose fallback mode and `/healthz` distinguishes chunker-up-but-embedding-down.
- **Regression Tests:** Pipeline test asserting failed-crawl and dedup counters; failure-injection test (embedding refused) asserting chunker returns a fallback marker and orchestrator stats reflect it.

#### RES-2 — Crawl fan-out has no per-task cancellation; one slow target stalls the call
- **Severity:** Medium · **Category:** Resilience · **Inv:** 9 (one slow target never stalls the call) · **Cross-ref:** RES-6
- **Evidence:** `crawl4ai_client.py:18-38` `awaits asyncio.gather` over `one(url)` for each URL; per-URL `httpx` timeout is enforced but `gather` waits for the slowest of all tasks. With `CRAWL_CONCURRENCY=4` and 6 URLs, the last-batch slow URL holds the whole extract up to `crawl_timeout_s`; a dribbling server can extend a single fetch beyond it. Wall-clock bound is `ceil(N/concurrency) * crawl_timeout_s`, not a single knob.
- **Recommendation:** Wrap the fan-out in an overall budget (`async with asyncio.timeout(total)`) and on expiry proceed with whatever completed (cancel the rest); use explicit `httpx.Timeout(connect, read)`; reuse one `AsyncClient` (RES-6). Optionally support first-K-pages-then-cancel.
- **Acceptance Criteria:** With one URL that sleeps far longer than others, extract returns the fast pages within the overall budget and the slow task is cancelled.
- **Regression Tests:** Failure-injection test (one URL hangs) asserting extract returns the others within the budget and the pipeline still yields `urls_crawled_ok < selected` and a 200.

#### RES-3 — Discovery fan-out unbounded by sub-query count (DoS amplification via the planner)
- **Severity:** Medium · **Category:** Resilience · **Inv:** 7 · **Cross-ref:** SEC-005, treat planner as untrusted (SEC posture)
- **Evidence:** `pipeline.py:62-64` `asyncio.gather` over discovery for each subquery, no semaphore. `LlmPlanner.plan` does not cap the parsed array (`planner.py:37-39`); a misbehaving or prompt-injected LLM can return an arbitrarily long JSON array, each element a concurrent SearXNG request.
- **Recommendation:** Cap planner output (e.g. `planned[:3]`) and/or bound the discovery gather with an `asyncio.Semaphore`. Treat the planner as untrusted input.
- **Acceptance Criteria:** A planner returning 50 sub-queries results in at most N (e.g. 3) discovery calls.
- **Regression Tests:** Failure-injection test with a `FakePlanner` returning more than K sub-queries; assert discovery is called at most K times.

#### SEC-004 — No allowlist-only locked-down deployment mode
- **Severity:** Medium · **Category:** Security
- **Evidence:** Per-request `domains` allowlist exists (`models.py:46`, `pipeline.py:77`), but no operator-level allowlist env var or allowlist-only mode; `settings.py` exposes only `DOMAIN_BLOCKLIST`. With no per-request `domains`, allowlist is None and everything passes.
- **Recommendation:** Add `DOMAIN_ALLOWLIST` env (and optional `ALLOWLIST_ONLY=true`) merged into the selection gate so that, when set, only those hosts are ever crawled regardless of request.
- **Acceptance Criteria:** With `ALLOWLIST_ONLY`, a request for a non-allowlisted domain selects zero URLs.
- **Regression Tests:** Operator-allowlist-enforced test; allowlist-only-overrides-request test.

#### CRAWL-003 — No per-host rate limiting; concurrency cap unbounded
- **Severity:** Medium · **Category:** Crawl-Safety · **Inv:** 7 · **Cross-ref:** CRAWL-002, SEC-005
- **Evidence:** `crawl4ai_client.py:19` uses a global `asyncio.Semaphore(self.concurrency)` and a per-request timeout, but no per-target-host throttle; `CRAWL_CONCURRENCY` has no validated upper bound.
- **Recommendation:** Add a small per-host concurrency/delay limit alongside the global semaphore; validate `CRAWL_CONCURRENCY` against a sane ceiling at settings load.
- **Acceptance Criteria:** Two URLs on the same host are not fetched concurrently beyond a per-host limit; `CRAWL_CONCURRENCY` above the ceiling rejected/clamped.
- **Regression Tests:** Per-host rate-limit test; concurrency-ceiling settings test.

#### MCP-5 — Tool docstring is a single line; omits budget semantics, return shape, when-to-call
- **Severity:** Medium · **Category:** MCP / Contracts
- **Evidence:** `mcp_server.py:26` docstring is one line. The design doc (`04-...md:116-130`) specifies a far richer docstring with usage guidance, `Args:`, and `Returns:`.
- **Problem:** The FastMCP docstring is what an LLM sees as the tool description. The one-liner tells the model nothing about when to call it, that `token_budget` is the sizing control (inv 6), what `max_urls` does, or the return shape to parse.
- **Recommendation:** Port the documented docstring; keep it in sync with the actual return shape once MCP-3/CON-2 are fixed.
- **Acceptance Criteria:** Tool description includes when-to-call, `token_budget` semantics, `max_urls`, and an accurate `Returns` shape matching what the tool emits.
- **Regression Tests:** Test asserting the tool description/schema contains the budget-semantics and return-shape text.

#### LIC-6 — No `NOTICE`/attribution file for permissive bundled and pip dependencies
- **Severity:** Medium · **Category:** Licensing
- **Evidence:** No `NOTICE`/`AUTHORS` tracked. Apache-2.0 requires preserving copyright/license notices and any upstream `NOTICE` contents that apply to the exact artifact; Crawl4AI (`unclecode/crawl4ai:latest`) and Apache-2.0 pip deps need tag-by-tag verification before redistribution.
- **Recommendation:** Add a `NOTICE`/`THIRD-PARTY-NOTICES.md` listing each bundled image and pip dep with pinned version, license, upstream link, and any required upstream `NOTICE`/attribution text (Crawl4AI Apache-2.0, SearXNG AGPL-3.0 referenced upstream, TEI/llama.cpp, Python deps). Pin the Crawl4AI tag while here.
- **Acceptance Criteria:** A third-party-notices file enumerates bundled images + pip deps with licenses; required notices for the exact pinned Crawl4AI artifact are preserved; referenced from the README.

#### LIC-7 — Chroma Research credited in code but missing from the README
- **Severity:** Medium · **Category:** Licensing · **Inv:** 12 (credit Chroma)
- **Evidence:** Credit present in `cluster_semantic.py:4-6,17`; `README.md` has no Chroma mention. `CLAUDE.md` and doc 05 require crediting in README *and* code header.
- **Recommendation:** Add a "Credits / Attribution" README section crediting Chroma Research, "Evaluating Chunking Strategies for Retrieval" (July 2024), stating the implementation is first-party.
- **Acceptance Criteria:** README credits Chroma Research naming the paper and date; does not represent the algorithm as novel.

#### SOV-1 — Sovereignty / optional-outbound disclosures absent from the README
- **Severity:** Medium · **Category:** Sovereignty
- **Evidence:** Doc 05 documents the outbound surface (commercial search API key in SearXNG `settings.yml`; remote model endpoints); README has no sovereignty section and no disclosure.
- **Recommendation:** Add a "Sovereignty & outbound network" README section: core stack reaches only the searched sites; optional outbound = (1) commercial search API key in `settings.yml`, (2) remote BYO model endpoints — both explicit operator choices; data at rest is ephemeral.
- **Acceptance Criteria:** README discloses the two optional outbound paths and states the core stack has no mandatory external SaaS.

### LOW

#### PIPE-6 — Documented pre-filter stage (stage 8) absent
- **Severity:** Low · **Category:** Pipeline
- **Evidence:** `pipeline.py:90-101` goes chunk → rerank with no cosine narrowing (map row 8 NOT IMPLEMENTED). Every chunk is shipped to the reranker, so rerank cost is unbounded by chunk count.
- **Recommendation:** Implement pre-filter as a SEPARATE optional impure stage (BYO embedding endpoint behind a new Protocol, kept OUT of the pure stages) or update doc 01 to mark stage 8 deferred. Do not fold cosine into selection/assembly (breaks inv 7).
- **Acceptance Criteria:** Docs and code agree; if implemented, a discrete stage with its own Protocol and fake.
- **Regression Tests:** If implemented, a fake pre-filter with a deterministic stub embedding narrowing N to k chunks, asserted in a pipeline test.

#### PIPE-7 — `merge_dedup` discards duplicate provenance with no corroboration signal
- **Severity:** Low · **Category:** Pipeline · **Cross-ref:** PIPE-2 (compounds zero-score selection)
- **Evidence:** `merge.py:6-14` keeps only the single highest-score result per normalized URL; no aggregation across sub-queries/engines.
- **Recommendation:** Optionally fold a small corroboration bonus (count of source sets containing the URL) into the kept score, staying pure/deterministic; or document merge as max-score only.
- **Acceptance Criteria:** Documented behavior matches code; if aggregation added it is pure/deterministic.
- **Regression Tests:** Pure unit test: a URL present in 3 result sets ranks above an equal-max URL present in 1.

#### PIPE-2 — Selection gate caps count but not quality when discovery scores are zero
- **Severity:** Low (re-graded; see note) · **Category:** Pipeline · **Inv:** 7
- *Note: the specialist filed PIPE-2 as High. It is consolidated here adjacent to its root cause (zero-score discovery) and downgraded to Low because rerank is the real quality gate (inv 6) and the invariant the High SSRF/bounds findings protect — that selection "protects the crawl" — is about volume/safety, not relevance ordering. The relevance concern is real but quality-of-results, not correctness/safety. Recorded explicitly so it is not lost.*
- **Evidence:** `selection.py:14-22` applies `[:max_urls]` with sort key `(-score, url)`; `searxng_client.py:35` defaults score to `0.0` when the engine omits it. When all scores are 0.0 (common), selection degenerates to alphabetical-by-URL.
- **Recommendation:** Derive a rank-based fallback score in discovery (`score or 1/(rank+1)` using SearXNG result order) so merge/selection have a meaningful ordering signal; OR document that selection is count-only and relevance lives in rerank. Do NOT add network I/O to selection (preserves inv 7 purity).
- **Acceptance Criteria:** With all-zero engine scores, selection preserves a deterministic relevance-meaningful order (discovery rank), not alphabetical; `max_urls` still caps count.
- **Regression Tests:** Pure unit test: 10 zero-score results shuffled, assert kept top-N matches discovery/rank order, not `sorted(url)`.

#### PIPE-8 — `decompose` default awaits a no-op IdentityPlanner; toggle is misleading
- **Severity:** Low · **Category:** Pipeline
- **Evidence:** `pipeline.py:58`; `models.py:44` `decompose=True`; `build_deps_from_settings` installs IdentityPlanner when LLM unset. Every default call awaits a no-op.
- **Recommendation:** Document that `decompose` has effect only when an LLM planner is configured; consider default `decompose=False`. (Inv 6 holds — rerank scores the original query.)
- **Acceptance Criteria:** `decompose` semantics documented; default output unchanged (`[query]`).
- **Regression Tests:** Add a test asserting IdentityPlanner yields `[query]` regardless.

#### IFACE-6 — Fakes omit failure/degradation coverage for several seams
- **Severity:** Low · **Category:** Interfaces · **Inv:** 9 · **Cross-ref:** IFACE-2
- **Evidence:** No `DownExtractor` (covered by `EmptyExtractor`), no partial-result fakes (`PartialChunker`, `PartialExtractor`), no `FakeSelector`/`FakeAssembler`.
- **Recommendation:** Add `PartialChunker` and `PartialExtractor`; ensure a Down/Empty/Partial fake exists for every seam whose failure mode the invariants describe.
- **Acceptance Criteria:** Every failure mode in arch-map §8 is reachable through a deterministic fake with no network.
- **Regression Tests:** Pipeline tests using the new fakes asserting the documented reason/degradation for each.

#### IFACE-7 — ResultAssembler Protocol leaks the Pydantic wire models into the internal seam layer
- **Severity:** Low · **Category:** Interfaces · **Inv:** 10
- **Evidence:** `interfaces.py:4-5` imports `Citation`, `Passage` (wire models) into `ResultAssembler`; the other six seams use only internal dataclasses.
- **Recommendation:** Have `ResultAssembler` return internal dataclasses (`AssembledPassage`/`AssembledCitation` in `types.py`); let `run_search` project them onto the wire models — keeps the pure stage independent of Pydantic so the wire schema can version (CON-1) without disturbing policy code.
- **Acceptance Criteria:** `ResultAssembler`/`assembly.py` import nothing from `models.py`; wire projection happens at the boundary.
- **Regression Tests:** Assembly unit tests assert on internal types; a pipeline test asserts faithful projection (ids, urls, token_count preserved).

#### CHUNK-9 — Sync `def` endpoint runs in the threadpool (correct); shared blocking embedder + thread cap bound concurrency
- **Severity:** Low · **Category:** Performance (Chunker)
- **Evidence:** `app.py:32-33` `def chunk` runs in the AnyIO worker threadpool (event loop not stalled — invariant upheld). Default threadpool ~40 threads; the shared `_embedder._progress_callback` is never set in the FastAPI path (no cross-request contamination today).
- **Recommendation:** Keep the sync `def`; do not add `set_progress_callback` in the request path (would introduce shared mutable state); document horizontal scaling (compose replicas).
- **Acceptance Criteria:** No shared mutable per-request state on `_embedder`; scaling guidance documented.
- **Regression Tests:** Concurrency test issuing N parallel `/chunk` calls asserting independent, correct results.

#### CHUNK-10 — Reward LRU is correct and per-run; document it must not be hoisted to module scope
- **Severity:** Low · **Category:** Performance (Chunker) · **Inv:** 3 (stateless)
- **Evidence:** `cluster_semantic.py:425-428` `get_reward` is a closure inside `_dynamic_programming_chunking`, cleared per run (`:460`); captures the run `similarity_matrix`.
- **Recommendation:** Add a one-line comment that the cache is intentionally per-DP-run and must not be hoisted to module/class scope; optionally lower the default bound.
- **Acceptance Criteria:** Comment documents per-run lifetime; a test asserts rewards never cross two different matrices.
- **Regression Tests:** Unit test running DP on two matrices back-to-back asserting rewards reflect each (no stale cache).

#### CHUNK-11 — `avg_similarity` log hard-codes the diagonal as `n`; fragile if normalization changes
- **Severity:** Low · **Category:** Chunker (cosmetic)
- **Evidence:** `cluster_semantic.py:230` subtracts a literal `n` for the diagonal; holds only because rows are unit-normalized (zero-vector guard sets diagonal to 0, over-correcting in that edge case). Log-only.
- **Recommendation:** Use `np.trace(similarity_matrix)` instead of literal `n`, or drop the stat.
- **Acceptance Criteria:** Diagonal correction uses `np.trace`, or the line is removed.

#### CHUNK-12 — Greedy-semantic threshold and single-oversized-segment handling untested for boundary correctness
- **Severity:** Low · **Category:** Testing (Chunker) · **Cross-ref:** CHUNK-2, CHUNK-3
- **Evidence:** `test_oom_guard_uses_greedy_semantic` only asserts non-empty; does not check the 25th-percentile break threshold, `max_chunk_size` respect, or the nested embedding-failure check (`:668-670`). `_greedy_fallback_chunking` emits a lone oversized segment unverified.
- **Recommendation:** Strengthen the OOM-guard test to assert greedy-semantic chunks satisfy `token_count <= max_chunk_size` (except a lone oversized segment) and that a clear topic boundary becomes a chunk boundary; add a test for `_greedy_fallback_chunking` with one oversized segment.
- **Acceptance Criteria:** Greedy-semantic and greedy-fallback paths have size-respecting and boundary-placement assertions.
- **Regression Tests:** Forced-fallback unit tests (valve 1 nested embedding failure; greedy size bounds; oversized-segment handling).

#### RES-6 — httpx clients created per call/per URL; no connection reuse or pool ceiling
- **Severity:** Low · **Category:** Operations · **Cross-ref:** RES-2
- **Evidence:** Every client opens `httpx.AsyncClient(...)` per invocation (`searxng_client.py:20`, `crawl4ai_client.py:24` per URL, `chunker_client.py:18`, `reranker_client.py:19`, `planner.py:19`, `app.py:49` per healthz probe; chunker `httpx.Client` per batch). No shared client, pool, or limits.
- **Recommendation:** Construct one `AsyncClient` per stage client (with `httpx.Limits`) at deps-build time, reuse across calls, close on shutdown; share one client across the whole crawl fan-out.
- **Acceptance Criteria:** A burst of N concurrent searches does not open N×sockets per backend.
- **Regression Tests:** Assert client construction count is O(1) per stage per process (mock `httpx.AsyncClient`).

#### RES-7 — No retries with caps/jitter on transient backend failure
- **Severity:** Low · **Category:** Resilience
- **Evidence:** No retry/backoff logic anywhere. A transient 502 from SearXNG hard-fails the call; a transient crawl failure permanently drops the URL.
- **Recommendation:** Either document the no-retry stance as a deliberate anti-retry-storm choice, or add bounded (1-2) jittered retries on idempotent GETs only (SearXNG discovery, crawl); never retry the chunker multi-page loop blindly.
- **Acceptance Criteria:** Documented; if implemented, retries bounded/jittered and do not multiply fan-out.
- **Regression Tests:** n/a unless implemented.

#### RES-8 — `RERANKER_HEALTH_PATH` default mismatches compose
- **Severity:** Low · **Category:** Operations · **Cross-ref:** RES-1/API-2, map D4
- **Evidence:** `settings.py:61` defaults `/healthz` while `.env.example:13` and `docker-compose.yml:22` set `/health`. The cold-path `/healthz` reranker probe uses this.
- **Recommendation:** Align the default to `/health` (matches TEI and compose) or fix the env files; make all three sources agree.
- **Acceptance Criteria:** Code default, `.env.example`, and compose use the same reranker health path.
- **Regression Tests:** Settings test asserting the default equals the compose value.

#### OBS-5 — `reranked=false` conflates rerank-degraded with rerank-never-reached
- **Severity:** Low · **Category:** Observability
- **Evidence:** `_empty_response` (`pipeline.py:47-49`) returns `reranked=false`/`tokens_returned=0` regardless of which early-return fired.
- **Recommendation:** Treat `reranked` as meaningful only when `reason` is None (document), or use a tri-state (null when rerank not reached).
- **Acceptance Criteria:** Documentation/model makes rerank-not-reached distinguishable from rerank-degraded.
- **Regression Tests:** Assert `reason` present implies consumers ignore `reranked`.

#### OBS-6 + SOV-3 — Dead `CACHE_BACKEND`/`REDIS_URL` settings advertise a non-existent cache (merged)
- **Severity:** Low · **Category:** Operations / Sovereignty · **Inv:** 1, 3, 4 · **Cross-ref:** map D1
- **Evidence:** `CACHE_BACKEND` (`settings.py:45,68`, default `memory`) and `REDIS_URL` (`.env.example:23`) are parsed/advertised but there is no cache class, injection point, or caching anywhere. `03-deployment.md:186-187` and `05-...:114,132` document them as live knobs.
- **Problem:** An operator can set `CACHE_BACKEND=redis` + `REDIS_URL` and observe zero behavioral change. A misleading operability surface; dead `REDIS_URL` could confuse a sovereignty audit (looks like external egress).
- **Recommendation (resolution):** **Remove the dead settings and docs now** (cheapest, keeps the sovereignty surface honest). If a cache is implemented later it MUST be an *injected* Protocol seam in `PipelineDeps` (never owned by pipeline logic), keyed by the same `normalize_url`, with disabling it changing latency only — never the passages/citations/stats contract (inv 1, 4). *Conflict resolved in favor of inv 1/4: implementing-now was rejected because no caching need is demonstrated and an in-pipeline cache would risk inv 1.*
- **Acceptance Criteria:** Either the settings are gone and docs match code, or a cache exists as an injected seam with a test proving identical results with cache on/off/none (only `elapsed_ms` differs).
- **Regression Tests:** If implemented later: parity test (same query, `CACHE_BACKEND` none/memory/redis → byte-identical passages/citations); TTL-expiry; URL-normalization key-collision test.

#### SEC-007 — Crawled evidence carries no untrusted-content boundary marker (prompt-injection)
- **Severity:** Low · **Category:** Security · **Inv:** 5 (evidence not prose — upheld)
- **Evidence:** Passages flow verbatim (`crawl4ai_client.py:30` → chunker → `assembly.py:30-39` → response). No provenance/labeling. The planner hop sends only the USER QUERY (not crawled text) — good.
- **Problem:** A consuming agent cannot distinguish untrusted web text from its own instructions; an injected instruction inside a crawled page reaches the agent unmarked.
- **Recommendation:** Add a per-passage `provenance=external_web`/`trust=untrusted` field (or wrap text with an explicit delimiter) on REST and MCP, and document that downstream agents must treat passage text as data, never instructions. Keep the planner hop free of crawled text (it already is).
- **Acceptance Criteria:** Every returned passage is labeled as external-untrusted on both surfaces.
- **Regression Tests:** Boundary-marker-present test on REST and MCP; planner-receives-no-crawled-text test.

#### SEC-008 — BYO model endpoints carry no auth-header support
- **Severity:** Low · **Category:** Security · **Cross-ref:** CHUNK-7
- **Evidence:** No `Authorization`/`Bearer`/api_key handling in any client. The chunker `EmbeddingFunction` forces `http://host:port`, so an HTTPS token-authenticated embedding server cannot be used. No secret is currently logged or in stats/errors (that part holds).
- **Recommendation:** Add optional per-seam `*_API_KEY` env vars sent as `Authorization: Bearer`; let `EMBEDDING_ENDPOINT` honor https + path like the orchestrator clients (CHUNK-7). Confirm keys are env-only, never logged, never in responses/stats/errors.
- **Acceptance Criteria:** A configured API key reaches the model endpoint and never appears in any log, stats, or error response.
- **Regression Tests:** Secret-not-logged / secret-not-in-response tests; auth-header-sent test.

#### CON-3 — `mcp` dependency unpinned; transport behavior non-reproducible
- **Severity:** Low · **Category:** Contracts / MCP · **Cross-ref:** MCP-1, MCP-2
- **Evidence:** `orchestrator/requirements.txt:5` `mcp` (no specifier); sandbox resolves 1.26.0 / 1.27.2. Mounting/lifespan semantics changed across the 1.x line.
- **Recommendation:** Pin a tested minor (e.g. `mcp==1.27.2` or `mcp>=1.27,<1.28`); record the version the mounting code was validated against.
- **Acceptance Criteria:** `requirements.txt` pins a concrete MCP version range; the MCP integration test (MCP-1) runs against the pin.
- **Regression Tests:** Covered by the MCP-over-HTTP integration test.

#### LIC-1 / LIC-3 / LIC-5 / LIC-8 — Licensing hygiene (grouped; see Section 9)
- **LIC-1 (High):** SearXNG bundled at `:latest` (`docker-compose.yml:49`); pin to a tag/digest and document as AGPL-3.0. Boundary structurally clean (LIC-2).
- **LIC-3 (High):** TEI/llama.cpp at `:latest`; pin and verify each tag's LICENSE; correct doc section 4 — TEI is currently **Apache-2.0** (re-opened, issue #232), the HFOIL drift was **TGI**, and llama.cpp is **MIT** (doc omits it).
- **LIC-5 (High):** No `LICENSE` for the first-party code; add a root MIT/Apache-2.0 `LICENSE` and name it in the README. (Blocks publication.)
- **LIC-8 (Low):** `RecursiveCharacterTextSplitter` name/design derives from LangChain (MIT); add a one-line provenance note in the module docstring and NOTICE.

Confirmation (non-defect) items recorded for completeness: **LIC-2** (AGPL boundary honored), **LIC-4** (first-party code embeds no model; bundled servers profile-gated), **SOV-2** (hard non-goals hold: no persistent index, stateless, evidence not prose).

---

## 4. Target Architecture (incrementally reachable)

The end state preserves every stage and seam below; changes are additive or surgical.

**Pipeline stages.** Unchanged order. An impure URL-safety resolution step runs before Stage 4, resolving/canonicalizing crawl candidates and producing deterministic safety facts. Stage 4 (selection) stays pure and consumes those facts for SSRF/scheme/canonicalization filtering. Stage 5 (extract) re-validates the final hop after redirects and runs under an overall wall-clock budget. Stage 8 (pre-filter) stays deferred unless implemented as its own impure Protocol stage (never folded into a pure stage).

**Protocol seams + purity contracts.** Seven seams, all with a real impl AND a deterministic fake (adds `FakeSelector`, `FakeAssembler`, `PartialChunker`, `PartialExtractor`). Pure stages (merge, selection, content-dedup, assembly) take no network and speak only internal `types.py` dataclasses — `ResultAssembler` no longer imports wire models (IFACE-7). The reranker contract guarantees one ScoredChunk per input chunk (floor-fill); the token budget, not the reranker, decides output (inv 6).

**BYO model seams.** Embedding/reranker/planner remain env-selected HTTP endpoints with no hardcoded server. The chunker's embedding seam uses the full base URL (preserving scheme/path, port defaulted from scheme — CHUNK-7), and all seams gain optional `Authorization: Bearer` from env (SEC-008). Bundled TEI/llama.cpp stay optional profile services with pinned, license-verified tags.

**REST + MCP twin surfaces.** One shared request-builder resolves defaults in `run_search` only. REST `POST /v1/search` and MCP `web_search` both pass the raw request through and both return the same envelope (full `SearchResponse` dump, or a documented subset that always includes `passages`, `citations`, `reason`, `reranked`, `tokens_returned`). The MCP HTTP transport runs `session_manager.run()` in the FastAPI lifespan, is served at exactly `/mcp` with `stateless_http=True`, and is pinned to a tested SDK version. Both surfaces inherit Pydantic `Field` bounds from the shared model. A schema-snapshot test guards the wire contract; `reason` codes are a closed enum.

**Optional cache.** Either removed entirely (recommended now — OBS-6/SOV-3) or, if added later, a `CacheBackend` Protocol injected into `PipelineDeps`, keyed by `normalize_url`, where `CACHE_BACKEND=none` changes only `elapsed_ms` — never passages/citations/stats (inv 1, 4).

**Observability spine.** `X-Request-ID` contextvar middleware forwarded downstream; one structured per-call summary log with redaction; complete `stats` (`urls_crawled_failed`, dedup count, `chunk_strategy`/`embedding_degraded`); `/healthz` always probes and aggregates per-dependency with hard-fail vs degradable distinction.

---

## 5. Chunker Hardening Plan

Driven by `findings/02-chunker.md`. The DP core, reward objective, similarity matrix, all three valves, memory release, the bounded reward LRU, strategy versioning, and the `/chunk` contract are correct — the plan is about locking them against silent drift (inv 11) and resolving the tokenizer question.

1. **Golden-test coverage of the semantic DP path (CHUNK-1).** Add a mixed-topic golden with a fake embedder returning clearly separable per-topic vectors, sized so the DP boundary differs from greedy max-cap packing; assert the DP picks the topic-coherent boundary. Keep the existing case as the size-cap golden. Add a unit test asserting `_dynamic_programming_chunking` differs from `_greedy_fallback_chunking` for a constructed matrix.
2. **Fallback (valve) tests (CHUNK-2, CHUNK-12).** Force valve 3 (`dp[n] == -inf`, e.g. `initial=400`/`max=10`) asserting non-empty token-bounded chunks; strengthen the valve-1 greedy-semantic test to assert `token_count <= max_chunk_size` (except a lone oversized segment) and a real topic boundary; add a `_greedy_fallback_chunking` oversized-segment test. Add param cross-validation in `resolve_strategy` (reject `initial>max`, `min>max` → 400) so valve 3 is only reachable by genuine pathology.
3. **Memory-bound enforcement (CHUNK-3).** Unit test monkeypatching `MAX_SEGMENTS_FOR_DP` low and patching `_compute_similarity_matrix` to raise — assert it is never called above the cap while a valid greedy result returns; assert the matrix is float32 / `N·N·4` bytes just under the cap; set a `mem_limit` on the chunker container in compose sized to the documented ~400 MB peak.
4. **Tokenizer decision (CHUNK-4).** Resolution: **document now** that `token` means whitespace word and size budgets are approximate (one-line, no drift); **schedule** a real tokenizer (tiktoken proxy) behind a deliberate `cluster-semantic@2` with regenerated goldens. Never swap `_length` silently — it changes every boundary.
5. **`strategy_version` discipline (CHUNK-8).** Add a test pinning the `cluster-semantic@1` registry params (`max=400/min=50/initial=50`) to exact values so any edit forces a conscious `@2`; add a documented `scripts/gen_golden.py`/`--update-goldens` regeneration ritual; freeze the CHUNK-1 mixed-topic golden into the `@1` set.
6. **Contract honesty (CHUNK-5, CHUNK-6).** Document `min_chunk_size` as a soft floor (final/degenerate cases exempt) and `start_index`/`end_index` as approximate (not byte-exact, may not round-trip after whitespace normalization). Any change that makes them hard/verbatim is text-affecting → strategy bump.
7. **Per-run LRU note (CHUNK-10) + cosmetic (CHUNK-11).** Comment that the reward cache is per-DP-run and must not be hoisted; replace the magic `n` diagonal with `np.trace`.

---

## 6. Resilience & Degradation Matrix (canonical)

Per dependency: how the failure is detected, whether it degrades or hard-fails, the `reason`/signal emitted, the observable flag, and the test that proves it. Findings that fix gaps are noted.

| Dependency | Detect | Degrade or hard-fail | reason / signal | Observable flag | Proving test |
|---|---|---|---|---|---|
| **Planner (LLM)** | non-200 / parse error in `LlmPlanner.plan` | **Degrade** silently → identity `[query]`; **cap output to N** (RES-3) | none (sub-query count in stats) | `stats.sub_queries` | FakePlanner returns >K → ≤K discovery calls (RES-3) |
| **SearXNG (discovery)** | `DiscoveryUnavailable` (network or non-200) | **Hard-fail 503** | `searxng_unavailable` (503 `{dependency, reason}`) | HTTP 503 body | discovery-down test → 503 with reason |
| **Crawl4AI (extract)** | per-URL exception/None; overall budget expiry (RES-2) | **Degrade / partial**; proceed on what fetched; zero → 200 `all_crawls_failed` | `all_crawls_failed` when zero | `stats.urls_crawled_ok`, `urls_crawled_failed` (OBS-4) | one URL hangs → fast pages returned within budget, `crawled_ok < selected`, 200 (RES-2) |
| **Chunker (service)** | unreachable/5xx → `ChunkerUnavailable`; per-page 4xx → skip page (IFACE-5) | **Hard-fail 503** only when unreachable/5xx; per-page 4xx degrades; zero chunks → 200 `no_chunks_after_dedup` | `chunker_unavailable` (503) / `no_chunks_after_dedup` (200) | HTTP 503 / `stats.reason` | page 2 of 3 returns 422 → pages 1&3 chunk, 200; 5xx → 503 (IFACE-5) |
| **Embedding (in chunker)** | `EmbeddingFunction` raises / count mismatch | **Degrade** → greedy token fallback inside chunker, 200 chunks | must surface `chunk_strategy=fallback` / `embedding_degraded` (OBS-4/RES-5) | `stats.chunk_strategy` + `/healthz` chunker-embedding sub-status | embedding refused → chunker fallback marker + orchestrator stats reflect it (RES-5) |
| **Reranker (BYO)** | `RerankerUnavailable` (network/non-200); index-less or short/empty 200 (IFACE-1/IFACE-4/RES-4) | **Degrade** → discovery order; floor-fill missing chunks; never crash | `reranked: false`; post-rerank empty guard sets a `reason` | `stats.reranked`, `stats.reason` | DownReranker → `reranked=false`; index-less shape → positional, not 500; short results → all chunks floor-filled |

Every degraded path returns 200 with a populated `reason`/`reranked` (inv 9). Both surfaces must expose those signals (MCP-3 fix). `/healthz` must reflect all six per-dependency states live (RES-1/API-2).

---

## 7. Observability Plan

1. **`stats` completeness (OBS-4 / RES-5).** Add `urls_crawled_failed`, `chunks_after_dedup`/`pages_deduped`, and `chunk_strategy`/`embedding_degraded` to `SearchStats`; keep all existing fields (additive — preserves inv 10). Count the content-dedup effect explicitly.
2. **MCP parity on `stats`/`reason`/`reranked` (OBS-1 / MCP-3).** Source both surfaces from the same `SearchResponse` so they cannot drift; MCP returns at minimum `reason`, `reranked`, `tokens_returned` (prefer the full dump). The REST↔MCP parity test (Section 11) is the guardrail.
3. **Correlation / trace IDs (OBS-2).** ASGI middleware reads/generates `X-Request-ID`, stores it in a contextvar, includes it in every structured log line, forwards it as a header on every downstream httpx call (orchestrator → chunker → embedding/reranker), and returns it in the response and on the MCP path.
4. **Structured logging + secret redaction (OBS-3 / SEC-006).** JSON logs; one per-call summary line (query hash, sub-query count, urls discovered/selected/crawled_ok/failed, reranked, reason, elapsed_ms — never raw query or markdown). A redaction helper strips userinfo/query strings from endpoint and target URLs before logging; never log full upstream error bodies above DEBUG; never log page bodies. Document and test the policy.
5. **Real `/healthz` (RES-1 / API-2).** Always probe; aggregate per-dependency; `status=degraded` when any probe is false; distinguish hard-fail (searxng/chunker) from degradable (crawl4ai/reranker/embedding, including the chunker's nested embedding health). Fix the reranker health-path mismatch (RES-8).

---

## 8. Security & Crawl-Safety Posture

**SSRF defense (SEC-001 + CRAWL-001) — the headline.** Add an impure URL-safety resolution step before stage-4 selection, then feed deterministic safety facts into the pure selector:
- Resolve each candidate host to its IP(s); reject if any resolved address is loopback / link-local / private / reserved / multicast / unspecified; reject IP-literal hosts in those ranges; normalize dec/hex/oct encodings; block metadata IPs (`169.254.169.254`, `fd00:ec2::254`).
- **Re-validate the final hop:** disable redirect-following in the crawl path and re-run the guard on each `Location`, or use a connection-time hook for every hop; cap redirect depth; pin the resolved IP through to connect (closes DNS-rebinding).
- **Purity (inv 7):** DNS/IP resolution is not inside `SelectionPolicy`. Unit tests exercise the resolver with mocked DNS output and the selector with synthetic safety facts, so selection stays network-free.
- Defense-in-depth: egress-firewall the `crawl4ai` container off link-local + RFC1918.

**Scheme restriction (SEC-002).** Reject any URL whose scheme is not exactly `http`/`https` at the gate; treat `host_for` returning the raw string (no hostname) as an automatic reject.

**Blocklist → proper egress control (SEC-003 / SEC-004).** Canonicalize hosts (strip trailing dot, IDNA-encode, reject IP-literal encodings, suffix/registrable-domain match). The host blocklist is for ToS exclusion, **not** SSRF — the IP-range guard is the real SSRF control. Add `DOMAIN_ALLOWLIST` + optional `ALLOWLIST_ONLY` for locked-down deployments.

**Untrusted-content / prompt-injection boundary (SEC-007).** Label each returned passage `provenance=external_web`/`trust=untrusted` on both surfaces; document that downstream agents must treat passage text as data, never instructions. Keep the planner hop free of crawled text (it already is — inv 5 upheld).

**Polite-crawl / ToS (CRAWL-002 / SOV-4 / CRAWL-003).** Set Crawl4AI `check_robots_txt` by default with an operator toggle; add a per-host throttle alongside the global semaphore; clamp `CRAWL_CONCURRENCY` to a sane ceiling; wrap the fan-out in an overall budget (RES-2).

**Secret handling (SEC-008 / OBS-3).** Add optional per-seam `*_API_KEY` env (`Authorization: Bearer`); let the embedding endpoint honor https + path (CHUNK-7); confirm keys are env-only, never logged, never in responses/stats/errors (holds today; add tests).

**Input bounds (API-1 / SEC-005).** Pydantic `Field` ceilings on `query` length, `token_budget`, `max_urls` (small hard cap ≈ 20), `max_passages`, and chunker `text` — on the shared model so both surfaces inherit them; REST 422 / MCP tool error before any fan-out.

---

## 9. Licensing & Sovereignty Compliance

Driven by `findings/06-licensing-sovereignty.md`. *Engineering guidance, not legal advice; the operator must re-verify each license against the exact pinned tag and consult counsel.*

**AGPL boundary — sound (LIC-2).** SearXNG is a referenced upstream image, run unmodified, configured only via a read-only `settings.yml`, called over HTTP behind the `SearchDiscovery` Protocol. `searxng/` tracks only `settings.yml`. Never add a `build:`/Dockerfile/vendored source. **Preserve this — it is what keeps the first-party code out of AGPL network-copyleft scope.**

**Tag pinning (LIC-1 / LIC-3 / LIC-6).** Replace every `:latest`: SearXNG (`docker-compose.yml:49`) → tag/digest, documented AGPL-3.0; TEI and llama.cpp → pinned tags with verified LICENSE; Crawl4AI (`unclecode/crawl4ai:latest`) → pinned tag. Unpinned tags defeat the doc's own "pin and verify" advice and make the bundle non-reproducible.

**Model-server license drift (LIC-3).** Correct doc section 4: **TEI is currently Apache-2.0** (re-opened, GitHub issue #232) — the doc's claim that TEI became restrictive is backwards; the HFOIL drift was **TGI** (text-generation-inference). Add **llama.cpp = MIT** to the table (the doc omits it though the compose wires it). First-party code embeds no model and bundled servers are profile-gated (LIC-4) — keep it that way.

**Chroma attribution (LIC-7).** Code header is correct (`cluster_semantic.py:4-6`); the README is missing the credit. Add a README "Credits / Attribution" section: Chroma Research, "Evaluating Chunking Strategies for Retrieval" (July 2024), implementation first-party, algorithm not novel. (Splitter provenance — LIC-8 — clarify `RecursiveCharacterTextSplitter` as LangChain-inspired/MIT or independent.)

**LICENSE / NOTICE (LIC-5 / LIC-6).** Add a root `LICENSE` (MIT or Apache-2.0) covering `orchestrator/` + `semantic-chunking-service/` — without it, "no license" means all-rights-reserved and **publication is blocked**. Add a `NOTICE`/`THIRD-PARTY-NOTICES.md` enumerating bundled images + pip deps with licenses and preserving any required upstream `NOTICE`/attribution text for the exact pinned artifacts.

**Sovereignty (SOV-1 / SOV-2 / SOV-3).** Hard non-goals hold (no persistent index, stateless, evidence not prose — preserve). Add a README "Sovereignty & outbound network" section disclosing the two optional egress paths (commercial search API key in `settings.yml`; remote BYO model endpoints) as explicit operator choices. Neutralize the dead `CACHE_BACKEND`/`REDIS_URL` (OBS-6/SOV-3) so a sovereignty audit sees no phantom external dependency.

### Publishability Checklist (pre-release)
- [ ] **SearXNG unmodified + documented** — referenced upstream image, no `build:`/Dockerfile/vendored source; `searxng/` config-only; tag pinned + documented AGPL-3.0 (LIC-1; boundary clean LIC-2).
- [ ] **SearXNG tag pinned** to a specific tag/digest (LIC-1).
- [ ] **Bundled model-server tags pinned + LICENSE verified** — TEI (Apache-2.0), llama.cpp (MIT), any other wired server (LIC-3).
- [ ] **Doc section 4 TEI claim corrected** — TEI Apache-2.0 (issue #232); HFOIL drift was TGI; add llama.cpp = MIT (LIC-3).
- [ ] **First-party LICENSE** added at repo root and named in README (LIC-5).
- [ ] **NOTICE / third-party-notices** covering Crawl4AI (Apache-2.0), Python deps, bundled servers, AGPL SearXNG reference, and any required upstream `NOTICE`/attribution text for exact pinned artifacts (LIC-6).
- [ ] **Chroma attribution in README** — paper + July 2024; not novel (LIC-7).
- [ ] **Splitter provenance note** for `RecursiveCharacterTextSplitter` (LIC-8).
- [ ] **Optional-outbound disclosures in README** — search API key + remote model endpoints as operator choices; core stack reaches only searched sites (SOV-1).
- [ ] **Crawl4AI image tag pinned** (LIC-6).
- [ ] **Cache claim matches code** — implement injected cache or mark unimplemented and neutralize dead `CACHE_BACKEND`/`REDIS_URL` (OBS-6/SOV-3).
- [ ] **Polite-crawl posture enforced or scoped** — robots.txt honoring wired or delegated default documented (CRAWL-002/SOV-4).
- [ ] **Non-goals preserved** — no persistent index/vector store in the hot path; stateless per call (SOV-2).
- [ ] **Re-verify every license against the exact pinned tag** before distributing; consult counsel.

---

## 10. Phased Refactor Plan

Dependency-ordered. **Where the Criticals land:** the MCP transport Criticals (MCP-1, MCP-2) are scheduled in **Phase 1** and the SSRF Critical (SEC-001/CRAWL-001) in **Phase 4** — but both should be **pulled forward as immediate hotfixes** on top of Phase 0's test baseline, because (a) the MCP surface is simply dead and the fix is small and self-contained, and (b) SSRF is remotely exploitable and the IP-range guard plus redirect re-validation are independently shippable. Treat Phase 0 + the two hotfixes as the gate before any feature work; the rest of Phase 1/4 then proceed in order. Every backlog item below traces to a finding.

### Phase 0 — Safety Baseline & Tests
- **Scope:** Establish the test scaffolding the rest depends on; do not change behavior. Add `FakeSelector`/`FakeAssembler`/`PartialChunker`/`PartialExtractor` (IFACE-2, IFACE-6); add the MCP-over-HTTP integration harness (red, proving MCP-1) and the REST↔MCP parity harness (red, proving MCP-3/MCP-4); pin the `mcp` SDK (CON-3); pin `cluster-semantic@1` params and add the mixed-topic golden + valve-3/OOM tests as red specs (CHUNK-1/2/3/8/12).
- **Non-goals:** No production-code behavior change; no contract change.
- **Tasks/files:** `orchestrator/fakes.py`, `orchestrator/tests/`, `semantic-chunking-service/tests/`, `orchestrator/requirements.txt`.
- **Closes (sets up):** IFACE-2, IFACE-6, CON-3; red harnesses for MCP-1, MCP-3/4, CHUNK-1/2/3/8/12.
- **Risks:** Low. New red tests must be quarantined (xfail) until their phase lands.
- **Acceptance:** Fakes for all seven seams exist; the parity and MCP-HTTP harnesses run and currently fail for the documented reasons; chunker pin + new goldens committed.
- **Tests:** the harnesses themselves. **Rollback:** revert test files (no prod change).

### Phase 1 — Interface & Contract Hardening (includes MCP transport Critical)
- **Scope:** Fix MCP-1 (lifespan `session_manager.run()`), MCP-2 (`streamable_http_path="/"`, `stateless_http=True`); unify defaulting in `run_search` (PIPE-4); one shared request-builder so REST/MCP are twins; MCP returns the full envelope incl. `reason`/`reranked`/`stats` (MCP-3/PIPE-1/OBS-1) and the missing fields + ported docstring (MCP-4/MCP-5); reranker adapter positional/floor-fill/degrade (IFACE-1, IFACE-4/RES-4); chunker per-page 4xx skip vs 5xx hard-fail (IFACE-5); distinct `no_urls_after_selection` reason (PIPE-3); raw-markdown join key (PIPE-5); assembler internal types (IFACE-7); API versioning + schema snapshot (CON-1).
- **Non-goals:** No security/crawl changes; no chunker algorithm change.
- **Tasks/files:** `orchestrator/app.py`, `orchestrator/mcp_server.py`, `orchestrator/pipeline.py`, `orchestrator/clients/reranker_client.py`, `orchestrator/clients/chunker_client.py`, `orchestrator/models.py`, `orchestrator/interfaces.py`, `orchestrator/assembly.py`, `orchestrator/types.py`.
- **Closes:** MCP-1, MCP-2, PIPE-1/MCP-3/OBS-1, MCP-4, MCP-5, CON-1, CON-2, PIPE-3, PIPE-4, PIPE-5, IFACE-1, IFACE-4/RES-4, IFACE-5, IFACE-7.
- **Risks:** Touches the public contract — must be additive (preserve REST shape; inv 10). Staged migration via `/v1/search` + schema snapshot.
- **Acceptance:** MCP HTTP `initialize`+`tools/call` succeed at `/mcp`; the parity harness is green; index-less reranker degrades not crashes; per-page 422 skips not 503s; schema-snapshot test gates non-additive changes.
- **Tests:** Phase 0 harnesses now green; reranker/chunker MockTransport tests; schema snapshot. **Rollback:** revert per-file; the contract is additive so REST callers are unaffected.

### Phase 2 — Resilience & Degradation
- **Scope:** Overall crawl budget + cancellation + explicit `httpx.Timeout` (RES-2); cap planner output + bound discovery fan-out (RES-3); post-rerank empty guard (RES-4); chunker surfaces fallback strategy, orchestrator propagates it (RES-5/OBS-4); shared reused `AsyncClient` per stage (RES-6); document/decide retries (RES-7); align reranker health path (RES-8); decompose default note (PIPE-8); merge corroboration / pre-filter doc (PIPE-7/PIPE-6).
- **Non-goals:** No new public fields beyond additive stats; no security work.
- **Tasks/files:** `orchestrator/clients/*.py`, `orchestrator/pipeline.py`, `orchestrator/models.py`, `semantic-chunking-service/chunking/*` (fallback marker), `docker-compose.yml`.
- **Closes:** RES-2, RES-3, RES-4 (with IFACE-4), RES-5/OBS-4, RES-6, RES-7, RES-8, PIPE-6, PIPE-7, PIPE-8.
- **Risks:** Cancellation semantics — ensure partial pages still return 200.
- **Acceptance:** Slow-URL test returns fast pages within budget; 50-subquery planner → ≤N discovery calls; embedding-down surfaces in stats; clients reused O(1)/stage.
- **Tests:** failure-injection suite. **Rollback:** per-client revert.

### Phase 3 — Chunker Robustness & Performance
- **Scope:** Turn the Phase 0 red chunker specs green (CHUNK-1/2/3/12); param cross-validation 400 (CHUNK-2); container `mem_limit` (CHUNK-3); tokenizer decision — document word-based now, schedule `@2` (CHUNK-4); soft-floor/approximate-offset docs (CHUNK-5/CHUNK-6); embedding endpoint full-URL (CHUNK-7); golden-regeneration ritual + params pin (CHUNK-8); per-run LRU comment + `np.trace` (CHUNK-10/CHUNK-11).
- **Non-goals:** No boundary-altering change without a `strategy_version` bump (inv 11). The `@2` tokenizer swap is scheduled, not shipped here unless goldens are regenerated deliberately.
- **Tasks/files:** `semantic-chunking-service/chunking/*.py`, `semantic-chunking-service/tests/*`, `docker-compose.yml`, `embedding_function.py`.
- **Closes:** CHUNK-1, CHUNK-2, CHUNK-3, CHUNK-4 (doc), CHUNK-5, CHUNK-6, CHUNK-7, CHUNK-8, CHUNK-10, CHUNK-11, CHUNK-12.
- **Risks:** Accidental boundary drift — the params-pin test is the backstop.
- **Acceptance:** All chunker specs green; editing `@1` params fails a test; matrix never allocated above cap; mem_limit set; https/path embedding endpoints work.
- **Tests:** golden parity + fallback + memory-bound. **Rollback:** per-file; goldens unchanged unless an explicit `@2`.

### Phase 4 — Security & Crawl Safety (includes SSRF Critical)
- **Scope:** URL-safety resolver before selection + pure selector safety-fact filtering + scheme allowlist + host canonicalization (SEC-001/SEC-002/SEC-003); redirect re-validation + depth cap + IP-pinning + crawl4ai egress firewall (CRAWL-001); input bounds on the shared model (API-1/SEC-005); operator allowlist + allowlist-only (SEC-004); robots.txt + per-host throttle + concurrency ceiling (CRAWL-002/SOV-4/CRAWL-003); untrusted-content provenance label (SEC-007); per-seam auth headers (SEC-008).
- **Non-goals:** No contract reshape (bounds are additive validation).
- **Tasks/files:** `orchestrator/selection.py`, `orchestrator/normalize.py`, `orchestrator/clients/crawl4ai_client.py`, `orchestrator/models.py`, `orchestrator/settings.py`, `orchestrator/clients/*` (auth), `docker-compose.yml`.
- **Closes:** SEC-001/CRAWL-001, SEC-002, SEC-003, API-1/SEC-005, SEC-004, CRAWL-002/SOV-4, CRAWL-003, SEC-007, SEC-008.
- **Risks:** Do not hide DNS/network I/O inside `SelectionPolicy` (inv 7); over-broad blocking could reject legitimate public hosts — test the public-URL-still-selected case.
- **Acceptance:** Metadata/loopback/RFC1918/internal-hostname/encoded-IP and forbidden schemes select zero URLs; redirect-to-internal blocked; over-cap inputs 422 before fan-out; robots `Disallow` skips.
- **Tests:** full SSRF/scheme/canonicalization/redirect/bounds/robots suite. **Rollback:** per-file; guard is additive.

### Phase 5 — Observability & Operability
- **Scope:** `X-Request-ID` middleware + downstream propagation (OBS-2); structured JSON logging + redaction + no-body/query (OBS-3/SEC-006); complete `stats` (OBS-4 — already partly in Phase 2); real `/healthz` always-probe + aggregate (RES-1/API-2); `reranked` tri-state/doc (OBS-5); neutralize dead cache settings (OBS-6/SOV-3).
- **Non-goals:** No cache implementation (settings removed, not built).
- **Tasks/files:** `orchestrator/app.py` (middleware, healthz), `orchestrator/clients/*` (header forward), logging config, `orchestrator/settings.py`, `.env.example`.
- **Closes:** OBS-2, OBS-3/SEC-006, OBS-4 (finalize), RES-1/API-2, OBS-5, OBS-6/SOV-3.
- **Risks:** `/healthz` probe cost — cache results briefly.
- **Acceptance:** Down-dependency `/healthz` reports false when warm; per-call summary log emitted with no secrets; `X-Request-ID` propagated to mocked downstreams.
- **Tests:** healthz failure-injection; log-redaction; header-propagation. **Rollback:** per-file.

### Phase 6 — Licensing & Sovereignty Hardening
- **Scope:** Pin all image tags (LIC-1/LIC-3/LIC-6); root `LICENSE` (LIC-5); `NOTICE`/third-party-notices (LIC-6); README Chroma credit (LIC-7), splitter provenance (LIC-8), sovereignty/outbound disclosure (SOV-1); correct doc section 4 TEI/TGI/llama.cpp (LIC-3); robots scoping doc if not enforced in Phase 4 (SOV-4); complete the Publishability Checklist.
- **Non-goals:** No code-behavior change (doc/manifest/metadata only).
- **Tasks/files:** `docker-compose.yml`, `docker-compose.llamacpp.yml`, `LICENSE`, `NOTICE`, `README.md`, `docs/foundational design/05-licensing-and-sovereignty.md`, `.env.example`.
- **Closes:** LIC-1, LIC-3, LIC-5, LIC-6, LIC-7, LIC-8, SOV-1, SOV-3 (doc side), SOV-4 (doc side).
- **Risks:** None to runtime; re-verify each license against the pinned tag.
- **Acceptance:** Publishability Checklist fully ticked; no `:latest` anywhere; LICENSE + NOTICE + README credits present.
- **Tests:** a release-checklist CI assertion (no `build:` for searxng; no `:latest`). **Rollback:** revert metadata.

---

## 11. Recommended Tests

- **Network-free pure-stage tests:** `merge_dedup` corroboration ordering (PIPE-7); selection zero-score rank order (PIPE-2), pure selector safety-fact filtering, scheme/canonicalization rejection (SEC-001/002/003); assembly budget-not-count and internal-type projection (IFACE-7, inv 6).
- **Fake-backed pipeline tests:** every failure mode in arch-map §8 reachable via Down/Empty/Partial fakes (IFACE-6); `FakeAssembler[]` empty path and `FakeSelector` single-URL path (IFACE-2); distinct reason codes (PIPE-3); raw-markdown attaches for URL variants (PIPE-5).
- **Chunker golden + fallback tests:** size-cap golden (existing) + mixed-topic semantic-DP golden (CHUNK-1); valve-3 forced no-solution (CHUNK-2); greedy-semantic size/boundary + oversized-segment (CHUNK-12); OOM guard never allocates the matrix above cap + matrix dtype/bytes (CHUNK-3); `@1` params pin (CHUNK-8); embedding endpoint URL forms (CHUNK-7).
- **REST↔MCP parity test (the guardrail for inv 8):** one parametrized test over identical inputs (default; custom budget/urls; `include_raw_markdown`; each degraded `reason`; `reranked=false`; settings-override) asserting REST and MCP expose identical `passages`, `citations`, `stats.reason`, `stats.reranked`. Replaces the divergence-encoding `test_mcp.py:13`.
- **MCP transport integration test (would have caught the dead `/mcp` mount):** real `initialize` + `tools/call` over HTTP via the SDK client against the mounted app, asserting a payload returns at exactly `/mcp` (MCP-1/MCP-2), pinned to the chosen SDK version (CON-3).
- **Dependency-down / failure-injection:** SearXNG/chunker hard-fail 503; crawl slow-URL within budget (RES-2); planner fan-out cap (RES-3); reranker short/empty/index-less (RES-4/IFACE-1/IFACE-4); embedding-down fallback surfaced in stats (RES-5); `/healthz` false-when-warm-and-down (RES-1).
- **SSRF / security:** URL-safety resolver blocks metadata/loopback/RFC1918/IPv6/internal-hostname/encoded-IP + pure selector still selects safe public URLs; redirect-to-internal blocked + depth cap (CRAWL-001); forbidden schemes (SEC-002); host canonicalization bypasses (SEC-003); allowlist-only (SEC-004); robots Disallow (CRAWL-002); per-host throttle (CRAWL-003); secret-not-logged / not-in-response + auth-header-sent (SEC-006/SEC-008); provenance label present on both surfaces (SEC-007).
- **Contract / schema:** golden JSON-schema snapshot of `SearchResponse` + MCP tool I/O that fails on any non-additive change (CON-1); passage-key equivalence across surfaces (CON-2); input-bound 422 per field (API-1/SEC-005).
- **Performance:** chunker memory-bound assertions (CHUNK-3); client-reuse O(1)/stage (RES-6); chunker concurrency independence (CHUNK-9).

---

## 12. Contract & Migration Strategy

**Safe to change (additive, no migration):** adding `Field` bounds (rejects only already-invalid inputs — API-1/SEC-005); adding `stats` fields (`urls_crawled_failed`, dedup count, `chunk_strategy` — OBS-4/RES-5); adding the `no_urls_after_selection` reason (new enum member — PIPE-3); adding a per-passage provenance field (SEC-007); making MCP return `reason`/`reranked`/`stats` (MCP-3 — purely additive to the MCP envelope, with `passages`/`citations` kept first). Internal-only changes (assembler types IFACE-7, reranker floor-fill IFACE-1/IFACE-4, chunker per-page handling IFACE-5) do not touch the wire shape.

**REST + MCP wire-contract stability + versioning (CON-1, inv 10):** introduce `/v1/search` (keep `/search` aliased to `/v1/search` during a deprecation window) or add `schema_version` to `SearchResponse`; treat `reason` codes as a closed documented enum; default change policy is additive-only; reserve breaking changes for a new version. For MCP, version the tool name or expose a version in server info. A schema-snapshot test fails on any non-additive change, forcing a deliberate bump. Pin the MCP SDK (CON-3) so the transport contract is reproducible.

**`stats`/`reason`/`reranked` compatibility:** all existing fields are preserved and only added to (inv 10). MCP gains them additively — existing MCP callers that read `passages`/`citations` keep working. The REST↔MCP parity test enforces that both surfaces emit the same `reason`/`reranked` going forward.

**Needs a staged migration:** the MCP HTTP path move from `/mcp/mcp` to `/mcp` (MCP-2) is a behavioral correction toward the documented URL — since the current path was never documented and the surface is dead anyway (MCP-1), there is no live MCP HTTP caller to break; ship it with the MCP-1 fix and document `/mcp` as the canonical path. Any future tokenizer swap (CHUNK-4 → `cluster-semantic@2`) is a deliberate strategy-version migration with regenerated goldens; old callers pin `@1`.

**Defer:** the optional pre-filter stage (PIPE-6) and any cache (OBS-6/SOV-3) — remove the dead cache settings now; if a cache lands later it is an injected seam that changes only `elapsed_ms`. The retry policy (RES-7) is a documented deliberate omission unless demand appears.

---

## 13. Final Prioritized Backlog

Sorted by severity then dependency-order (phase). Every item traces to a consolidated finding.

| Priority | Item | Category | Severity | Title | Phase | Invariant(s) |
|---|---|---|---|---|---|---|
| 1 | MCP-1 | MCP/Transport | Critical | HTTP MCP mount has no session-manager lifespan; `/mcp` fails at request time | 1 (hotfix) | 8 |
| 2 | SEC-001+CRAWL-001 | Security | Critical | No SSRF guard before crawl; redirects not re-validated | 4 (hotfix) | 7 |
| 3 | MCP-2 | MCP/Transport | Critical | Double-mount: tool served at `/mcp/mcp`, not `/mcp` | 1 (hotfix) | 8, 10 |
| 4 | API-1+SEC-005 | API/Security | High | No input bounds on `token_budget`/`max_urls`/`max_passages`/`query`/text (DoS) | 4 | 6, 7 |
| 5 | SEC-002 | Security | High | Crawl scheme unrestricted (no http/https allowlist) | 4 | 7 |
| 6 | SEC-003 | Security | High | Blocklist/allowlist host-exact-match; bypassable, SSRF-ineffective | 4 | 7 |
| 7 | MCP-3+PIPE-1+OBS-1+MCP-4+CON-2 | MCP/Contracts | High | MCP drops stats/reason/reranked, hardcodes defaults, omits fields | 1 | 8, 10 |
| 8 | RES-1+API-2 | Resilience/API | High | `/healthz` all-True once warm; no live probe | 5 | 9 |
| 9 | CRAWL-002+SOV-4 | Crawl-Safety | High | robots.txt never honored; polite-crawl unenforced | 4 | 7 |
| 10 | CON-1 | Contracts | High | No API/tool version or staged-migration mechanism | 1 | 10 |
| 11 | IFACE-1 | Interfaces | High | Reranker parse error escapes as uncaught 500 | 1 | 9 |
| 12 | IFACE-2 | Interfaces | High | Pure seams have no deterministic fake | 0 | 7 |
| 13 | CHUNK-1 | Testing | High | Golden test pins size-packing, not the semantic DP path | 0/3 | 11 |
| 14 | CHUNK-2 | Testing | High | Valve-3 (DP-no-solution) untested; reachable via overrides | 0/3 | 9, 11 |
| 15 | CHUNK-3 | Performance | High | No memory-bound test or container mem_limit for 400 MB ceiling | 0/3 | 11 |
| 16 | LIC-1 | Licensing | High | SearXNG bundled at `:latest`; defeats AGPL pin posture | 6 | 2 |
| 17 | LIC-3 | Licensing | High | TEI/llama.cpp at `:latest`; doc TEI license claim wrong | 6 | — |
| 18 | LIC-5 | Licensing | High | No LICENSE for first-party code (blocks publication) | 6 | — |
| 19 | PIPE-3 | Pipeline | Medium | One reason reused for two distinct empty states | 1 | 10 |
| 20 | PIPE-4 | Pipeline | Medium | Defaulting duplicated in REST and pipeline (drift root cause) | 1 | 8 |
| 21 | PIPE-5 | Pipeline | Medium | `include_raw_markdown` silently drops URL-mismatch citations | 1 | — |
| 22 | IFACE-3 | Interfaces | Medium | Selection policy split between pipeline and selector; double lowercasing | 1 | 7 |
| 23 | IFACE-4+RES-4 | Interfaces/Resilience | Medium | Reranker may drop chunks; degraded returns more than success | 1/2 | 6, 9 |
| 24 | IFACE-5 | Interfaces | Medium | One bad page hard-fails the whole chunk batch | 1 | 9 |
| 25 | CHUNK-4 | Chunker | Medium | Token proxy is word count; `token_budget` is word-budget end-to-end | 3 | 6, 11 |
| 26 | CHUNK-5 | Chunker | Medium | `min_chunk_size` is a soft floor; docstring overstates it | 3 | 11 |
| 27 | CHUNK-6 | Chunker | Medium | `start_index`/`end_index` approximate; wrong on repeated text | 3 | 11 |
| 28 | CHUNK-7 | Chunker | Medium | Embedding seam drops scheme/path, requires explicit port | 3 | BYO seams |
| 29 | CHUNK-8 | Testing | Medium | No params pin / golden-regeneration ritual tying change to `@2` | 0/3 | 11 |
| 30 | OBS-2 | Observability | Medium | No correlation/trace IDs across services | 5 | — |
| 31 | OBS-3+SEC-006 | Observability/Security | Medium | No structured logging; no redaction; full URLs logged | 5 | — |
| 32 | OBS-4+RES-5 | Observability/Resilience | Medium | `stats` incomplete; embedding degradation invisible | 2/5 | 9, 10 |
| 33 | RES-2 | Resilience | Medium | Crawl fan-out has no overall budget/cancellation | 2 | 9 |
| 34 | RES-3 | Resilience | Medium | Discovery fan-out unbounded by sub-query count (DoS) | 2 | 7 |
| 35 | SEC-004 | Security | Medium | No allowlist-only locked-down mode | 4 | 7 |
| 36 | CRAWL-003 | Crawl-Safety | Medium | No per-host rate limit; concurrency cap unbounded | 4 | 7 |
| 37 | MCP-5 | MCP/Contracts | Medium | Tool docstring omits budget semantics/return shape | 1 | 8 |
| 38 | LIC-6 | Licensing | Medium | No NOTICE/attribution for permissive deps | 6 | — |
| 39 | LIC-7 | Licensing | Medium | Chroma credited in code, missing from README | 6 | 12 |
| 40 | SOV-1 | Sovereignty | Medium | Optional-outbound/sovereignty disclosures absent from README | 6 | 4 |
| 41 | PIPE-2 | Pipeline | Low | Selection caps count not quality on zero-score results | 2 | 7 |
| 42 | PIPE-6 | Pipeline | Low | Documented pre-filter stage absent | 2 (doc) | — |
| 43 | PIPE-7 | Pipeline | Low | merge_dedup discards corroboration signal | 2 | — |
| 44 | PIPE-8 | Pipeline | Low | `decompose` default awaits a no-op planner | 2 (doc) | 6 |
| 45 | IFACE-6 | Interfaces | Low | Fakes omit failure/degradation coverage for several seams | 0 | 9 |
| 46 | IFACE-7 | Interfaces | Low | ResultAssembler Protocol leaks wire models | 1 | 10 |
| 47 | CHUNK-9 | Performance | Low | Sync endpoint + shared embedder thread cap (footgun note) | 3 (doc) | 3 |
| 48 | CHUNK-10 | Performance | Low | Reward LRU per-run; document do-not-hoist | 3 | 3 |
| 49 | CHUNK-11 | Chunker | Low | `avg_similarity` log hard-codes diagonal as `n` | 3 | — |
| 50 | CHUNK-12 | Testing | Low | Greedy-semantic/oversized-segment untested for boundaries | 0/3 | 11 |
| 51 | RES-6 | Operations | Low | httpx clients per-call; no reuse or pool ceiling | 2 | — |
| 52 | RES-7 | Resilience | Low | No retries with caps/jitter (document or implement) | 2 (doc) | 9 |
| 53 | RES-8 | Operations | Low | `RERANKER_HEALTH_PATH` default mismatches compose | 2 | — |
| 54 | OBS-5 | Observability | Low | `reranked=false` conflates degraded vs never-reached | 5 (doc) | 10 |
| 55 | OBS-6+SOV-3 | Operations/Sovereignty | Low | Dead `CACHE_BACKEND`/`REDIS_URL` advertise phantom cache | 5 | 1, 3, 4 |
| 56 | SEC-007 | Security | Low | Crawled evidence has no untrusted-content boundary marker | 4 | 5 |
| 57 | SEC-008 | Security | Low | BYO endpoints have no auth-header support | 4 | BYO seams |
| 58 | CON-3 | Contracts/MCP | Low | `mcp` dependency unpinned; transport non-reproducible | 0 | 10 |
| 59 | LIC-8 | Licensing | Low | `RecursiveCharacterTextSplitter` LangChain provenance unattributed | 6 | — |

Confirmation (non-defect) items to preserve, not fix: LIC-2 (AGPL boundary clean), LIC-4 (no embedded model), SOV-2 (hard non-goals hold).
