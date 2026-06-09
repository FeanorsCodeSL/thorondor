# 01 - Pipeline & Interface Seams Review

Scope: orchestrator/ pipeline state machine (pipeline.py), pure policy stages
(selection.py, assembly.py, merge.py, content_dedup.py, normalize.py), the seven
Protocols (interfaces.py), the BYO client adapters (clients/), DI wiring
(PipelineDeps / build_deps_from_settings), deterministic fakes (fakes.py), and the
two thin surfaces (app.py, mcp_server.py). Read-only; no code modified.

Ground truth: docs/reviews/20260609T124912Z/findings/00-architecture-map.md.

## Severity tally
- Critical: 0
- High: 4  (PIPE-1, PIPE-2, IFACE-1, IFACE-2)
- Medium: 6  (PIPE-3, PIPE-4, PIPE-5, IFACE-3, IFACE-4, IFACE-5)
- Low: 5  (PIPE-6, PIPE-7, PIPE-8, IFACE-6, IFACE-7)

Overall: the pipeline is genuinely stateless-per-call, the two pure stages ARE pure
(no I/O, deterministic), seams are clean HTTP-endpoint interfaces, and no vector
store / persistent corpus exists. The structure is sound. The findings below concern
parity drift between REST and MCP, under-tested high-value policy edges, a couple of
leaky/asymmetric contracts, and a missing-stage / dead-config gap.

---

### PIPE-1 - MCP and REST diverge on defaults, request fields, and degradation signal
- **Severity:** High
- **Category:** Pipeline
- **Evidence:** orchestrator/mcp_server.py:24-34 vs orchestrator/app.py:30-44; SearchRequest defaults models.py:39-48.
- **Problem:** The two surfaces are meant to be thin twins over one run_search
  (invariant 8). They are not equivalent:
  1. MCP hardcodes token_budget=4000, max_urls=6 as tool-signature defaults
     (mcp_server.py:25). REST resolves these from injected settings
     (get_deps().default_token_budget, default_max_urls, app.py:34-36). If an operator
     sets DEFAULT_TOKEN_BUDGET/MAX_URLS to anything other than 4000/6, MCP silently
     ignores operator config while REST honors it. Same query, two answers.
  2. MCP exposes no freshness, domains, exclude_domains, decompose, or max_passages -
     REST does (models.py:45-48). An MCP agent cannot scope a search the way a REST
     caller can.
  3. MCP returns only {passages, citations} (mcp_server.py:31-34), dropping stats,
     reason, and reranked. Per the failure posture (invariant 9/10), a degraded call
     still returns 200 with a reason; MCP callers cannot observe degradation or that
     rerank was skipped.
- **Impact:** Behavioral parity (invariant 8) and "preserve stats block, reason codes,
  reranked flag" (invariant 10) are broken on the MCP surface.
- **Recommendation (incremental, preserves wire shapes):**
  - Resolve MCP defaults from the same injected deps as REST: change the tool defaults
    to None and pass token_budget/max_urls through to run_search, which already applies
    deps.default_* when None (pipeline.py:54-55).
  - Add the missing optional fields (freshness, domains, exclude_domains, max_passages)
    to the tool signature, forwarded into SearchRequest.
  - Return reason and reranked (at minimum) in the MCP dict; keep passages/citations
    first so existing callers do not break.
- **Acceptance Criteria:** With a non-default DEFAULT_TOKEN_BUDGET, MCP and REST produce
  identical passages/citations for identical inputs; an MCP caller can pass
  freshness/domains; a reranker-down call surfaces reranked:false on both surfaces.
- **Regression Tests:** Fake-backed parity test asserting MCP web_search(query) and REST
  POST /search {query} return the same passages/citations under
  fakes.deps(default_token_budget=2000, default_max_urls=3); MCP test under
  fakes.deps(reranker=DownReranker()) asserting the degradation flag is present.

---

