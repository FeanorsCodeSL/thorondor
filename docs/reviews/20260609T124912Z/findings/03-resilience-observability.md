# 03 - Resilience and Observability Review

Read-only specialist review of Thorondor failure posture and operability. Grounded in
00-architecture-map.md and verified against code. All paths under thorondor/.

## Severity tally
- High: 3 (RES-1, RES-2, OBS-1)
- Medium: 6 (RES-3, RES-4, RES-5, OBS-2, OBS-3, OBS-4)
- Low: 5 (RES-6, RES-7, RES-8, OBS-5, OBS-6)

The core graded-failure posture (invariant 9) is largely correct: SearXNG-down and chunker-down
hard-fail (503), Crawl4AI degrades partial, embedding degrades inside the chunker, reranker degrades
to discovery order with reranked=false, planner degrades silently to identity. The defects are at the
edges: /healthz lies when warm, MCP drops the entire observability contract, there are no correlation
IDs, and several timeouts/concurrency knobs are coarse or unbounded under adversarial input.

---

## Resilience (RES)

### RES-1 - /healthz reports all dependencies healthy without probing once deps are warm
- Severity: High
- Category: Resilience
- Evidence: orchestrator/app.py:61-72. When deps is not None (true after the first /search, since
  get_deps() lazily memoizes the singleton), /healthz returns a hardcoded all-True dependencies map and
  never issues any probe. Test orchestrator/tests/test_app_rest.py:43-50 asserts only the shape,
  locking in the lie.
- Problem: The real per-dependency probing in _check_url (app.py:47-53,85-90) only runs on the cold
  path (deps is None) - i.e. essentially never in a running container, because the Docker HEALTHCHECK
  (03-deployment.md:69-70) hits /healthz only after uvicorn is up and the first request has usually
  built deps. After warm-up, a dead SearXNG/Crawl4AI/chunker/reranker still reports True.
- Impact: Directly violates the documented requirement (03-deployment.md:259-262, 01-architecture.md
  section 7) that /healthz aggregate downstream reachability per-dependency. Load balancers route
  traffic to a node whose dependencies are down; operators get no degradation signal. This is the
  single biggest observability regression.
- Recommendation: Always probe (run the asyncio.gather over _check_url regardless of whether deps is
  built), and set top-level status to degraded when any probe is false (optionally distinguish hard-fail
  deps searxng/chunker from degradable deps crawl4ai/reranker). Probe the same endpoints the pipeline
  uses. Do not short-circuit on the warm path.
- Acceptance Criteria: With deps warm and SearXNG unreachable, GET /healthz returns
  dependencies.searxng == false and a non-ok aggregate status; with all reachable, all true.
- Regression Tests: failure-injection test that monkeypatches appmod.deps to a built value AND points a
  settings URL at a closed port, asserts the corresponding dependency is false (replace
  test_healthz_shape which currently only checks keys).

### RES-2 - Crawl fan-out has no per-task cancellation; one slow target stalls the call up to the timeout
- Severity: High
- Category: Resilience
- Evidence: orchestrator/clients/crawl4ai_client.py:18-38. extract does await asyncio.gather over
  one(url) for each url. Each one opens httpx.AsyncClient(timeout=self.timeout_s) (line 24), so the
  per-URL timeout is enforced, but gather waits for the slowest of all tasks. With CRAWL_CONCURRENCY=4
  (settings.py:42) and e.g. 6 URLs, the last batch slow URL holds the whole extract for up to
  crawl_timeout_s.
- Problem: httpx timeout=N is a per-operation timeout (connect/read/write/pool each get N), not a
  wall-clock cap; a server that dribbles bytes just under the read timeout can extend a single fetch
  well beyond crawl_timeout_s. There is no asyncio.timeout/wait_for wrapping the gather and no
  cancellation of still-running fetches once enough pages are in hand.
- Impact: Borderline-meets the invariant that one slow target never stalls the whole call - the per-URL
  timeout bounds it, but the wall-clock bound is ceil(N/concurrency) * crawl_timeout_s, not
  crawl_timeout_s. Under adversarial/slow targets the /search latency is unbounded relative to a single
  timeout knob, and there is no early-return on partial success.
- Recommendation: Wrap the whole fan-out in an overall budget (async with asyncio.timeout(total)) and on
  expiry proceed with whatever completed (cancel the rest); use explicit httpx.Timeout(connect, read)
  rather than a single scalar; reuse one AsyncClient (see RES-6). Optionally support a
  first-K-pages-then-cancel early-exit.
