# 04 - API & MCP Surface Review

Read-only review of the two thin Thorondor surfaces over the shared `run_search` pipeline:
REST `POST /search` (`orchestrator/app.py`) and the MCP `web_search` tool
(`orchestrator/mcp_server.py`), plus the wire models (`orchestrator/models.py`).

**SDK version note.** `orchestrator/requirements.txt:5` pins `mcp` with **no version**. The
sandbox resolves to **mcp 1.26.0 / 1.27.2** (current 1.x line). MCP-SDK behavior cited below comes
from the MCP Python SDK README / maintainer issues for the **1.x** series; source URLs inline. The
design doc flags its MCP snippets as representative - verify against the pinned SDK version
(`docs/foundational design/04-api-and-agent-integration.md:3,140`).

## Severity tally
- **Critical: 2** - MCP-1 (HTTP MCP mount has no session-manager lifespan -> fails at request time),
  MCP-2 (double-mount path: endpoint served at `/mcp/mcp`, not `/mcp`).
- **High: 4** - API-1 (no input bounds on `token_budget`/`max_urls`/`max_passages` -> cost/memory DoS),
  MCP-3 (MCP drops `stats`/`reason`/`reranked` -> degraded-200 contract invisible),
  MCP-4 (MCP hardcodes defaults divergent from REST/settings -> twins can drift),
  CON-1 (no API/tool version; no staged-migration path for the wire contract).
- **Medium: 3** - API-2 (`/healthz` reports all-true once deps are built; no live probe),
  MCP-5 (tool docstring far thinner than the documented contract),
  CON-2 (MCP return shape diverges from REST passage shape and from the doc example).
- **Low: 1** - CON-3 (unpinned `mcp` dependency makes transport behavior non-reproducible).

---

### MCP-1 - Streamable-HTTP MCP mount has no session-manager lifespan; `/mcp` fails at request time
- **Severity:** Critical
- **Category:** MCP
- **Evidence:** `orchestrator/app.py:11` builds `FastAPI(title=thorondor)` with no `lifespan=`;
  `orchestrator/app.py:96` `app.mount(/mcp, mcp.streamable_http_app())`. No call to
  `mcp.session_manager.run()` anywhere in the app.