### PIPE-2 - Selection gate gates count but not quality when discovery scores are zero
- **Severity:** High
- **Category:** Pipeline
- **Evidence:** orchestrator/selection.py:14-22; consumed at pipeline.py:78-84; score
  default at searxng_client.py:35.
- **Problem:** select applies [:max_urls] after filtering (correct count cap), but
  max_urls is the only cap and there is no per-host cap or snippet pass. Every SearXNG
  result defaults to score 0.0 when the engine omits a score (searxng_client.py:35:
  float(item.get("score") or 0.0)). When all scores are 0.0 (common across SearXNG
  engines), selection degenerates to a pure alphabetical-by-URL slice (sort key
  (-score, url), selection.py:22). The top-N-by-relevance gate becomes
  first-N-hosts-alphabetically, which is not a quality gate and can starve better
  candidates - undermining invariant 7 (selection gate protects the crawl).
- **Impact:** The stage meant to genuinely gate crawl cost gates count but not quality
  in the common zero-score case; crawl budget is spent on alphabetically-first URLs.
  This is the high-value cost/quality policy and it silently no-ops on relevance.
- **Recommendation (incremental):** Keep the pure contract. Either (a) derive a
  rank-based fallback score in discovery (e.g. score or 1/(rank+1) using SearXNG result
  order) so merge/selection have a meaningful ordering signal, or (b) document that
  selection is count-only and the relevance gate lives in rerank. Do NOT add network
  I/O to selection. The optional snippet-relevance pass (doc 01 stage 4) is legitimate
  only as a SEPARATE impure pre-filter stage, never folded into the pure selector.
- **Acceptance Criteria:** With all-zero engine scores, selection preserves a
  deterministic, relevance-meaningful order (e.g. discovery rank), not alphabetical URL
  order; max_urls still caps count.
- **Regression Tests:** Pure unit test: 10 zero-score results shuffled, assert kept
  top-N matches discovery/rank order, not sorted(url) order.

---

### PIPE-3 - no_results_from_discovery reason reused for two distinct empty states
- **Severity:** Medium
- **Category:** Pipeline
- **Evidence:** orchestrator/pipeline.py:70-72 (empty merge) and pipeline.py:80-82
  (empty selection) both emit "no_results_from_discovery".
- **Problem:** When discovery returned candidates but selection filtered them all out
  (allowlist matching nothing, or everything blocklisted), the reason is still
  no_results_from_discovery - factually wrong, since discovery DID return results.
  stats.urls_discovered > 0 while urls_selected == 0, so stats contradict the reason.
  test_pipeline.py:38-42 asserts this conflated behavior, locking it in.
- **Impact:** Operators/agents cannot distinguish "the web had nothing" from "your
  domain filter excluded everything" - different remediations. Reason codes are part of
  the contract (invariant 10).
- **Recommendation:** Add a distinct reason such as no_urls_after_selection for the
  selection-empty branch (pipeline.py:80-82); keep no_results_from_discovery for the
  truly-empty merge branch. Additive, not a breaking change.
- **Acceptance Criteria:** Empty discovery -> no_results_from_discovery; non-empty
  discovery + empty selection -> no_urls_after_selection with urls_discovered > 0 and
  urls_selected == 0.
- **Regression Tests:** Replace test_pipeline.py:38-42 to assert the new reason; add a
  fake-backed test where allowlist matches nothing.

---

### PIPE-4 - token_budget/max_urls defaulting duplicated in REST and pipeline
- **Severity:** Medium
- **Category:** Pipeline
- **Evidence:** app.py:32-37 resolves into a model_copy; pipeline.py:54-55 resolves
  again (req.token_budget or deps.default_token_budget).
- **Problem:** The same defaulting policy lives in two places. REST pre-resolves, then
  run_search re-resolves anyway (REST pre-resolution is effectively dead). MCP does NOT
  pre-resolve (PIPE-1) and relies on the pipeline copy. This duplication is exactly
  what let MCP drift; defaulting belongs in ONE place - the pipeline, which owns deps.