- Acceptance Criteria: With one URL that sleeps far longer than the others, extract returns the fast
  pages within roughly the overall budget and the slow task is cancelled, not awaited.
- Regression Tests: failure-injection test with a mock transport where one URL hangs; assert extract
  returns the other pages and total time is bounded by the overall budget; assert the pipeline still
  yields stats.urls_crawled_ok < selected and a 200.

### RES-3 - Discovery fan-out is unbounded by sub-query count (DoS amplification via the planner)
- Severity: Medium
- Category: Resilience
- Evidence: orchestrator/pipeline.py:62-64: asyncio.gather over deps.discovery.search(q, ...) for each q
  in subqueries, with no semaphore. Each call is a 15s httpx request (searxng_client.py:20) opening a
  fresh client.
- Problem: subqueries comes from the planner. The default IdentityPlanner yields exactly one, and the
  prompt asks the LlmPlanner for 1-3 (planner.py:27), but LlmPlanner.plan does NOT cap the parsed array
  (planner.py:37-39): a misbehaving or prompt-injected LLM can return an arbitrarily long JSON array,
  each element becoming a concurrent SearXNG request with no bound.
- Impact: Unbounded fan-out against SearXNG (which the deployment doc notes is rate-limit/CAPTCHA prone,
  03-deployment.md:231-236); a single /search can issue dozens of upstream searches. Also a
  latency/backpressure cliff.
- Recommendation: Cap planner output (e.g. planned[:3]) and/or bound the discovery gather with an
  asyncio.Semaphore. Treat the planner as untrusted input.
- Acceptance Criteria: A planner returning 50 sub-queries results in at most N (e.g. 3) discovery calls.
- Regression Tests: failure-injection test with a FakePlanner returning more than K sub-queries; assert
  discovery is called at most K times.

### RES-4 - Reranker degrade is correct, but a partial/short reranker 200 silently drops chunks
- Severity: Medium
- Category: Resilience
- Evidence: orchestrator/clients/reranker_client.py:28-36. On 200, scored is built only from the
  results/data items returned; if the reranker returns fewer items than documents (or an empty results
  list with status 200), those chunks are silently dropped and stats.reranked is still set True
  (pipeline.py:101-103). An empty results list yields zero scored chunks - and the pipeline does NOT
  re-check for empty after rerank.
- Problem: A reranker that returns 200 with a truncated/empty results array is treated as success.
  Downstream assemble then operates on a shrunken or empty set; if empty, the call returns 200 with
  passages empty but no reason code (the no_chunks_after_dedup guard is before rerank, pipeline.py:96).
- Impact: Quality loss with no observability; a degenerate-but-200 reranker produces empty results with
  reranked=true and no reason, indistinguishable from genuinely nothing relevant.
- Recommendation: If len(scored) < len(chunks), log and fall back (append missing chunks in discovery
  order) or treat a wildly short response as RerankerUnavailable (degrade with reranked=false). Add a
  post-rerank empty guard that sets a reason.
- Acceptance Criteria: A reranker returning an empty results list produces either a degraded
  reranked=false ordering or an empty response with an explicit reason - never reranked=true plus silent
  empty.
- Regression Tests: failure-injection test with a fake reranker returning fewer results than inputs;
  assert chunk count preserved or reranked=false.

### RES-5 - Embedding degradation is invisible to the orchestrator and to /search stats
- Severity: Medium
- Category: Resilience / Observability
- Evidence: Architecture map section 8 plus semantic-chunking-service/chunking/cluster_semantic.py
  fallbacks. When the embedding endpoint is down, the chunker falls back to greedy token chunking and
  returns 200 chunks; the orchestrator sees normal chunks. ChunkResponse (chunker models.py) carries
  strategy_version but no degradation flag, and ChunkerClient (chunker_client.py:30-41) reads only
  text/token_count/position, discarding it.
- Problem: Invariant 9 says embedding-down is a graded degrade (lower quality), but it is completely
  unobservable end-to-end. Operators cannot tell a high-quality semantic result from a greedy-token
  fallback.
- Impact: Silent quality regression; no signal in /healthz (orchestrator does not probe embedding
  directly - it is only reachable transitively via the chunker) nor in /search stats.
- Recommendation: Have the chunker surface the actual strategy used (e.g. degraded=true or
  fallback=token in the response or per-chunk metadata), have ChunkerClient propagate it, and add a
  chunk_strategy/embedding_degraded field to SearchStats. Surface the chunker nested embedding health in
  the orchestrator /healthz aggregate rather than collapsing it to a bool.