- **Problem:** In the MCP Python SDK 1.x, `streamable_http_app()` returns a Starlette ASGI app whose
  `StreamableHTTPSessionManager` must be started inside the **parent** app lifespan. Mounting it on a
  FastAPI app that does not run the session manager raises at request time:
  `RuntimeError: Task group is not initialized. Make sure to use run().` The documented correct
  pattern is an `@asynccontextmanager` lifespan wrapping `async with mcp.session_manager.run(): yield`,
  passed as `FastAPI(lifespan=...)` (modelcontextprotocol/python-sdk README; issues #1220, #1367).
  Nested lifespans from the mounted sub-app are NOT auto-invoked by the parent.
- **Impact:** The HTTP MCP transport - the surface remote agents use - is broken on first request.
  CI is green only because `test_mcp.py` calls the Python `web_search` coroutine directly and asserts
  the `/mcp` route merely exists (`test_mcp.py:19`); no test drives a real MCP HTTP session, so the
  runtime failure is unobserved.
- **Recommendation:** Build the FastAPI app with a lifespan that enters `mcp.session_manager.run()`
  (use an `AsyncExitStack` if other startup work is added). Verify against the pinned SDK version.
- **Acceptance Criteria:** An MCP `initialize` handshake over HTTP to the mounted endpoint succeeds
  (returns a session) without `RuntimeError`; a `tools/list` shows `web_search`.
- **Regression Tests:** An MCP-over-HTTP integration test performing `initialize` + `tools/call`
  against the mounted app via the SDK client, asserting a passage/citation payload returns.

### MCP-2 - Double-mount: the tool is served at `/mcp/mcp`, not the documented `/mcp`
- **Severity:** Critical
- **Category:** MCP
- **Evidence:** `orchestrator/mcp_server.py:7` `mcp = FastMCP(thorondor)` (default
  `streamable_http_path=/mcp`); `orchestrator/app.py:96` `app.mount(/mcp, mcp.streamable_http_app())`.
- **Problem:** `streamable_http_app()` already serves its routes under the FastMCP internal path
  `/mcp` by default. Mounting that app under the FastAPI prefix `/mcp` composes to the effective
  endpoint `/mcp/mcp`. The design doc and the client-config snippet advertise `http://host:8080/mcp`
  (`docs/foundational design/04-api-and-agent-integration.md:157,174`). (gofastmcp.com/deployment/http;
  python-sdk issue #1367 on path composition.)
- **Impact:** Even after MCP-1 is fixed, agents configured per the documented URL hit `/mcp` and get
  404; the working endpoint is undocumented. `test_mcp.py:19` only checks a route literally named
  `/mcp` (the mount prefix) exists, which passes regardless of the internal-path mismatch.
- **Recommendation:** Either set `FastMCP(thorondor, streamable_http_path=/)` (mount prefix `/mcp`
  becomes the whole path) or mount at `/` and let FastMCP own `/mcp`. Keep the public URL exactly
  `/mcp`. While here, set `stateless_http=True` - the project is stateless-per-call (invariant 4)
  and stateless HTTP is the SDK-recommended mode for a horizontally-scaled mount.
- **Acceptance Criteria:** A request to `http://host:8080/mcp` reaches the MCP transport; no reachable
  `/mcp/mcp`.
- **Regression Tests:** A route/transport test asserting the MCP handshake resolves at exactly the
  documented public path `/mcp`.

### API-1 - No input bounds on `token_budget`, `max_urls`, `max_passages` (cost/memory exposure)
- **Severity:** High
- **Category:** API
- **Evidence:** `orchestrator/models.py:41-43` - `token_budget`, `max_urls`, `max_passages` are
  `int | None` with no `Field(ge=..., le=...)`. REST default-resolution only treats falsy values:
  `orchestrator/app.py:32-37` and `pipeline.py:54-55` use `req.token_budget or default`, so `0`
  falls back but any positive integer passes unchecked. `max_urls` flows straight into selection
  (`pipeline.py:78` -> `selection.py` slice `[:max_urls]`) and thus the crawl fan-out (`pipeline.py:84`).
- **Problem:** A hostile/buggy caller can send `max_urls: 100000` (uncapped crawl fan-out vs
  SearXNG/Crawl4AI -> resource exhaustion; defeats the select/budget-protects-the-crawl intent and
  invariant 7 crawl-politely), a huge `token_budget` (assembler accumulates essentially all chunks;
  `assembly.py:22` only skips when over budget -> unbounded passage list/memory), or a negative value.
  `query: str` (`models.py:40`) has no `min_length`/`max_length` either.
- **Impact:** Unbounded cost and memory from a single request on both surfaces; the MCP tool inherits
  the gap because it builds the same `SearchRequest`.
- **Recommendation:** Add Pydantic `Field` constraints to the shared model so both surfaces inherit
  them: `query=Field(min_length=1, max_length=...)`, `token_budget=Field(default=None, ge=1, le=MAX)`,
  `max_urls=Field(default=None, ge=1, le=HARD_MAX_URLS)`, `max_passages=Field(default=None, ge=1, le=...)`.
  Validate centrally so REST returns 422 and MCP returns a tool error rather than fanning out.
- **Acceptance Criteria:** Out-of-range `token_budget`/`max_urls`/`max_passages`/empty `query` are
  rejected (REST 422) on both surfaces; crawl fan-out can never exceed a server hard cap regardless of
  request value.
- **Regression Tests:** Schema/bounds tests per field (too-large, zero, negative, empty query); a test
  asserting `max_urls` above the hard cap is rejected/clamped before `extract` is called.

### MCP-3 - MCP `web_search` discards `stats`/`reason`/`reranked` -> degraded-200 contract invisible
- **Severity:** High
- **Category:** MCP / Contracts
- **Evidence:** `orchestrator/mcp_server.py:31-34` returns only `{passages, citations}`; it drops
  `response.stats` (incl. `reason`, `reranked`) and `response.raw_markdown`. REST returns the full
  model (`app.py:30` `response_model=SearchResponse`; `models.py:51-56`).
- **Problem:** Invariants 5/10 and the architecture map (section 8) make `reason`, `reranked`, and
  `stats` part of the observable degraded-200 contract. On an empty/degraded result the pipeline
  returns empty `passages`/`citations` plus a populated `stats.reason` (`pipeline.py:47-49,72,82,88,98`).
  An MCP agent receiving empty passages+citations cannot distinguish `no_results_from_discovery`
  (reword and retry) from `all_crawls_failed` from a reranker degrade (`reranked: false`, order is
  discovery-rank). The surface strips the very signals the design promises agents use for the next hop.
- **Impact:** MCP callers are blind to degradation; identical inputs that produce an informative
  degraded REST response produce an opaque empty MCP response. Breaks REST/MCP parity (invariant 8).
- **Recommendation:** Return `stats` (at minimum `reason` and `reranked`) and include `raw_markdown`
  when requested. Prefer returning the full `SearchResponse.model_dump()` so the surfaces are twins.
- **Acceptance Criteria:** For any degraded outcome, the MCP tool result carries the same `reason` and
  `reranked` as the REST response for the same input.
- **Regression Tests:** Degraded-`reason` tests per code (`no_results_from_discovery`,
  `all_crawls_failed`, `no_chunks_after_dedup`) asserting the MCP result exposes `reason`; a
  `reranked: false` test (inject `DownReranker`) asserting both surfaces report it.

### MCP-4 - MCP hardcodes `token_budget=4000`/`max_urls=6`, bypassing settings; twins can drift
- **Severity:** High
- **Category:** MCP / Contracts
- **Evidence:** `orchestrator/mcp_server.py:25` `web_search(query, token_budget=4000, max_urls=6)`;
  `:28` builds `SearchRequest(query=query, token_budget=token_budget, max_urls=max_urls)`. REST
  resolves defaults from settings/deps (`app.py:34-35` -> `deps.default_token_budget`/`default_max_urls`,
  sourced from `Settings.default_token_budget`/`max_urls`, `settings.py:41,44,64,67`, env
  `DEFAULT_TOKEN_BUDGET`/`MAX_URLS`).
- **Problem:** MCP defaults are literals in the signature; REST defaults come from env-driven
  settings. If an operator sets `DEFAULT_TOKEN_BUDGET=8000` / `MAX_URLS=10`, REST honors it and MCP
  does not - the thin twins now behave differently for an unspecified field (invariant 8 violated).
  The MCP tool also never exposes `max_passages`, `decompose`, `freshness`, `domains`,
  `exclude_domains`, `include_raw_markdown`, so agents cannot use budget/allowlist controls the REST
  contract advertises. (`decompose` defaults True on the built request but the identity planner makes
  it a no-op, `pipeline.py:58`, so that one is benign today.)
- **Impact:** Divergent defaults between surfaces; MCP agents silently get a different budget than the
  operator configured; missing knobs make MCP strictly weaker than REST.
- **Recommendation:** Resolve MCP defaults from the same deps/settings path REST uses (accept
  `token_budget`/`max_urls` as Optional and fall back to `deps.default_*`), and expose the remaining
  documented request fields. Cleanest: one shared request-builder used by both surfaces.
- **Acceptance Criteria:** With `DEFAULT_TOKEN_BUDGET`/`MAX_URLS` overridden, an MCP call omitting
  those args uses the same resolved values as the REST call omitting them.
- **Regression Tests:** REST<->MCP parity test (below) including a settings-override case; a test
  asserting every REST-documented request field is reachable via the tool.

### API-2 - `/healthz` reports all dependencies healthy once deps are built; no live probe (warm path)
- **Severity:** Medium
- **Category:** API
- **Evidence:** `orchestrator/app.py:61-72` - when `deps is not None`, returns a hardcoded all-True
  dependency map. Live `_check_url` probes (`app.py:85-90`) only run on the cold path where `deps`
  is still `None`.
- **Problem:** `get_deps()` is invoked on the first `/search` (`app.py:34`) and memoized
  (`app.py:23-27`), so in any running deployment `deps` is non-None almost immediately. From then on
  `/healthz` always returns all-True regardless of actual reachability - the opposite of a readiness
  probe. The Docker `HEALTHCHECK` (`orchestrator/Dockerfile:10-11`) depends on this endpoint and will
  report healthy even when SearXNG or the chunker is down (which hard-fails every search with 503 per
  `pipeline.py:65-66,93-94`).
- **Impact:** Liveness/readiness probes and the container healthcheck give false-green; orchestration
  will not restart or de-route a broken instance.
- **Recommendation:** Always run the live `_check_url` probes; do not short-circuit on
  `deps is not None`. Cache results briefly if probe cost is a concern.
- **Acceptance Criteria:** With a dependency unreachable, `/healthz` reports that dependency false
  even after deps have been built.
- **Regression Tests:** A `/healthz` test with a down dependency asserting the corresponding flag is
  false after `get_deps()` has run.

### MCP-5 - Tool docstring is a single line; omits budget semantics, return shape, when-to-call
- **Severity:** Medium
- **Category:** MCP / Contracts
- **Evidence:** `orchestrator/mcp_server.py:26` - docstring is the single line: Search the live web
  and return reranked, citation-bearing passages. The design doc specifies a far richer docstring with
  usage guidance, an `Args:` block, and a `Returns:` block
  (`docs/foundational design/04-api-and-agent-integration.md:116-130`).
- **Problem:** The FastMCP tool docstring is what an LLM sees as the tool description. The as-built
  one-liner tells the model nothing about: when to call it (current/open-web info), that
  `token_budget` is the sizing control to fit context (invariant 6 budget-not-count), what `max_urls`
  does, or the return shape it must parse. The documented version explaining all this was not ported.
- **Impact:** Agents use the tool worse - wrong/absent `token_budget`, misparsed returns, calling it
  when not needed. The agent-surface contract treats the docstring as load-bearing; it was regressed.
- **Recommendation:** Port the documented docstring (usage, `Args` with budget semantics, `Returns`
  with the exact shape). Keep it in sync with the actual return shape once MCP-3/CON-2 are fixed.
- **Acceptance Criteria:** Tool description includes when-to-call, `token_budget` semantics,
  `max_urls`, and an accurate `Returns` shape matching what the tool emits.
- **Regression Tests:** A test asserting the tool description/schema contains the budget-semantics and
  return-shape text (guards against re-truncation).

### CON-1 - No API/tool version or staged-migration mechanism for the wire contract
- **Severity:** High
- **Category:** Contracts
- **Evidence:** REST route is unversioned `@app.post(/search)` (`app.py:30`); the MCP server id is
  `FastMCP(thorondor)` with tool name `web_search` and no version (`mcp_server.py:7,24`). `models.py`
  carries no schema/contract version field. The design doc treats `stats`/`reason`/`reranked` as the
  stable contract (`docs/.../04-...md:54-75`) but nothing pins it.
- **Problem:** Invariant 10 requires not breaking the public REST/MCP wire contract without a staged
  migration. There is no version on the path, no version field in the response, and no negotiation -
  any future change to the `/search` or `web_search` shape (adding required fields, renaming `reason`
  codes, changing passage shape) breaks every existing agent at once with no transition window.
- **Impact:** No safe path to evolve the contract; the very shapes the design calls stable have no
  guardrail. Agents cannot detect or pin a contract version.
- **Recommendation:** Decide and document the stable surface: (a) version the REST path (`/v1/search`)
  or add `schema_version`/`api_version` to `SearchResponse`; (b) treat `reason` codes as a closed,
  documented enum; (c) make additive-only the default change policy and reserve breaking changes for a
  new version. For MCP, version the tool name or expose a version in the server info. Document which
  of `stats`/`reason`/`reranked` are part of the stable contract vs best-effort.
- **Acceptance Criteria:** A documented versioning policy exists; a client can determine the contract
  version it is talking to; adding a field does not break existing agents.
- **Regression Tests:** A schema/contract snapshot test (golden JSON schema of `SearchResponse` and
  the tool I/O schema) that fails on any non-additive change, forcing a deliberate version bump.

### CON-2 - MCP passage/return shape diverges from REST and from the doc example
- **Severity:** Medium
- **Category:** Contracts
- **Evidence:** MCP returns `passage.model_dump()` (`mcp_server.py:32`), which includes
  `text, score, token_count, citation_id` (`models.py:7-11`). The doc MCP `Returns` block lists
  passages as text/score/citation_id only - omitting `token_count` (`docs/.../04-...md:129`).
  Separately, REST emits the full `SearchResponse` (with `stats`, `raw_markdown`) while MCP emits a
  bespoke 2-key dict (MCP-3).
- **Problem:** Three shapes are in play: REST response, MCP response, and the documented MCP example.
  An agent author reading the doc builds against a shape the code does not emit; an agent moving
  between REST and MCP must handle two envelopes.
- **Impact:** Integration friction and silent mismatches; undermines thin-twins (invariant 8).
- **Recommendation:** Make the MCP tool return the same envelope as REST (full `SearchResponse` dump),
  and reconcile the doc `Returns` block to the actual passage fields. One shape, documented once.
- **Acceptance Criteria:** REST and MCP serialize passages/citations identically; the doc `Returns`
  matches the emitted keys.
- **Regression Tests:** A shape-equivalence test asserting the MCP result `passages[0]` keys equal the
  REST `passages[0]` keys for the same input.

### CON-3 - `mcp` dependency is unpinned; transport behavior is non-reproducible
- **Severity:** Low
- **Category:** Contracts / MCP
- **Evidence:** `orchestrator/requirements.txt:5` `mcp` (no specifier). Sandbox shows 1.26.0 and
  1.27.2 resolving for the same line.
- **Problem:** `streamable_http_app()` mounting/lifespan/session-manager semantics changed across the
  MCP 1.x line (python-sdk issues #1220, #1367). An unpinned dependency means a rebuild can change the
  transport contract under the same code - and makes the MCP-1/MCP-2 fixes brittle to verify.
- **Impact:** Non-reproducible MCP behavior between builds; verifying against the pinned SDK version
  is impossible because nothing is pinned.
- **Recommendation:** Pin a tested minor (e.g. `mcp==1.27.2` or `mcp>=1.27,<1.28`) and record the
  version the mounting code was validated against.
- **Acceptance Criteria:** `requirements.txt` pins a concrete MCP version range; the MCP integration
  test (MCP-1) runs against that pin.
- **Regression Tests:** Covered by the MCP-over-HTTP integration test pinned to the chosen version.

---

## Cross-surface parity regression test (applies to MCP-3, MCP-4, CON-2; enforces invariant 8)
Add one parametrized test that, for a set of identical inputs (default request; custom
`token_budget`/`max_urls`; `include_raw_markdown=True`; each degraded `reason`; `reranked=false`),
runs both surfaces against the **same** `fakes.deps()` and asserts the REST response and the MCP tool
result expose identical `passages`, `citations`, `stats.reason`, and `stats.reranked`. This is the
guardrail that the thin-twins-over-one-pipeline-function invariant actually holds; today
`test_mcp.py` only asserts the MCP result has exactly {passages, citations} (`test_mcp.py:13`),
which actively encodes the divergence rather than catching it.