- **Impact:** Duplicated policy invites divergence (already realized in PIPE-1) and
  obscures that run_search is the single source of truth.
- **Recommendation:** Remove the model_copy defaulting from app.py:32-37; let
  run_search be the sole resolver and have surfaces pass the raw request through. Also
  fixes PIPE-1 root cause.
- **Acceptance Criteria:** Defaulting exists only in run_search; REST passes the request
  unmodified; existing REST tests still pass.
- **Regression Tests:** REST test asserting an unset token_budget yields a response
  budgeted to deps.default_token_budget; confirm no behavior change vs current.

---

### PIPE-5 - include_raw_markdown re-derives the URL join key and silently drops mismatches
- **Severity:** Medium
- **Category:** Pipeline
- **Evidence:** pipeline.py:113-120; citation.url = chunk.source_url (assembly.py:30);
  match via normalize_url(citation.url) against {normalize_url(page.url): page}.
- **Problem:** Matching relies on normalize_url(citation.url) == normalize_url(page.url).
  citation.url is chunk.source_url, populated from the chunking-service metadata echo
  (chunker_client.py:37) falling back to page.url. If the chunker echoes a transformed
  URL - or the crawler page URL ever differs after redirects (currently safe:
  Crawl4aiExtractor sets Page.url = url, crawl4ai_client.py:32, but fragile) - the
  guard at pipeline.py:119 silently omits raw markdown for that citation, with no
  signal why.
- **Impact:** include_raw_markdown can return a partial set with no explanation; a
  hidden coupling among crawler URL, chunker metadata echo, and assembly citation URL.
- **Recommendation:** Carry a stable join key (citation_id to source page) through
  provenance rather than re-deriving via URL normalization at the end. Simplest fix:
  build by_url from the SAME normalize_url(chunk.source_url) space the citations use, or
  attach raw_markdown keyed off citation_id at assembly time. Keep the stage pure.
- **Acceptance Criteria:** Every returned citation that has a crawled page gets
  raw_markdown; a citation that legitimately has no page is observable, not silently
  dropped.
- **Regression Tests:** Fake-backed test where chunker metadata echoes a trailing-slash
  or utm-bearing variant of the page URL; assert raw_markdown attaches for all citations.

---

### PIPE-6 - Documented pre-filter stage (stage 8) absent
- **Severity:** Low
- **Category:** Pipeline
- **Evidence:** pipeline.py:90-101 goes chunk -> rerank with no cosine narrowing;
  arch-map row 8 (NOT IMPLEMENTED); doc 01 section 2 stage 8.
- **Problem:** The documented optional pre-filter (cheap cosine narrowing before rerank)
  does not exist. Acceptable as optional, but every produced chunk is shipped to the
  reranker, so rerank cost is unbounded by chunk count.
- **Impact:** Reranker payload scales with total chunks across all crawled pages; for
  large pages this dominates cost. Low severity - rerank degrades gracefully and the
  invariants do not require pre-filter.
- **Recommendation:** Either implement pre-filter as a SEPARATE optional impure stage
  (BYO embedding endpoint behind a new Protocol, kept OUT of the pure stages) or update
  doc 01 to mark stage 8 deferred. Do not fold cosine into selection/assembly (breaks
  purity).
- **Acceptance Criteria:** Docs and code agree on whether pre-filter exists; if
  implemented, it is a discrete stage with its own Protocol and fake.
- **Regression Tests:** If implemented, a fake pre-filter with a deterministic stub
  embedding narrowing N to k chunks, asserted in a pipeline test.

---

### PIPE-7 - merge_dedup discards duplicate provenance with no corroboration signal
- **Severity:** Low
- **Category:** Pipeline
- **Evidence:** orchestrator/merge.py:6-14.
- **Problem:** When the same normalized URL appears from multiple sub-queries/engines,
  merge keeps only the single highest-score DiscoveryResult and drops the rest. Doc 01
  stage 3 describes re-scoring using SearXNG aggregate ranking (a URL found by many
  engines is more trustworthy). As-built there is no aggregation, so a URL ranked highly
  by three engines ranks identically to one ranked highly by one engine - weakening the
  selection signal (compounds PIPE-2).