- Acceptance Criteria: With embedding down, /search stats expose that chunking ran in fallback mode;
  /healthz distinguishes chunker-up-but-embedding-down.
- Regression Tests: failure-injection test (embedding endpoint refused) asserting chunker returns a
  fallback marker and orchestrator stats reflect it.

### RES-6 - httpx clients are created per call/per URL; no connection reuse or pool ceiling
- Severity: Low
- Category: Operations
- Evidence: Every client opens async with httpx.AsyncClient(...) per invocation: searxng_client.py:20,
  crawl4ai_client.py:24 (per URL), chunker_client.py:18, reranker_client.py:19, planner.py:19,
  app.py:49 (per healthz probe), and the chunker httpx.Client per batch (embedding_function.py:154). No
  shared client, no pool, no limits set.
- Problem: Not a leak (context managers close cleanly), but every request pays connect/handshake cost
  and there is no connection-pool ceiling, so concurrent /search calls can open unbounded sockets to
  each backend. The crawl client opens a brand-new client per URL inside the semaphore.
- Impact: Higher latency and FD/socket pressure under load; no global backpressure on outbound
  connections.
- Recommendation: Construct one AsyncClient per stage client (with httpx.Limits) at deps-build time,
  reuse across calls, close on shutdown. In the crawl client, share one client across the whole extract
  fan-out.
- Acceptance Criteria: A burst of N concurrent searches does not open N x sockets per backend; clients
  are reused.
- Regression Tests: assert client construction count is O(1) per stage per process (mock
  httpx.AsyncClient).

### RES-7 - No retries with caps/jitter on any transient backend failure
- Severity: Low
- Category: Resilience
- Evidence: Grep across orchestrator/ for retry/backoff/jitter/tenacity/sleep returns no retry logic
  anywhere. Every client does a single attempt then raises/drops.
- Problem: Not a correctness bug (single-attempt-then-degrade is a valid posture and avoids retry-storm
  DoS), but a transient 502/connection-reset from SearXNG hard-fails the whole call (503), and a
  transient crawl failure permanently drops that URL.
- Impact: Lower effective availability for hard-fail deps. Acceptable as a deliberate choice but should
  be a documented decision, not an omission.
- Recommendation: If adding retries, cap attempts (1-2) with full jitter and only on idempotent GETs
  (SearXNG discovery, crawl). Never retry the chunker whole multi-page loop blindly. Document the
  no-retry stance if intentional.
- Acceptance Criteria: Documented; if implemented, retries are bounded and jittered and do not multiply
  fan-out.
- Regression Tests: n/a unless implemented.

### RES-8 - Startup-not-ready is tolerated, but RERANKER_HEALTH_PATH default mismatches compose
- Severity: Low
- Category: Operations
- Evidence: Deps are built lazily on first request (app.py:23-27, build_deps_from_settings constructs
  clients but performs no network calls at build time), so a not-yet-ready dependency at startup does
  not crash the orchestrator - invariant satisfied. However settings.py:61 defaults
  RERANKER_HEALTH_PATH=/healthz while .env.example:13 and docker-compose.yml:22 set /health
  (architecture map D4).
- Problem: The cold-path /healthz reranker probe (app.py:89) uses this path; default vs compose
  disagreement means a reranker that exposes /health is probed at the wrong path and reports false even
  when up (only matters on the cold path, which RES-1 shows is the only path that probes).
- Impact: Spurious reranker-unhealthy signal when relying on the code default.
- Recommendation: Align the default to /health (matches TEI and compose) or fix the env files; pick one
  and make the three sources agree.
- Acceptance Criteria: Code default, .env.example, and compose all use the same reranker health path.
- Regression Tests: settings test asserting the default equals the compose value.

---

## Observability (OBS)

### OBS-1 - MCP web_search drops the entire observability contract (stats, reason, reranked)
- Severity: High
- Category: Observability
- Evidence: orchestrator/mcp_server.py:31-34 returns only passages and citations. The REST surface
  returns full stats, reason, and raw_markdown (pipeline.py:122-128, models.py:51-56). Architecture map
  D6/section 8 flags the same.
- Problem: Violates invariant 8 (REST/MCP thin twins, behavioral parity) and invariant 10 (preserve the
  stats/reason/reranked wire contract). An MCP agent cannot tell a fully-reranked answer from a degraded
  discovery-order answer (reranked=false), cannot see reason codes (no_results_from_discovery,
  all_crawls_failed, no_chunks_after_dedup), and cannot read tokens_returned/elapsed_ms. A degraded call
  looks identical to a healthy one.