- **Impact:** Multi-engine corroboration is discarded; selection ordering is weaker than
  designed. Low severity (rerank is the real quality gate).
- **Recommendation:** Optionally fold a small corroboration bonus (count of source sets
  containing the URL) into the kept score, staying pure and deterministic; or document
  merge as max-score only.
- **Acceptance Criteria:** Documented behavior matches code; if aggregation added it is
  pure and deterministic.
- **Regression Tests:** Pure unit test: a URL present in 3 result sets ranks above an
  equal-max URL present in 1.

---

### PIPE-8 - decompose default awaits a no-op IdentityPlanner; toggle is misleading
- **Severity:** Low
- **Category:** Pipeline
- **Evidence:** pipeline.py:58; models.py:44 (decompose: bool = True);
  build_deps_from_settings installs IdentityPlanner when LLM unset (pipeline.py:139-143).
- **Problem:** Default decompose=True plus default IdentityPlanner means every default
  call awaits IdentityPlanner.plan (a no-op returning [query]). Harmless to output, but
  decompose reads as a meaningful toggle while it does nothing unless an LLM planner is
  wired - behavior depends on env wiring not visible in the request contract. (Rerank
  correctly scores the ORIGINAL query at pipeline.py:101, so the
  sub-query-as-discovery-device invariant holds.)
- **Impact:** Minor confusion / dead toggle by default. No invariant broken.
- **Recommendation:** Document that decompose has effect only when an LLM planner is
  configured; consider default decompose=False to avoid awaiting a no-op and make the
  toggle honest. Low priority.
- **Acceptance Criteria:** decompose semantics documented; default output unchanged
  (still [query]).
- **Regression Tests:** Existing test_custom_planner_called_when_decompose_true covers
  the True path; add one asserting IdentityPlanner yields [query] regardless.

---

### IFACE-1 - Reranker adapter normalizes score shapes but not missing index; parse errors escape as uncaught crash
- **Severity:** High
- **Category:** Interfaces
- **Evidence:** orchestrator/clients/reranker_client.py:18-36.
- **Problem:** The shim absorbs alternate result keys (results/data,
  score/relevance_score/rank_score) and alternate paths (/rerank vs /reranking,
  settings.py:60 plus the llamacpp recipe). But it assumes every result item carries an
  integer index (line 32 reads item index as int). Some real rerank servers do not: TEI
  returns index (ok), but some Jina/Cohere-style /rerank responses return results in
  input order WITHOUT an explicit index, or echo document text instead. If index is
  absent, the lookup raises KeyError - and the try/except at lines 18-25 wraps ONLY the
  HTTP call, not the parse at 28-36. So a shape mismatch propagates a raw KeyError out
  of run_search instead of degrading. Invariant 9 requires reranker problems to degrade
  to discovery order, not hard-fail.
- **Impact:** A BYO reranker returning a valid-but-index-less shape turns a
  graceful-degradation path into an uncaught 500. The contract-over-implementation / BYO
  interchangeability promise is violated for a common shape.
- **Recommendation:** In the parser, treat a missing index as positional (enumerate
  order) and wrap the parse loop so any malformed item raises RerankerUnavailable, which
  run_search already catches (pipeline.py:104-107 -> discovery-order fallback). Keep
  normalization behind the interface; do not leak shape handling into the pipeline.
- **Acceptance Criteria:** A /rerank response lacking index is handled positionally; a
  structurally broken response raises RerankerUnavailable (-> 200 with reranked:false),
  never an uncaught exception.
- **Regression Tests:** reranker_client tests (httpx.MockTransport): results without
  index; results with document echo only; malformed item -> RerankerUnavailable.

---

### IFACE-2 - SelectionPolicy and ResultAssembler have no deterministic FAKE; pipeline tests run the real impls
- **Severity:** High
- **Category:** Interfaces
- **Evidence:** fakes.py:85,89 wire SelectionPolicyImpl() and ResultAssemblerImpl()
  directly into deps(); arch-map notes real-impl-used-in-fakes. No FakeSelector /
  FakeAssembler exist.
- **Problem:** Each seam should have a real impl AND a deterministic fake. Five of seven
  do (planner, discovery, extractor, chunker, reranker). The two PURE seams do not -
  pipeline tests run the production selection/assembly. Consequences:
  1. Pipeline tests cannot exercise branching independently of selection/assembly
     (cannot force assembly to return empty to test a downstream path, or force
     selection to return a known set). A bug in SelectionPolicyImpl would break
     unrelated pipeline tests and vice-versa - entanglement.
  2. Protocol substitutability is unproven: no second implementation demonstrates the
     contract is honestly minimal/swappable.
  These are the high-value cost/quality policy stages; absent fakes, pipeline tests
  assert combined behavior rather than the pipeline wiring alone.
- **Impact:** Hidden coupling between pipeline tests and policy impls; reduced confidence
  the Protocols are true seams.
- **Recommendation:** Add FakeSelector (returns input unchanged or a fixed subset) and
  FakeAssembler (one passage per chunk, trivial citations) to fakes.py and let deps()
  accept them via overrides. Keep the real impls as the DEFAULT in deps() so existing
  tests pass, but make fakes available for branch isolation.
- **Acceptance Criteria:** fakes.py exposes a deterministic fake for all seven Protocols;
  at least one pipeline test uses a fake selector/assembler to isolate a branch.
- **Regression Tests:** Pipeline test using FakeAssembler returning [] asserts the
  tokens_returned == 0 / empty-passages path; test using FakeSelector returning a fixed
  single URL asserts crawl receives exactly that URL.

---

### IFACE-3 - SelectionPolicy takes pre-merged blocklist/allowlist; source-merge lives in the pipeline
- **Severity:** Medium
- **Category:** Interfaces
- **Evidence:** pipeline.py:74-78 builds the effective blocklist (settings blocklist plus
  req.exclude_domains) and allowlist (req.domains) before calling select;
  selection.py:14-15 lower-cases them again.
- **Problem:** The which-domains-allowed-or-blocked policy is split: part in the pipeline
  (combining sources, pipeline.py:74-77), part in the selector (filtering,
  selection.py:16-21). The Protocol pushes pre-merged sets in, so the selector is not
  the single owner of selection policy. Both layers also lowercase (pipeline.py:76-77
  and selection.py:14-15) - redundant and a latent inconsistency.
- **Impact:** Selection policy is not fully encapsulated behind its seam; the
  request-plus-operator-blocklist merge cannot be unit-tested against the pure selector
  alone - it is pipeline glue, covered only via the fake-backed
  test_exclude_domains_merge_with_blocklist (test_pipeline.py:38-42).
- **Recommendation:** Either (a) move source-merging into the selector (pass req fields
  plus settings blocklist, let select combine, stays pure), or (b) keep it in the
  pipeline but document that selection policy is filter+cap and source resolution is
  glue. Remove the double lowercasing (pick one layer).
- **Acceptance Criteria:** Domain allow/block resolution is owned by one layer and
  unit-testable there without the full pipeline; no double normalization.
- **Regression Tests:** Pure unit test of merge-and-filter in whichever layer owns it,
  covering exclude_domains plus operator blocklist plus allowlist together.

---

### IFACE-4 - Reranker.rerank may return fewer chunks than input; degraded mode returns MORE than success mode
- **Severity:** Medium
- **Category:** Interfaces
- **Evidence:** interfaces.py:34-35; reranker_client.py:30-36 appends only items whose
  index is in range; pipeline.py:101-103 sets chunks_reranked = len(scored); fallback at
  pipeline.py:105 preserves ALL chunks.