- Impact: The agent loop loses every degradation signal precisely where the agent owns the loop and most
  needs to decide whether to retry/broaden. Silent-quality-loss vector on the primary agent surface.
- Recommendation: Return stats (at minimum reranked and reason) from web_search. If the MCP tool
  intentionally returns a slimmer shape, include at least reranked, reason, tokens_returned. Keep both
  surfaces sourced from the same SearchResponse so they cannot drift.
- Acceptance Criteria: A degraded run (reranker down) over MCP exposes reranked=false; a
  no_results_from_discovery run exposes the reason.
- Regression Tests: parity test asserting MCP and REST expose the same reranked/reason for the same
  injected failure (extend orchestrator/tests/test_mcp.py).

### OBS-2 - No correlation/trace IDs across agent to orchestrator to chunker to embedding/reranker
- Severity: Medium
- Category: Observability
- Evidence: Grep for request-id/correlation/trace-id/X-Request/contextvar across the repo: no matches.
  No middleware sets/propagates a request ID; ChunkerClient, RerankerClient, SearxngDiscovery,
  Crawl4aiExtractor, and EmbeddingFunction send no correlation header.
- Problem: A single /search fans out to SearXNG, N crawl fetches, the chunker (which fans out to
  embedding), and the reranker, across two services - with no shared ID to stitch logs together.
- Impact: Debugging a slow/degraded call across the orchestrator and chunker is guesswork; you cannot
  correlate a chunker embedding-fallback log line with the originating /search.
- Recommendation: Add an ASGI middleware that reads/generates X-Request-ID, stores it in a contextvar,
  includes it in structured logs, and forwards it as a header on every downstream httpx call
  (orchestrator to chunker to embedding). Return it in the response and on the MCP path.
- Acceptance Criteria: A request with an X-Request-ID header appears in orchestrator and chunker logs
  for that call, and is forwarded downstream.
- Regression Tests: assert the header is propagated to a mocked chunker/reranker transport.

### OBS-3 - No structured logging; near-silent orchestrator, ad-hoc string logs with no redaction policy
- Severity: Medium
- Category: Observability
- Evidence: The only orchestrator logging is logger.warning in crawl4ai_client.py:27,34. There is no
  logging config in app.py (the chunker has logging.basicConfig(level=logging.INFO) at app.py:12). No
  structured/JSON logging, no per-request access-log enrichment, no documented redaction policy. The
  chunker embedding logs at INFO include the endpoint and on failure log the embedding server raw
  error_text/response.text (embedding_function.py:142,164-168).
- Problem: Two gaps: (1) the orchestrator emits essentially nothing for a successful or degraded call,
  so degradation is invisible in logs; (2) there is no redaction guarantee - if a BYO endpoint embeds
  credentials in the URL/path (e.g. an API key in a query string) or an upstream error echoes a key, it
  can land in logs at INFO/ERROR. Crawled bodies are not logged (good), but no policy enforces that.
- Impact: Weak operability; potential secret leakage into logs (never log API keys or full crawled
  bodies at info level).
- Recommendation: Add structured (JSON) logging with a per-call summary line (query hash, sub-query
  count, urls discovered/selected/crawled_ok, reranked, reason, elapsed_ms - never the raw query body or
  markdown). Add a redaction helper for endpoint URLs (strip userinfo/query) before logging; never log
  full upstream error bodies above DEBUG. Document the policy.
- Acceptance Criteria: A degraded call emits one structured log line with the stats summary and no
  secrets; endpoint logging strips credentials.
- Regression Tests: assert a log record for a failing embedding call does not contain a configured
  secret token; assert the per-call summary line is emitted.

### OBS-4 - stats block is incomplete: no chunk-strategy signal, no explicit crawl-failure or dedup counts
- Severity: Medium
- Category: Observability
- Evidence: SearchStats (models.py:26-36) carries sub_queries, urls_discovered, urls_selected,
  urls_crawled_ok, chunks_produced, chunks_reranked, reranked, tokens_returned, elapsed_ms, reason. It
  does NOT record: crawl failures (no explicit urls_failed; only derivable by subtraction), how many
  pages content_dedup dropped (pipeline.py:90 reassigns pages with no count), or
  chunk-strategy/embedding-degraded (see RES-5).
- Problem: The required fields (urls discovered/selected/crawled_ok, chunks produced/reranked, reranked,
  tokens_returned, elapsed_ms) are present and populated incrementally and accurately
  (pipeline.py:59,69,79,85,95,103,110,111). But the degradation-relevant deltas are not surfaced, so a
  partial crawl (6 selected, 1 ok) and content-dedup drops are invisible.