- **Problem:** The Protocol is silent on whether rerank must return one ScoredChunk per
  input chunk. The real client can silently drop chunks (any chunk whose index never
  appears in results is lost) or include none (empty results -> empty scored -> empty
  passages, but reranked=True and no reason). A reranker that returns only top-k (a
  legitimate behavior) would silently discard the rest even though they fit the token
  budget. Because the fallback path preserves all chunks, DEGRADED mode can return more
  candidates than SUCCESS mode - an inversion.
- **Impact:** Coverage depends on undocumented reranker behavior; budget-not-count
  (invariant 6) is undermined if the reranker truncates before assembly applies the
  token budget. Leaky abstraction: the pipeline assumes full coverage; the contract does
  not guarantee it.
- **Recommendation:** Document the contract: rerank returns a score for every input chunk
  (reordering allowed, dropping not). In the client, after parsing, back-fill any chunk
  missing from results with a floor score (e.g. 0.0) so assembly sees all candidates and
  the TOKEN BUDGET - not the reranker - decides output. Treat an empty results body
  (status 200) as RerankerUnavailable (degrade) or back-fill, not a silent empty.
- **Acceptance Criteria:** For N input chunks the real client yields N ScoredChunks
  regardless of how many the server scored; an empty results body does not silently zero
  out passages.
- **Regression Tests:** reranker_client test: server scores only 1 of 3 chunks -> all 3
  present (the 2 missing get floor scores); empty results -> degrade or full back-fill.

---

### IFACE-5 - Extractor/Chunker contracts hide partial-failure provenance; one bad page hard-fails the whole chunk batch
- **Severity:** Medium
- **Category:** Interfaces
- **Evidence:** interfaces.py:26-31; chunker_client.py:15-46 loops one POST /chunk per
  page and aborts the batch on any non-200; crawl4ai_client.py:18-38 drops failed URLs
  silently.
- **Problem:**
  1. ContentExtractor.extract(urls) -> list[Page] gives no per-URL success/failure
     signal; the pipeline infers all-failed only when the list is empty
     (pipeline.py:86). Partial crawl failures (invariant 9) are invisible beyond a count
     (urls_crawled_ok). Callers cannot know WHICH URLs failed.
  2. SemanticChunker.chunk(pages) is a batch contract, but ChunkerClient implements it as
     a sequential per-page loop (chunker_client.py:19), and a single page non-200 aborts
     the ENTIRE batch via ChunkerUnavailable (lines 28-29) -> hard-fail 503 for all
     pages. The chunker MAY hard-fail when down (invariant 9), but a single per-page 4xx
     is not chunker-down. Partial tolerance is asymmetric: crawl tolerates, chunk does
     not.
- **Impact:** The batch Protocol masks a sequential, all-or-nothing implementation; one
  page chunking error escalates to a full-call 503.
- **Recommendation:** Distinguish chunker-unreachable / 5xx (-> ChunkerUnavailable,
  hard-fail per invariant) from this-page-failed-to-chunk / 4xx (-> skip the page,
  continue). Keep the batch Protocol. Optionally enrich stats with per-URL crawl/chunk
  outcomes without changing the Protocol return type.
- **Acceptance Criteria:** A single page returning 422 from /chunk is skipped, other
  pages still chunk, call returns 200; only an unreachable/5xx chunker hard-fails.
- **Regression Tests:** chunker_client test (MockTransport) where page 2 of 3 returns 422
  -> chunks from pages 1 and 3 present, no exception; 5xx -> ChunkerUnavailable.

---

### IFACE-6 - Fakes omit failure/degradation coverage for several seams
- **Severity:** Low
- **Category:** Interfaces
- **Evidence:** fakes.py has DownDiscovery, DownChunker, DownReranker, but no
  DownExtractor (crawl is partial-tolerant so down == empty, covered by EmptyExtractor),
  no partial-result fakes, and no FakeSelector/FakeAssembler (IFACE-2).
- **Problem:** Fake coverage is uneven. There is no fake to exercise: a chunker returning
  partial results; a reranker returning the index-less shape (IFACE-1); a crawl returning
  fewer pages than URLs. The deterministic-fake suite does not let a reviewer drive every
  documented failure mode through run_search without network.
- **Impact:** Some failure-posture branches (invariant 9) are unreachable by the
  fake-backed pipeline tests and are effectively unverified.
- **Recommendation:** Round out fakes: add PartialChunker (chunks for some pages, none for
  others) and PartialExtractor (fewer pages than URLs); ensure a Down/Empty/Partial fake
  exists for the seams whose failure mode the invariants describe.
- **Acceptance Criteria:** Every failure mode in arch-map section 8 is reachable through a
  deterministic fake with no network.
- **Regression Tests:** Pipeline tests using the new fakes asserting the documented
  reason/degradation for each.

---

### IFACE-7 - ResultAssembler Protocol leaks the Pydantic WIRE models into the internal seam layer
- **Severity:** Low
- **Category:** Interfaces
- **Evidence:** interfaces.py:4-5 imports Citation, Passage (Pydantic wire models,
  models.py) into ResultAssembler; the other six Protocols use only internal dataclasses
  (types.py).
- **Problem:** Six of seven seams speak only internal dataclasses (DiscoveryResult, Page,
  Chunk, ScoredChunk). The assembler return type leaks the WIRE models (Passage,
  Citation) into the internal seam layer - a layering inversion. If the wire schema is
  versioned (invariant 10), the pure assembler signature changes too, even though
  assembly is conceptually internal.
- **Impact:** The most policy-critical pure stage is bound to the public wire contract;
  versioning the response schema forces touching the pure stage and its Protocol.
- **Recommendation:** Have ResultAssembler return internal dataclasses (e.g.
  AssembledPassage/AssembledCitation in types.py) and let run_search (or a thin mapper)
  project them onto the wire Passage/Citation. Keeps the pure stage independent of
  Pydantic and lets the wire schema version without disturbing policy code. Low priority
  (works today) but removes a real layering leak.
- **Acceptance Criteria:** The ResultAssembler Protocol and assembly.py import nothing
  from models.py; wire projection happens at the pipeline/surface boundary.
- **Regression Tests:** assembly unit tests assert on internal types; a pipeline test
  asserts the projection to wire models is faithful (ids, urls, token_count preserved).

---

## Invariants - verification summary
- (1) No persistent index / vector DB in hot path: UPHELD. No store anywhere in
  orchestrator/; pages chunked, reranked, discarded per call.
- (2) SearXNG opaque: UPHELD. searxng_client.py only issues GET /search; no patching.
- (3) BYO seams as env-selected HTTP endpoints: UPHELD. build_deps_from_settings wires
  from Settings; no model server hardcoded.
- (4) Stateless per call; cache injected-not-owned: UPHELD by absence - no cache is
  implemented (dead CACHE_BACKEND/REDIS_URL settings; arch-map D1). When added it must be
  injected into PipelineDeps, never owned by pipeline logic.
- (5) Evidence not prose; no hidden LLM hop: UPHELD. The only LLM call is the optional
  query planner (discovery device); rerank scores the ORIGINAL query (pipeline.py:101).
- (6) Budget not count: UPHELD in assembly (greedy by score under token_budget,
  assembly.py:19-40); max_passages is correctly a secondary cap. NOTE: IFACE-4 can
  undermine this if a reranker truncates upstream.
- (7) Selection gate protects crawl: PARTIAL - count cap works; quality gate no-ops on
  zero-score results (PIPE-2).
- (8) REST/MCP twins: VIOLATED on the MCP surface (PIPE-1).
- (9) Partial failure never stalls; only SearXNG/chunker hard-fail: MOSTLY upheld;
  IFACE-1 (reranker parse crash) and IFACE-5 (single-page chunk 4xx escalates) are
  exceptions to fix.
- (10) Do not break wire contracts; preserve stats/reason/reranked: REST upheld; MCP
  drops stats/reason/reranked (PIPE-1); reason codes conflated (PIPE-3).