- Impact: Operators/agents can see that few results came back but not where they were lost (crawl
  failures vs dedup vs budget).
- Recommendation: Add urls_crawled_failed and chunks_after_dedup (or pages_deduped) to SearchStats; keep
  the existing fields. Ensure content_dedup effect is counted.
- Acceptance Criteria: A run with 3 selected / 1 crawled ok reports urls_crawled_failed == 2; a run that
  dedups a page reports the drop.
- Regression Tests: pipeline test asserting failed-crawl and dedup counters.

### OBS-5 - reranked=false conflates rerank-degraded with rerank-never-reached
- Severity: Low
- Category: Observability
- Evidence: _empty_response (pipeline.py:47-49) returns stats with reranked=false and tokens_returned=0
  regardless of where the early-return fired (no_results_from_discovery, all_crawls_failed,
  no_chunks_after_dedup all leave reranked at its default False, models.py:33).
- Problem: reranked conflates reranker-was-down with we-never-reached-the-rerank-stage.
- Impact: Minor ambiguity; the reason code disambiguates, so low severity.
- Recommendation: Treat reranked as meaningful only when reason is None (document that), or use a
  tri-state (null when rerank not reached).
- Acceptance Criteria: Documentation or model makes rerank-not-reached distinguishable from
  rerank-degraded.
- Regression Tests: assert reason present implies consumers ignore reranked.

### OBS-6 - Dead cache settings advertise a capability that does not exist (no cache implemented)
- Severity: Low
- Category: Operations / Observability
- Evidence: CACHE_BACKEND (settings.py:45,68, default memory) and REDIS_URL (.env.example:23) are
  parsed/advertised but there is NO cache class, injection point, or SearXNG/crawl caching anywhere
  (architecture map section 7, D1). The deployment doc (03-deployment.md:186-187) documents
  memory/redis/none and REDIS_URL as live knobs.
- Problem: Operators can set CACHE_BACKEND=redis plus REDIS_URL and observe zero behavioral change and
  zero error - a silent no-op. Misleading operability surface; the doc and .env over-promise.
- Impact: Operator confusion; a cache-enabled expectation that is never met. No correctness impact today
  precisely because nothing reads it (invariant 4 trivially holds: cache is not part of the contract
  because there is no cache).
- Recommendation: Either remove the dead settings and docs, or implement the cache. If implemented
  later, enforce the contract: SearXNG result sets keyed by sub-query with a short TTL; crawled markdown
  keyed by normalize_url(url) + crawl_date with a longer TTL; the cache must be an injected optimization
  (a Protocol seam in PipelineDeps, like every other stage), and disabling it (CACHE_BACKEND=none) must
  change latency only - never the passages/citations/stats contract (invariants 1 and 4). Keys must use
  the same normalize_url (normalize.py) the rest of the pipeline uses, or merge/dedup and cache will
  disagree.
- Acceptance Criteria: Either the settings are gone, or a cache exists with TTL-keyed entries and a test
  proving identical results with cache on/off/none (only elapsed_ms differs).
- Regression Tests: if implemented - parity test: same query with CACHE_BACKEND in none/memory/redis
  returns byte-identical passages/citations; TTL-expiry test; key-collision test on URL normalization
  plus crawl_date.

---

## Invariant scorecard (resilience/observability-relevant)
- Inv 1 (no persistent index): upheld - per-call state only (pipeline.py:56), no store.
- Inv 4 (stateless; cache injected, never contract): upheld today only because no cache exists (OBS-6).
  Must be enforced if a cache lands.
- Inv 7 (crawl politely / bounded concurrency): partial - CRAWL_CONCURRENCY semaphore bounds crawl
  (crawl4ai_client.py:19), but discovery fan-out is unbounded (RES-3) and there is no overall crawl
  deadline/cancellation (RES-2).
- Inv 8 (REST/MCP parity): VIOLATED - MCP drops stats/reason/reranked (OBS-1).
- Inv 9 (graded failure; only SearXNG+chunker hard-fail): upheld in run_search mapping
  (pipeline.py:65-66,93-94,104-107) and partial-crawl handling (crawl4ai_client.py); weak spots are the
  silent embedding degrade (RES-5) and partial-reranker-200 (RES-4).
- Inv 10 (preserve stats/reason/reranked wire contract): VIOLATED on MCP (OBS-1); stats incomplete on
  REST (OBS-4).
