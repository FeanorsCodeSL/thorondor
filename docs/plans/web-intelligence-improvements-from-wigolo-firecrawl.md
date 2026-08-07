# Web Intelligence Improvements from Wigolo and Firecrawl

## Goal

Improve Thorondor's evidence integrity, fetch visibility, site mapping, bounded crawling, optional caching, and long-running job support using independently designed lessons from the local Wigolo and Firecrawl clones.

Preserve Thorondor's SearXNG and Crawl4AI service boundaries, private service APIs, layered SSRF protection, robots policy, external-web provenance, untrusted-content labels, and backward-compatible `thorondor.search.v1` response. Default search must remain live and non-persistent.

Wigolo and Firecrawl are AGPL-3.0 reference material only. Do not copy or adapt their source, tests, assets, schemas, text, or implementation files into Thorondor. Re-derive behavior from Thorondor's requirements and write first-party Python implementations and tests.

## References

Thorondor:

- `README.md:5-29` — current synchronous search pipeline, internal service boundaries, and non-persistent posture.
- `README.md:551-598` — URL filtering plus the independent DNS-resolving egress proxy.
- `orchestrator/pipeline.py:123-157` — parallel subquery discovery and current degradation handling.
- `orchestrator/pipeline.py:320-402` — synchronous search flow and terminal reason codes.
- `orchestrator/clients/searxng_client.py:29-83` — one SearXNG request per subquery; the client currently keeps only singular `engine`, score, and reported failures even though the pinned JSON contract can expose plural engines, positions, and publication date. True per-engine latency is not in the response.
- `orchestrator/clients/crawl4ai_client.py:49-75,124-218` — per-call rather than process-wide crawl limits, a connection pool shared with redirect preflight, a batch-wide timeout, an unproxied target-site `HEAD`, and loss of final URL, status, headers, links, and metadata that pinned Crawl4AI already returns.
- `orchestrator/selection.py:70-84,107-113,191` — filtered decisions are recorded before selections and the combined list is truncated at 50.
- `orchestrator/clients/reranker_client.py:32-35,47-93` and `orchestrator/app.py:54-58` — per-request rerank telemetry is mutable state on one process-wide client.
- `orchestrator/markdown_cleaner.py:33-55` — no Markdown fallback when HTML extraction yields nothing and `markdown_blocks_dropped` is always zero.
- `orchestrator/clients/chunker_client.py:13-68` and `semantic-chunking-service/chunking/models.py:7-22` — sequential per-page chunk calls, a hardcoded timeout, silent 4xx drops, and the 200,000-character request cap.
- `semantic-chunking-service/chunking/cluster_semantic.py:325-351,555-576,695-721` — segment offsets can be approximated, chunk text is reconstructed rather than sliced, and boundaries depend on embedding output.
- `orchestrator/content_dedup.py:10-23` — distinct pages can be dropped solely because their first 2,000 normalized characters match.
- `orchestrator/url_safety.py:61-70` and `ssrf-proxy/proxy.py:181` — orchestrator DNS resolution blocks the event loop while the proxy already offloads resolution.
- `docker-compose.yml:3-30,112-169` — the orchestrator and Crawl4AI both have direct egress; proxy variables on Crawl4AI are convention rather than a topology-enforced target-site path.
- `orchestrator/models.py:23-114` — current search wire contract and compatibility surface.
- `orchestrator/types.py:26-84` — current page, chunk, passage, and citation types.
- `orchestrator/normalize.py:25-39` — current URL normalization used by discovery deduplication and citation assembly.
- `semantic-chunking-service/chunking/models.py:29-35` — chunk responses already contain character start and end indexes.
- `semantic-chunking-service/chunking/app.py:62-88` — indexes are calculated after chunk-service pre-cleaning.
- `semantic-chunking-service/chunking/textprep.py:9-14` — pre-cleaning changes the text, so existing indexes are not automatically offsets into the orchestrator's cleaned Markdown.
- `orchestrator/clients/chunker_client.py:48-63` — the orchestrator currently discards the returned start and end indexes.
- `orchestrator/tests/test_models.py:88-91` — exact JSON-schema equality is already pinned; compatibility checks must catch accidental breaking changes when the golden file is intentionally updated.
- `thorondor_cli/mcp_proxy.py:16-18,21-101` — the stdio MCP surface duplicates request parameters, assumes an optional bearer token, hardcodes a 180-second timeout, and passes response JSON through unchanged.
- `orchestrator/settings.py:11-22` and `orchestrator/observability.py:50-94` — missing configuration keys fail startup and structured logs silently drop fields outside a fixed allowlist.

Wigolo snapshot `b3ccf92be3ac15ce3ad5a439be11777d992996a3`:

- `/home/sergio/git/feanors-code/wigolo/src/search/evidence.ts:80-190` — evidence excerpts, aggregate output budgets, source spans, and stable identifiers.
- `/home/sergio/git/feanors-code/wigolo/src/search/evidence.ts:22-38` and `/home/sergio/git/feanors-code/wigolo/src/search/highlights.ts:35-42,87-106` — structural evidence filtering and heading lookup from source positions.
- `/home/sergio/git/feanors-code/wigolo/src/search/core/freshness.ts:1-63` — publication-date signals separated from inference confidence.
- `/home/sergio/git/feanors-code/wigolo/src/search/core/engine-base.ts:31-109` — engine outcomes, soft deadlines, retries, throttling, and circuit-breaker state.
- `/home/sergio/git/feanors-code/wigolo/src/cache/store.ts:31-316` — URL normalization, content hashes, status, timestamps, TTLs, and stale windows.
- `/home/sergio/git/feanors-code/wigolo/src/crawl/etag-incremental.ts:7-118` — conditional requests using `ETag` and `Last-Modified`.
- `/home/sergio/git/feanors-code/wigolo/src/cache/change-detector.ts:12-79` — content-hash and HTTP-status change detection.
- `/home/sergio/git/feanors-code/wigolo/src/cache/diff-engine.ts:7-25` — explicit complexity caps before quadratic diff work.
- `/home/sergio/git/feanors-code/wigolo/src/crawl/mapper.ts:70-206` — sitemap-first mapping with bounded BFS fallback.
- `/home/sergio/git/feanors-code/wigolo/src/crawl/robots.ts:14-73` — a deliberately simple parser whose wildcard-group-only behavior is insufficient as Thorondor's robots contract.
- `/home/sergio/git/feanors-code/wigolo/src/crawl/sitemap.ts:11-75` — sitemap metadata and deterministic last-modified/priority truncation order.
- `/home/sergio/git/feanors-code/wigolo/src/crawl/rate-limiter.ts:58-208` — per-host concurrency, robots delay, jitter, and adaptive 403/429 cooldown.
- `/home/sergio/git/feanors-code/wigolo/src/fetch/politeness.ts:9-49` — bounded parsing of delta-seconds and HTTP-date `Retry-After` values.
- `/home/sergio/git/feanors-code/wigolo/src/extraction/metadata.ts:20-68` and `/home/sergio/git/feanors-code/wigolo/src/extraction/jsonld.ts:7-32` — deterministic metadata, canonical-link, and JSON-LD extraction.
- `/home/sergio/git/feanors-code/wigolo/src/daemon/rest/limits.ts:9-159` — body caps, route deadlines, concurrency slots, and shared clamp metadata.
- `/home/sergio/git/feanors-code/wigolo/src/watch/ssrf.ts:15-18,386-479` — the input guard leaves DNS rebinding out of scope, while fetch-time guards reuse one literal/resolved IP policy and still require connection pinning.

Firecrawl snapshot `0344bc87a64b455d6e06c7d1eb74ba5ebe007b1c`:

- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/types.ts:1128-1234` — crawl/map path policies, sitemap modes, query handling, limits, and strict request schemas.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/map.ts:153-208` — map deadline with cooperative cancellation.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/lib/map-utils.ts:82-180` — multiple map sources and sitemap-only behavior.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/scraper/WebScraper/crawler.ts:414-665` — robots filtering, crawl delay, sitemap admission, and link filtering.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/scraper/scrapeURL/engines/index.ts:552-578` — cache/index bypass when request capabilities make a cached result unsafe.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/scraper/scrapeURL/engines/index.ts:751-867` — capability-based engine selection and explicit unsupported-feature tracking.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/crawl.ts:274-362` — asynchronous crawl admission and kickoff.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/lib/crawl-redis.ts:12-126` — expiring crawl state and crawl-scoped policy data.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/crawl-status.ts:179-403` — ownership checks, terminal states, partial results, byte-bounded pagination, expiry, and warnings.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/crawl-cancel.ts:17-70` — cancellation semantics.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/routes/shared.ts:273-291` — check-then-insert idempotency flow; use as an anti-pattern rather than copying it.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/controllers/v2/types.ts:1128-1140` and `/home/sergio/git/feanors-code/firecrawl/apps/api/src/scraper/WebScraper/crawler.ts:400-526` — configured robots identity, per-link denial reasons, and crawl-delay handling.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/lib/map-utils.ts:124-162` — seed redirect re-homing and a robots fail-open behavior Thorondor must not inherit silently.
- `/home/sergio/git/feanors-code/firecrawl/apps/api/src/lib/crawl-redis.ts:12-126` — per-crawl robots state and sliding-on-read retention that conflicts with Thorondor's bounded persistence posture.

Pinned upstream contracts:

- `unclecode/crawl4ai@0.9.2` `crawl4ai/models.py:130-160` — `CrawlResult` exposes links, metadata, response headers, status, final redirect, and cache state; the hardened Docker API does not allow arbitrary caller headers, so conditional revalidation needs an explicitly capable path.
- `searxng/searxng@c63835bd2` `searx/result_types/_base.py:388-422` and `searx/webutils.py:162-174` — JSON results can expose `publishedDate`, plural `engines`, and `positions` when engines supply them.
- RFC 9309 — robots group selection, longest-match semantics, percent-encoding comparison, redirect/error handling, caching, and parsing-size limits.

## Build & run

- **Containerized:** yes
- **Build command:** `docker compose --env-file .env -f docker-compose.yml --profile bundled-models config`
- **Test command:** `python -m pytest semantic-chunking-service/tests orchestrator/tests -v`

## Second-pass conclusions

Adopt or adapt:

- Evidence must identify an exact version of cleaned source text, not only a URL and an excerpt.
- Evidence should carry trustworthy publication/modification signals with explicit source and confidence; unknown freshness is better than a guessed date.
- Structural evidence-quality checks belong before the output token budget, with benchmarked recall and auditable drop reasons.
- The crawler needs one configured identity used consistently for outbound requests and robots group selection.
- Site discovery should fuse robots-declared sitemaps, sitemap indexes, bounded link traversal, and optionally Thorondor's existing SearXNG `site:` discovery.
- Every expensive operation needs item limits, byte limits, deadlines, concurrency limits, and explicit degradation metadata.
- Fetch routing should be capability-based and must never let a cache hit or cheaper backend silently ignore a requested capability.
- Cache freshness should use conditional HTTP revalidation only on a backend that can actually send validators, and otherwise compare full refetch body hashes and status transitions.
- Long crawls need durable state, partial-result pagination, expiry, cancellation, and restart behavior before webhooks or schedules.

Do not adopt:

- Do not add direct search-engine adapters or per-engine breakers in Thorondor. SearXNG owns engine dispatch; Thorondor can truthfully report subquery latency, engine contribution, and SearXNG-reported failures, but not fabricate per-engine latency.
- Do not use one aggressive URL normalizer for safety, fetching, display, deduplication, and cache identity. In particular, do not collapse `www` and apex hosts by default.
- Do not expose caller-provided regular expressions initially. Use bounded glob/path rules so pathological patterns cannot consume the request deadline.
- Do not implement a deadline that merely returns early while work continues. Cancellation must propagate to pending discovery, fetch, chunk, rerank, and crawl tasks.
- Do not use check-then-insert idempotency. Claim an idempotency key atomically together with a request fingerprint and replay the existing job only for the same request.
- Do not refresh job or result retention on read. Retention is absolute from the terminal-state transition.
- Do not default schemeless input to `http://`, treat sitemap `lastmod` as a publication date, or trust a page-declared canonical URL for safety or cross-origin deduplication.
- Do not copy the incomplete robots parsers or fail-open defaults in either reference. Implement and test Thorondor's RFC 9309 policy independently.
- Do not reproduce stealth, challenge-evasion, hosted billing, multi-tenant, cloud-index, or large queue infrastructure.
- Do not assume the current redirect preflight is proxy-enforced. It is issued by the orchestrator, whose Compose service has direct egress and no proxy variables; close that gap before adding new target-site request paths.

## Cross-cutting acceptance criteria

- Existing `/search`, `/v1/search`, in-process MCP `web_search`, and stdio-proxy callers remain compatible; optional additive response fields are allowed within `thorondor.search.v1`, while breaking changes require a new schema version.
- REST, in-process MCP, and `thorondor_cli/mcp_proxy.py` use the same request models, limits, timeout policy, and capability descriptions. The stdio proxy may pass additive response JSON through, but its duplicated request signature must not drift.
- URL safety runs on the original seed, every discovered URL, every redirect hop, and the final URL. Every target-site connection must additionally be DNS-pinned by the egress proxy or an equivalently reviewed connector; hostname validation alone is insufficient.
- External content, metadata, structured data, and errors are treated as untrusted and are bounded before logging or returning.
- Search remains non-persistent by default. Cache, job storage, and raw HTML each require explicit operator enablement.
- Each new configuration key updates `orchestrator/settings.py`, Compose, every tracked environment template, and the upgrade documentation in the same phase; missing keys currently fail startup.
- Each new telemetry field updates the structured-log allowlist. Logs redact URL query/fragment data; caller responses may contain bounded full URLs, but logs and responses must not expose request headers, cookies, arbitrary response headers, or bodies outside explicit content fields.
- Each phase updates architecture, configuration, security, API, and dependency/license documentation affected by that phase.
- Every benchmark records fixture revision, configuration, latency, request count, success/degradation reasons, and output-quality metrics so routing choices are reproducible.

## Phase 0 — Baseline contracts and architecture decisions
**Status:** completed
**Kind:** logic

### Tasks

- [x] Capture the current `thorondor.search.v1` JSON schema, deterministic fixture responses, request counts, and stage timings as the compatibility and performance baseline.
- [x] Define separate URL concepts: `requested_url`, `final_url`, `display_url`, `safety_target`, conservative `dedup_key`, and untrusted advisory `declared_canonical_url`. A declared canonical never becomes a safety target, is never auto-followed, and is ignored for cross-origin deduplication. Document the exact tracking-parameter set removed by `dedup_key`; do not silently expand the current `utm_*` policy. Cache identity adds its own retrieval/extraction variant instead of reusing the display URL.
- [x] Define `document_id = hash(final_url, sha256(full_exact_cleaned_markdown))`. Keep cleaner version, retrieval timestamp, status, content type, and retrieval method as provenance rather than inputs that make the ID change on every fetch. The current 2,000-character prefix fingerprint may identify a near-duplicate candidate but must not by itself discard a page.
- [x] Capture the fields the current Crawl4AI client discards—final URL, status, content type, allowlisted validators, links, and bounded metadata—on the internal page/fetch boundary before evidence IDs are implemented. Do not retain or return arbitrary response headers.
- [x] Define closed reason-code enums for fetch, map, crawl, cache, and job outcomes. Human-readable messages remain secondary and must not drive control flow.
- [x] Record the access decision for new endpoints: keep them internal-only behind the existing operator-controlled proxy, or add first-party authentication before broader exposure. Do not silently broaden the current network trust boundary.
- [x] Choose and implement the target-site egress design before adding fetch paths: route redirect checks through the existing proxy, replace them with a resolver-pinned connector, or remove the duplicate `HEAD` in favor of proxy-enforced fetching and final-target validation. Make URL-safety DNS resolution non-blocking. Do not rely on proxy environment variables while Crawl4AI retains a direct egress route.
- [x] Record the network role of each service. Enforce proxy-only target-site egress for Crawl4AI; separately document why SearXNG or configured model providers need provider-plane egress instead of applying one blanket network rule.
- [x] Define one operator-configured crawler identity: an outbound `User-Agent` with contact URL and its robots user-agent token. The identity must be used by direct requests, robots/sitemap fetches, and Crawl4AI-supported fetch configuration.
- [x] Build a deterministic local fixture corpus covering static HTML, a JavaScript shell, redirects, a challenge shell, robots groups/error states, sitemap indexes, duplicate URLs with shared long preambles, canonical conflicts, query variants, Unicode/CRLF Markdown, PDF/document content, status/validator transitions, SearXNG plural-engine/date fields, and malformed upstream payloads.
- [x] Record implementation gates: Phase 1A depends on final-URL/source-document capture; Phases 2 and 3 are blocked by the target-site egress decision; Phase 3 depends on the Phase 2 fetch outcome contract; Phase 4 conditional revalidation depends on a backend that can send request validators; Phase 5 depends on bounded crawl plus an approved persistence mode.

### Implementation report — 2026-08-06

- Added first-party URL/cache/document identity contracts and closed internal outcome enums without changing the public REST/MCP schema.
- Added bounded Crawl4AI provenance capture, removed the duplicate target-site `HEAD`, moved DNS resolution off the event loop, revalidated changed final URLs, and configured a stable crawler identity.
- Enforced Crawl4AI internal-only networking while preserving separate egress roles for the proxy, SearXNG, and configured model providers; the release guard now validates rendered local, CLI, and production topology.
- Added the additive-safe v1 compatibility floor, 17-artifact checksum-sealed fixture corpus, raw upstream contract tests, and a fixture-only baseline report that does not invent live metrics.
- Updated settings, Compose assets, environment templates, CLI configuration surfaces, dependency declarations, and affected architecture/security/deployment documentation.
- Preliminary implementation checks: 219 focused tests passed; Bash release guard passed; fixture transfer byte-matched its reviewed source; Python compilation and `git diff --check` passed. Full verification remains below.

### Verification

- [x] Add a checked-in baseline report for the fixture corpus with current coverage, latency, request count, duplicate rate, and terminal reasons. Where the current implementation exposes only aggregate counters, record that limitation explicitly; Phase 2 owns per-URL cause attribution.
- [x] Preserve the exact schema snapshot test and add a monotonic compatibility assertion that independently proves old requests still validate and old required response fields, types, and enum values remain valid after an intentional golden update.
- [x] Add policy tests showing that safety targets, display URLs, dedup keys, and cache identities remain distinct for redirects, `www` versus apex hosts, fragments, tracking parameters, repeated query keys, and Unicode hosts.
- [x] Add contract fixtures proving the pinned Crawl4AI and SearXNG fields Thorondor intends to consume, with fixture-level contract checks that fail closed to explicit `unsupported_metadata`; Phase 2 owns runtime per-URL diagnostics.
- [x] Add Compose/network checks proving Crawl4AI cannot reach target sites except through the reviewed egress path and that `/livez` remains process-only while DNS resolution is slow.

### Verification report — 2026-08-06

- Independent review found and corrected two documentation defects: stale pre-integration wording in the URL identity contract and Phase 0 verification language that duplicated Phase 2 runtime outcome work. No runtime defect remained after review.
- Full isolated repository suite: 349 passed. Focused post-review Phase 0 re-verification: 170 passed.
- The documented bundled-models Compose render completed, and the rendered-topology checker proved Crawl4AI is internal-only, all proxy variables target the egress proxy, and SearXNG plus bundled model providers retain provider-plane egress.
- `bash scripts/check-release-guard.sh` passed, including local, CLI, and production rendered-topology checks. PowerShell execution was unavailable because `pwsh` is not installed on this host.
- `git diff --check` passed, and a stale-setting/documentation scan found no remaining preflight redirect configuration or claims outside the historical plan/review artifacts.
- The repository's literal `python -m pytest ...` command is unavailable on this host because `python` and system `pytest` are absent; verification used an isolated `uv` environment with both locked development requirement sets.
- The local `.env` predates the new required crawler identity keys, so Compose emits blank-variable warnings for them. The tracked example and CLI template values are complete and release-guard verified; the operator must synchronize the local secret-bearing `.env` before starting the stack.

### Acceptance — 2026-08-06

- Accepted by the user; Phase 1A authorized to proceed directly.

## Phase 1A — Addressable and fresh evidence
**Status:** completed
**Kind:** logic

### Tasks

- [x] Add an offset-preserving chunk mode for already-cleaned orchestrator Markdown. It must skip destructive pre-cleaning and return each chunk's `text` as the verbatim slice `document[start_index:end_index]`; re-joined segment text and `str.find` fallback positions are not valid span identity.
- [x] Carry Unicode code-point, end-exclusive `start_index` and `end_index` through `Chunk`, assembled passages, citations, REST, in-process MCP, and stdio MCP. If a fallback cannot produce an exact slice, mark it `verbatim=false` and do not emit an `evidence_id`.
- [x] Emit chunking-independent `document_id` plus `evidence_id = hash(final_url, cleaned_document_sha256, start_index, end_index)`. Keep integer `citation_id` for compatibility and report cleaner/chunker strategy separately; evidence-ID stability is conditional on identical document bytes and span boundaries, not on result order or embedding backend.
- [x] Attach the nearest preceding section heading to every exact evidence span. The heading is navigation metadata and does not alter span identity.
- [x] Add deterministic, bounded metadata extraction needed for evidence: title/description, page and JSON-LD publication/modification timestamps, author, language, and advisory canonical URL. Preserve field-level source and conflicts instead of silently choosing an untraceable value.
- [x] Populate the existing `Citation.published` only from a valid publication-date source and add optional source/confidence metadata. Keep `modified_at` separate; SearXNG `publishedDate`, page metadata, JSON-LD `datePublished`, and sitemap `lastmod` must not be conflated, and body prose must not be date-mined in this phase.

### Verification

- [x] Add tests proving `cleaned_markdown[start:end] == chunk.text` for exact chunks across ASCII, Unicode, combining characters, CRLF input, image-only lines, repeated paragraphs, multi-paragraph groups, fallback chunking, and truncated passages.
- [x] Add tests proving exact spans survive REST/in-process-MCP/stdio-MCP serialization and that non-verbatim chunks cannot receive evidence IDs.
- [x] Add tests for stable document IDs across chunk ordering/backend changes, stable evidence IDs across result ordering, and changed evidence IDs when document bytes or span boundaries change.
- [x] Add timestamp tests for timezone normalization, malformed values, conflicting metadata/JSON-LD/discovery dates, publication versus modification, missing values, and the rule that sitemap `lastmod` never populates `published`.
- [x] Update the golden response schema only for reviewed additive fields and run the monotonic compatibility assertion from Phase 0.

### Implementation report — 2026-08-06

- The semantic chunking service now has an orchestrator-Markdown mode that preserves the complete cleaned source and emits exact Unicode code-point spans through semantic, greedy, and embedding-failure paths. The orchestrator independently verifies every returned slice before assigning evidence identity.
- Stable document and evidence identities, nearest-section headings, and exact-span fields now propagate through internal types, assembly, citations, raw cleaned Markdown, REST, in-process MCP, streamable HTTP MCP, and stdio MCP. Existing integer citation IDs and v1 response fields remain compatible.
- Deterministic bounded metadata extraction now preserves selected title, description, publication/modification time, author, language, and advisory canonical URL with source, confidence, and conflicts. Relative canonicals resolve against the effective page URL; sitemap dates remain modification-only.
- The response schema, README, architecture, workflow, and MCP descriptions document the additive evidence contract and its fail-closed `verbatim=false` behavior.

### Verification report — 2026-08-06

- The separate direct review found and fixed three integration defects: discovery publication dates were dropped during URL merging, a chunk beginning exactly at a Markdown heading did not inherit that heading, and relative declared canonical URLs were discarded. It also corrected stale architecture wording. No unresolved finding remained in that pass; the external follow-up below identified additional adversarial cases.
- Focused post-review verification: 163 passed, including the real stdio MCP client/server transport and the Phase 0 monotonic compatibility assertion.
- Full isolated repository suite: 378 passed.
- `bash scripts/check-release-guard.sh` passed, and `git diff --check` passed.
- A focused `F`, `B`, and async static check found no new issue. It still reports the pre-existing exception-chaining warning at `semantic-chunking-service/chunking/app.py:60`, which this phase did not alter.
- No live stack was started. The repository's literal `python` command remains unavailable on this host, so tests used the same isolated `uv` environment and locked development requirements recorded in Phase 0.
- Phase 1A remained `in_progress` through the external review and remediation recorded below.

### External Opus review and remediation — 2026-08-06

- Claude Code 2.1.222 ran a read-only `claude-opus-5` review at `xhigh` effort against the complete uncommitted Phase 0 and Phase 1A tree and this plan. It did not modify, stage, commit, build, test, deploy, or start services.
- Independent verification confirmed both acceptance blockers after correcting one detail in the report: 2,000 nested JSON arrays decode on this Python build, but 30,000 levels still fit beneath the 65,536-character JSON-LD input bound and raise `RecursionError`. Deep JSON-LD now fails closed, and canonical URLs are rejected if either their declared or resolved UTF-8 form exceeds the wire limit.
- Confirmed follow-up defects were fixed: all-page chunker 4xx rejection is now an explicit dependency failure; an offset-invariant failure degrades to non-verbatim chunks; exact token counts come from emitted slices; whitespace-only evidence is discarded; headings are precomputed once and fenced-code headings are ignored; truncation without complete identity inputs degrades safely; lowercase proxy variables and `NO_PROXY` bypasses are release-guarded; and the CLI MCP description plus upgrade instructions match the additive contract.
- Missing exact-path coverage was added for the single-segment and OOM-guard greedy-semantic branches. Timing-sensitive URL-safety tests now use synchronization primitives, and the older global crawl-concurrency test now forces real overlap instead of using a synchronous false-positive mock.
- The reported IDNA rejection has no production caller and remains a future wiring constraint for Phase 1B/2. The thin upstream capability fixtures meet Phase 0's explicit fixture-level scope; runtime typed diagnostics remain assigned to Phase 2. The remaining telemetry, raw-output bounding, full-document deduplication, request-owned counters, cancellation, and fetch-routing observations map to already-pending Phase 1B/2 tasks and were not pulled forward.
- Focused post-fix verification: 106 passed. CLI MCP verification under Python 3.13: 4 passed.
- Final full service/orchestrator suite: 389 passed. Full Python 3.13 CLI suite: 75 passed.
- `bash scripts/check-release-guard.sh`, focused `F`/`B`/async static checks, and `git diff --check` passed. Nothing is staged, committed, or pushed.
- Phase 1A has no unresolved acceptance blocker after the external-review remediation.

### Acceptance — 2026-08-06

- Accepted by the user after the external Opus review, independent claim verification, remediation, and final green verification.

### Live deployment verification — 2026-08-06

- Tengwar's canonical `just thorondor-deploy` workflow rebuilt the current first-party images, recreated Thorondor in place without removing volumes or the shared network, and reported all readiness dependencies healthy on `tengwar-shared`.
- The first live `curl` exposed a deployment blocker outside Phase 1A: the recently selected Crawl4AI 0.9.2 image installs an in-container DNS-pinning proxy that cannot operate on Thorondor's proxy-only internal network. All selected public targets failed before extraction.
- The managed Crawl4AI image was restored to the previously pinned 0.8.9 digest. `CRAWL4AI_ALLOW_INTERNAL_URLS=true` now disables only Crawl4AI's duplicate DNS precheck; the orchestrator URL gate, final-URL validation, internal-only network, and first-party egress proxy remain the enforced target-site boundary. The Compose release guard tests this invariant.
- After a clean canonical redeploy with no temporary override, `/healthz` reported SearXNG, Crawl4AI, chunker, embedding, and reranker healthy. A live `curl` search returned HTTP 200 in 6.77 seconds with 20 URLs discovered, 4 selected, 2 crawled successfully, 2 failed, 5 chunks embedded and reranked, and 4 passages from 2 citations. Every returned passage was verbatim and carried a document ID, evidence ID, exact start/end span, and section heading.
- Focused Compose-egress verification passed 4 tests, the full service/orchestrator suite passed 390 tests, and the Python 3.13 CLI suite passed 75 tests. `bash scripts/check-release-guard.sh` and `git diff --check` passed. The compatibility correction remains uncommitted pending review and user acceptance.

## Phase 1A.1 — Crawl4AI 0.9.2 egress migration
**Status:** completed
**Kind:** logic

### Tasks

- [x] Restore the latest verified Crawl4AI 0.9.2 digest and retain authenticated internal API access through `CRAWL4AI_API_TOKEN`.
- [x] Give Crawl4AI outbound internet routing while isolating its API on a dedicated internal control network shared only with the orchestrator. Do not publish the Crawl4AI API port.
- [x] Remove Crawl4AI's obsolete external proxy variables and set `CRAWL4AI_ALLOW_INTERNAL_URLS=false` so the upstream DNS-pinning proxy remains authoritative for every browser target connection.
- [x] Replace the Redis implementation-detail health probe with the public unauthenticated `/health` contract and mirror the upstream non-root, read-only, capability-drop, no-new-privileges, PID-limit, and writable-tmpfs container posture.
- [x] Rewrite the Compose release guard around the new ownership boundary: one isolated internal control network, exactly one dedicated default-gateway egress network, no proxy override variables, internal destinations disallowed, no published Crawl4AI ports, and a digest-pinned upstream image.
- [x] Keep the first-party egress proxy temporarily available for existing deployment compatibility, but remove Crawl4AI's dependency on it. Remove the proxy service and image pipeline only in a separately reviewed cleanup after all deployment consumers are confirmed absent.
- [x] Update local, production, and CLI Compose assets, environment templates, dependency inventory, architecture, security, deployment, and operator documentation together.

### Implementation report

- Updated all three managed Compose variants and all four environment templates to the 0.9.2 contract. Crawl4AI now has one isolated control network and one project-managed outbound network, with no published port or external proxy variables.
- Mirrored the upstream container hardening and public `/health` probe, restored the verified multi-architecture image digest, and kept `CRAWL4AI_ALLOW_INTERNAL_URLS=false`.
- Replaced the former proxy-pass-through release guard with topology, explicit default-gateway, authentication-configuration, image-pinning, health-contract, and hardening checks. Added focused positive and negative tests for each invariant.
- Reconciled operator, deployment, dependency, workflow, URL-safety, and security documentation. The historical 0.8.9 deployment record above is retained as evidence of why this migration is required.
- Implementation and verification are complete.

### Verification

- [x] Add focused tests for the accepted topology and for failures caused by missing direct egress, shared control networks, `CRAWL4AI_ALLOW_INTERNAL_URLS=true`, proxy environment variables, published API ports, missing hardening, and unauthenticated Redis health checks.
- [x] Render local, production, CLI, and host-endpoint-overlay Compose configurations and pass the release guard.
- [x] Run the full service/orchestrator and CLI suites plus `git diff --check`.
- [x] In a non-production compatibility deployment, prove authenticated `/crawl` succeeds for representative public static and JavaScript pages, unsafe internal/link-local targets are rejected, `/healthz` is ready, and a complete Thorondor `curl` search still uses embedding and reranking.

### Verification report — 2026-08-06

- Separate review found that merely attaching both networks left default-route selection implicit. A new failing test reproduced the gap; all Compose variants now select the dedicated crawl egress with `gw_priority: 1`, and the release guard enforces it.
- Focused topology verification passed 18 tests. The final full service/orchestrator suite passed 404 tests, the CLI suite passed 75 tests, the release guard passed all rendered variants, and `git diff --check` passed.
- Tengwar's canonical deploy wrapper stopped exporting obsolete Crawl4AI proxy variables, rebuilt the current first-party images, recreated only the Thorondor Compose project, and reported all dependencies healthy without removing Tengwar's shared network or model services.
- Runtime inspection confirmed the 0.9.2 digest, `appuser`, read-only root filesystem, dropped capabilities, no-new-privileges, PID limit, isolated control network, and outbound network with gateway priority 1. Crawl4AI logged its localhost egress pinning proxy at startup.
- Unauthenticated `GET /health` returned 200. Authenticated `/crawl` returned 200 with extracted markdown for `https://example.com` and the JavaScript-rendered `https://quotes.toscrape.com/js/`; a link-local metadata URL returned 400.
- The final end-to-end `curl` search discovered 35 URLs, selected and crawled 4, produced and reranked 28 chunks using Tengwar's existing embedding and reranking services, and returned 4 passages across 3 citations with no embedding degradation. An earlier release-note query selected anti-bot or empty-content pages and correctly returned no passages; the direct probes and successful documentation query isolated that outcome to the selected targets rather than egress failure.

## Phase 1B — Honest diagnostics and evidence quality
**Status:** completed
**Kind:** logic

### Tasks

- [x] Preserve discovery contributors through URL merging: contributing subqueries, plural SearXNG engines, positions, best upstream score, contribution count, lexical selection score, and selection reason. Distinguish duplicate reports within one subquery from agreement across independent subqueries.
- [x] Add bounded score components with explicit strategy/version labels. Keep the existing scalar passage score as the final reranker or fallback score and never report a component the pipeline did not calculate. Evaluate reciprocal-rank fusion only behind the Phase 0 quality benchmark; do not replace ranking because a reference has RRF.
- [x] Add per-subquery SearXNG attempt latency and result count, per-engine contribution counts from returned plural engines, and SearXNG-reported failures. Document that true per-engine latency is unavailable through the current response.
- [x] Make URL-diagnostic truncation deterministic and selection-aware: retain selected decisions first, fill the remaining item/byte budget with filtered decisions, and report omitted counts by reason.
- [x] Move reranker and all other per-request telemetry out of mutable process-global client fields into request-owned result objects; concurrent requests must not overwrite one another's diagnostics.
- [x] Audit existing `SearchStats` for values the pipeline never computes. Populate or deprecate `markdown_blocks_dropped` in place; removing the v1 field is a breaking change.
- [x] Replace the 2,000-character prefix drop rule with full-document identity for exact duplicates. Any optional near-duplicate heuristic must be a separately measured candidate signal with collision diagnostics, not an unconditional deletion rule.
- [x] Add a deterministic evidence-quality gate before assembly for clear navigation/boilerplate and link-dominated fragments. Do not blanket-reject code, tables, lists, or short factual evidence; benchmark precision and recall, version the policy, and return bounded drop counts by rule.
- [x] Bound evidence, raw Markdown, per-URL outcomes, diagnostics, and telemetry independently by items and bytes so optional diagnostics cannot defeat the caller's output budget.

### Verification

- [x] Add tests for duplicate contributors within/across subqueries, plural engines/positions, partial subquery failure, fallback scoring, optional RRF comparison, component/scalar agreement, and deterministic ordering.
- [x] Add concurrent-search tests with interleaved reranker and fetch awaits proving each response reports only its own counters and no limiter/telemetry state leaks.
- [x] Add tests where more than 50 filtered URLs precede selected URLs and prove selected diagnostics remain visible while omission counts are accurate.
- [x] Add dedup fixtures for exact duplicates, long shared preambles with different bodies, canonical disagreements, and normalized whitespace.
- [x] Add quality-gate fixtures for navigation, footer/link farms, concise factual sentences, code, lists, and tables, then publish precision/recall and answer-quality deltas before enabling the gate by default.

### Implementation report — verified

- Discovery merging now retains distinct sub-query/engine/position contributions, preserves the best upstream score without a corroboration boost, and exposes selected-first bounded diagnostics with omission counts.
- SearXNG attempts now fail independently and report bounded per-sub-query latency/result counts, plural-engine contribution totals, and reported engine failures. Malformed 200 responses map to a closed failure reason, valid sibling attempts survive, internal transport URLs are not exposed, invalid plural positions remain aligned as unknown, and true per-engine latency remains explicitly unavailable.
- Reranker scores and batch counters now travel in one request-owned result. Passage score components identify the external reranker, calculated partial floor, or position fallback without changing the scalar score contract.
- Exact page deduplication now hashes the full whitespace-normalized document. The Markdown cleaner populates `markdown_blocks_dropped` with an exact-line multiset difference rather than a clamped cross-renderer line-count delta.
- `evidence-quality@1` uses narrow structural rules, preserves short policy answers and curated link lists, and is operator-controlled with a disabled default until broader independent measurement. Evidence, URL diagnostics, and optional raw Markdown have independent serialized item/list byte envelopes and omission counters; oversized top-ranked evidence is identity-safely shortened instead of discarded.
- `docs/benchmarks/web-intelligence-phase1b-quality.md` records fixture revision/configuration, elapsed time, zero network requests, degradation reasons, 1.00 precision/recall, and zero useful-evidence/answer-coverage loss. Its synthetic 0.75/0.75 RRF comparison is explicitly not authorization to change production ranking, so RRF remains disabled.
- A read-only Claude Opus review was checked claim-by-claim. Verified corrections also preserve 500-character non-ASCII subqueries, validate the explicit 1–8 sub-query limit, retain the v1 response field acceptance floor, synchronize MCP descriptions and aggregate logs, and route diagnostics-less selectors through the same bounded envelope.
- Verification passed with 518/518 offline tests, the Phase 1B fixture benchmark, `git diff --check`, and `scripts/check-release-guard.sh`.
- Tengwar deployment rebuilt orchestrator image `sha256:701de5836617a00439a48c4ae0a489f4915b5b1b668c71db6d612ed33796f2e3` on `tengwar-shared`. Live `/healthz` reported every dependency healthy; a real `curl` to `/v1/search` returned HTTP 200 with five verbatim passages, exact citation spans, contributor/selection diagnostics, `reranked=true`, one successful reranker batch, and `embedding_degraded=false`.

## Phase 2 — Resource envelope and fetch outcome routing
**Status:** completed
**Kind:** logic

### Tasks

- [x] Add one shared transport policy for request-body bytes, total response bytes, per-route and per-stage deadlines, process-wide in-flight searches/fetches, per-host work, subqueries, URLs, raw-content size, and internal fan-out. Replace per-call crawl semaphores with process-owned limiters and give sequential chunking bounded concurrency plus a configurable stage budget. Enforcement, settings validation, OpenAPI metadata, and MCP descriptions must read from the same definitions.
- [x] Return `413` for oversized bodies, `429` with bounded `Retry-After` when no request slot is available, and a closed timeout error when the route deadline expires. Keep `/livez` process-only. Thread a cancellation scope through REST, in-process MCP, stdio MCP, `run_search`, and every client so timeout or disconnect stops pending work and records why.
- [x] Introduce typed stage outcomes with requested/final URL, status, content type, title, links, retrieval method, elapsed time, and closed reasons including content, empty shell, challenge, robots refusal, upstream timeout, orchestrator deadline cancellation, unsafe redirect, unsupported content/capability, content too large, extraction empty, malformed upstream response, and upstream failure.
- [x] Wrap the current Crawl4AI backend first. Preserve its browser-capable role; capture its status, final URL, links, metadata, and allowlisted `ETag`/`Last-Modified`/content headers; map every response/failure into the typed outcome; and never return or log arbitrary headers.
- [x] Complete the Phase 0 redirect/egress design. Preserve per-hop and final URL checks, but remove any duplicate target-site request that bypasses connect-time DNS pinning.
- [x] Define backend capabilities such as HTML, JavaScript rendering, PDF/document, links, metadata, and raw HTML. Routing must either choose a capable backend or return `unsupported_capability`; it must not silently drop requested behavior.
- [x] Apply the configured crawler identity consistently through Crawl4AI and every direct target-site request. Treat identity as honest attribution, not a mechanism for rotating or impersonating browsers.
- [x] Detect empty/SPA, challenge, extraction-empty, and oversize outcomes conservatively. Fall back from failed HTML extraction to the already-returned Crawl4AI Markdown before classifying the document empty; never cache, chunk, or return challenge/error shells as evidence.
- [x] Return bounded per-URL terminal outcomes and aggregate counts so an agent can distinguish retryable timeouts/rate limits from robots refusal, unsafe targets, unsupported formats, and local processing failures.
- [x] Benchmark a direct static HTTP path last, after the egress gate. Add it only if it materially improves latency/request cost and uses the same proxy/DNS pinning, redirects, byte limits, crawler identity, and cancellation. Its ability to send conditional validators is a Phase 4 requirement because Crawl4AI's network API cannot accept arbitrary request headers.
- [x] Add a bounded `/v1/fetch` REST/MCP contract after the internal outcome model and egress gate are stable. It is useful to agents with a known URL and need not wait for site crawl; raw HTML remains opt-in, size-capped, and absent from ordinary search.

### Implementation report — 2026-08-07

- Added shared request, response, content, route-deadline, stage-deadline, process-admission, per-host, and internal-fan-out policies across REST, in-process MCP, stdio MCP, search, known-URL fetch, Crawl4AI, and chunking.
- Added bounded `POST /v1/fetch` plus matching in-process and stdio `web_fetch` tools. The typed per-URL contract reports requested/final URL, status, content type, title, links, bounded metadata and validators, retrieval method, elapsed time, retryability, and closed terminal reasons without changing `thorondor.search.v1`.
- Reworked Crawl4AI handling to stream and cap upstream bodies, preserve duplicate inputs safely, retain only allowlisted provenance, validate changed final URLs before content acceptance, classify challenge and empty shells, and contain local failures per URL. Failed HTML cleaning falls back to Crawl4AI Markdown.
- Added cancellation-aware route cleanup and stage deadlines for planning, discovery, crawling, chunking, and reranking. Capacity exhaustion returns bounded retry metadata while `/livez` remains dependency-free.
- Evaluated a direct static HTTP route and rejected it at the egress security gate because no reviewed connector currently supplies connect-time DNS pinning and redirect enforcement. Crawl4AI remains the only target-site route; no cache or persistence was introduced.
- Added deterministic Phase 2 fixtures and benchmark reporting for static HTML, modeled browser rendering, PDF/document content, challenge and empty shells, malformed payloads, metadata, links, bytes, requests, latency, and terminal-reason accuracy.

### Verification

- [x] Add transport tests for body/response caps, process-wide and per-host rejection, per-stage attribution, deadline/disconnect cancellation, cleanup of tasks/slots, bounded `Retry-After`, and unaffected liveness checks.
- [x] Add deterministic backend tests for static and browser-rendered success, final URL/status/headers/links capture, empty/challenge/extraction-empty shells, cleaner fallback, PDF/document, timeout, malformed payload, robots refusal, unsafe redirect, oversize content, and unsupported capability.
- [x] Add regression tests proving every target-site dispatch uses URL safety and enforced egress, final URLs are revalidated, and no arbitrary request/response header crosses the trust boundary.
- [x] Add REST/in-process-MCP/stdio-MCP parity tests for `/v1/fetch`, cancellation, limits, and per-URL outcomes.
- [x] Publish a fixture benchmark comparing candidate routes by evidence quality, JavaScript/static/document coverage, metadata/links completeness, latency, requests, bytes, and terminal reason before enabling a new route.

### Verification report — 2026-08-07

- Separate verification found two low-severity omissions and corrected them: the closed fetch-outcome test and contract prose lacked the intentional `capacity_unavailable` member, and cancellation was proven at REST/pipeline boundaries but not directly at both MCP wrappers. No production behavior was weakened or bypassed.
- Focused MCP verification passed 23 in-process/transport tests and 9 stdio-proxy tests, including caller-cancellation propagation, fetch parity, response caps, capacity errors, and the concise stable `web_search` descriptor.
- Full offline service and orchestrator suite: 526 passed. Full CLI suite: 80 passed.
- `PYTHON=.venv/bin/python bash scripts/check-release-guard.sh` passed, including compilation, locked dependency checks, tests, and rendered Compose topology. `git diff --check` also passed.
- The final fixture benchmark processed seven outcomes in 0.294 ms with zero network requests and 2,546 payload bytes. Terminal-reason accuracy, content Markdown coverage, metadata coverage, and applicable HTML link coverage were each 1.0.
- The direct static route remains disabled because it failed the connect-time DNS-pinning and redirect-enforcement eligibility gate.
- Tengwar's `just thorondor-deploy` recipe rebuilt local orchestrator image `sha256:c2023edce3d1a425c325b351f29f3bf1ad850260aea94590e6469a29481e4665`, recreated only the Thorondor orchestrator, preserved the shared network and existing services, and reported every readiness dependency healthy.
- Real MCP 2.0 `curl` calls from Tengwar's backend container listed both tools, executed `web_search` through discovery, Crawl4AI, chunking, embedding, and reranking, and executed `web_fetch` against `https://example.com` with a successful typed `thorondor.fetch.v1` content outcome.
- Tengwar discovered both live descriptors but correctly withheld them from agent chat because the changed `web_search` checksum and new `web_fetch` descriptor require explicit administrator approval. No registry policy was bypassed or silently accepted.
- Phase 2 was accepted by the user after deployment and live verification.

### Acceptance — 2026-08-07

- Accepted by the user after separate verification, canonical Tengwar deployment, and successful live MCP calls to both `web_search` and `web_fetch`.

## Phase 3 — Sitemap-first mapping and bounded site crawl
**Status:** pending
**Kind:** logic

### Tasks

- [ ] Add one pure crawl-frontier/admission policy shared by `map` and `crawl`, with deterministic ordering and explicit discovered, admitted, queued, fetched, filtered, failed, and cancelled states. The same function must apply URL safety, scope, robots, deduplication, and every item/byte/depth budget at seed, sitemap, link, queue, dispatch, redirect, and final-URL boundaries.
- [ ] Support sitemap modes `include`, `only`, and `skip`. Resolve and validate the seed redirect chain first, report requested/effective origins, and re-home same-origin policy onto the final origin. Probe robots-declared sitemaps, bounded sitemap indexes, and common sitemap locations, then use bounded BFS when policy permits.
- [ ] Add an optional SearXNG `site:` discovery source as a Thorondor-specific map augmentation. Keep source diagnostics separate so sitemap, link, and search contributions can be measured and disabled independently.
- [ ] Default to same origin and the seed path hierarchy. Add explicit opt-ins for parent paths and subdomains; defer arbitrary external-link crawling.
- [ ] Support maximum depth/pages, maximum discovered URLs, safe include/exclude path globs, query-parameter policy, document/file allowlists, and maximum robots/sitemap bytes and entries. Truncate sitemap entries deterministically by valid `lastmod` descending, then priority descending, then document order; entries without valid signals come last.
- [ ] Implement an independent RFC 9309 robots policy using the configured crawler token: correct group merging and longest-match/allow-tie behavior, percent-encoding comparison, redirect cap, parseable-rule recovery, bounded bytes, and one snapshot per operation with cache control no longer than the standard permits. Treat 4xx robots responses as unavailable/allow and 5xx or network failure as unreachable/disallow unless a valid cached copy applies; record the decision and source.
- [ ] Apply process-wide and per-host concurrency, configured delay, robots crawl-delay, bounded parsing of both forms of `Retry-After`, bounded jitter, and adaptive 403/429 cooldown without turning a block into challenge evasion.
- [ ] Expose URL-only synchronous `map` and small synchronous `crawl` through versioned REST and MCP. Keep search's existing discover/select/fetch behavior unchanged until the crawl benchmark demonstrates an improvement.
- [ ] Return per-URL terminal reason codes and aggregate counts, with deterministic item/byte truncation. Expose sitemap `lastmod` as untrusted `modified_at` metadata, never as publication date.

### Verification

- [ ] Add unit tests for sitemap indexes/cycles, truncation order, oversized/malformed XML, seed re-homing, path boundaries, subdomains, files, fragments, repeated query keys, canonical collisions, include/exclude precedence, and every budget.
- [ ] Add RFC 9309 fixtures for exact/wildcard user-agent groups, merged groups, allow/disallow ties, `*`/`$`, percent-encoding, malformed lines, redirects, 4xx/5xx/timeouts, cached copies, byte limits, and crawl-delay extension handling.
- [ ] Add tests for delta-seconds/HTTP-date `Retry-After`, adaptive cooldown recovery, process/per-host concurrency, cancellation, and no slot leaks after failure.
- [ ] Add an integration test against the local fixture site covering effective-origin mapping, sitemap plus BFS fusion, redirects, robots denial/unreachability, unsafe targets, partial fetch failures, and deterministic output ordering.
- [ ] Publish a benchmark comparing sitemap-only, BFS-only, fused mapping, and optional SearXNG augmentation by coverage, requests, latency, duplicate rate, and unsafe/filtered counts.

## Phase 4 — Opt-in page cache, revalidation, and change detection
**Status:** pending
**Kind:** logic

### Tasks

- [ ] Introduce a cache repository protocol and an orchestrator-owned, operator-enabled local persistent adapter. Keep persistence disabled by default and use an explicit volume and retention configuration when enabled.
- [ ] Key page entries by conservative URL identity plus retrieval/extraction variant, cleaner version, and content capability. Bypass cache for authorization/cookie/custom-header requests and any capability whose output is not represented in the key. Keep the page cache independent of embedding/chunker versions unless a separately designed chunk cache is added.
- [ ] Store the minimum record: requested/final URL, cleaned Markdown, source-document hash, title/metadata/links, status, content type, retrieval method, validators, fetched/expiry timestamps, and cleaner version. Raw HTML requires a separate opt-in.
- [ ] Do not collapse `www` into the apex host, cache challenge shells, persist robots refusals as content, or let a cache hit bypass URL safety and current request policy.
- [ ] Add `force_refresh`, bounded stale-while-revalidate, request coalescing, and caller-visible `fresh`, `stale`, `revalidated`, and `bypass` states. Use `If-None-Match`/`If-Modified-Since` only on a backend that can send them; a `304` reuses the prior document while updating freshness metadata, while incompatible JavaScript routes perform a full bounded refetch and hash comparison.
- [ ] Detect `new`, `same`, `changed`, and `removed` using both document hash and HTTP status. Treat transitions such as `200` to `404` as changes and ship these flags before diff summaries.
- [ ] Add cache stats, scoped clear, retention cleanup, schema migration, corruption recovery, and observable hit/miss/bypass/stale/revalidated reasons.
- [ ] Produce bounded section/line diff summaries only after cache, revalidation, and change flags are stable. Cap both input and algorithmic work; above the cap, return a summary-only `truncated` result instead of running unbounded quadratic comparison.
- [ ] Defer semantic vectors and scheduled watch jobs until page-cache lifecycle, retention, and change detection are proven.

### Verification

- [ ] Add tests for cache identity variants, custom-header bypass, page-cache independence from chunker changes, `www` separation, repeated query keys, cleaner-version changes, TTL boundaries, stale windows, force refresh, and concurrent refresh coalescing.
- [ ] Add tests for capable/incompatible validator routes, `ETag`, `Last-Modified`, `304`, full-refetch fallback, changed validators, status-only changes, caller-visible freshness state, challenge/error non-caching, corruption, migrations, retention, and raw-HTML opt-in.
- [ ] Add diff tests for empty/new/removed content, CRLF, large documents, truncation, and complexity caps.
- [ ] Add a restart integration test using a temporary persistent volume and verify that default search writes nothing when cache is disabled.
- [ ] Add and verify a deployment privacy/retention checklist.

## Phase 5 — Durable asynchronous crawl jobs
**Status:** pending
**Kind:** logic

### Tasks

- [ ] Define a measured synchronous threshold and an explicit async mode. Ordinary search, map, and small crawls remain synchronous.
- [ ] Reuse the approved local persistence mode for a minimal job store. Start with one orchestrator/worker process and documented single-replica semantics; require workload evidence before adding Redis or a separate queue service.
- [ ] Implement states `queued`, `running`, `completed`, `partial`, `failed`, `cancelled`, and `expired`, with timestamps, progress counters, bounded failure summaries, and a result-retention deadline absolute from the terminal-state transition. Reads and polling must not extend retention.
- [ ] Add create, status, byte-and-item-bounded cursor pagination, and cancel endpoints. Status and cancellation must verify the same access scope as creation.
- [ ] Claim an idempotency key atomically with a canonical request fingerprint. Replay the existing job for an identical request, return conflict for a different request, and expire the claim with the job.
- [ ] Make cancellation cooperative: stop admitting URLs, cancel queued/pending work, let unavoidable in-flight I/O settle under its deadline, and persist one terminal transition.
- [ ] Add bounded retry/backoff by failure class, per-job and per-host concurrency, atomic result deduplication, and startup recovery for interrupted jobs. A restart must not duplicate a page result or lose a terminal state.
- [ ] Return an explicit expired response after retention. Defer webhooks until polling and cancellation are stable; any later webhook target must use the same SSRF/egress protections and bounded retries.

### Verification

- [ ] Add state-machine tests for atomic duplicate submission, same-key/different-body conflict, partial completion, retry exhaustion, cancellation races, expiry, and idempotent terminal reads.
- [ ] Add pagination tests for stable cursors, byte caps, item caps, concurrent completion, no duplicates, and expiry between pages.
- [ ] Add a local Compose integration test with a fixture crawl, forced process interruption, startup recovery, cancellation, and retention cleanup.
- [ ] Verify no asynchronous path bypasses access control, URL safety, robots, transport budgets, cache retention, or untrusted provenance.

## Phase 6 — Bounded structured extraction
**Status:** pending
**Kind:** logic

### Tasks

- [ ] Build on Phase 1A metadata/JSON-LD provenance and add deterministic profiles for links, tables, and narrowly defined document types. Each extracted field must retain source-document identity and, where practical, an exact source span or evidence reference.
- [ ] Define a strict format enum and capability matrix. Reject incompatible or duplicate formats rather than allowing one result field to overwrite another.
- [ ] Add constrained JSON Schema extraction only after deterministic profiles are stable. Bound schema bytes, depth, property count, URL count, prompt bytes, output bytes, and model deadline; return per-field provenance and validation failures.
- [ ] Treat page instructions and extracted values as untrusted data. Add adversarial fixtures for prompt injection, malformed structured data, hidden content, conflicting metadata, and oversized schemas.

### Verification

- [ ] Add happy-path and adversarial tests for links, tables, JSON-LD, malformed schemas, conflicting fields, prompt-injected pages, and every byte/time/count limit.
- [ ] Add provenance tests proving extracted fields refer to the exact retained source document and cannot cite challenge/error content.
- [ ] Publish benchmark reports by document category and extraction profile, including accuracy, unsupported capability, validation failures, latency, and model usage before enabling a profile by default.

## Explicit non-goals

- [ ] Do not replace SearXNG with Wigolo's direct engine adapters or claim per-engine controls that SearXNG does not expose.
- [ ] Do not import, vendor, translate, or mechanically adapt Wigolo or Firecrawl source, tests, assets, schemas, or AGPL implementation fragments.
- [ ] Do not reproduce anti-bot stealth, clearance reuse, human-solve flows, hosted billing, multi-tenancy, cloud indexing, enterprise threat products, or zero-data-retention product machinery.
- [ ] Do not add a queue, vector corpus, scheduler, browser-action surface, or second persistence backend merely because a reference repository has one.
- [ ] Defer browser actions, research orchestration, neighbour-span retrieval across requests, and semantic find-similar/vector search to separate successor designs with their own threat, lifecycle, and product requirements.
- [ ] Do not weaken SSRF, DNS pinning, redirect validation, robots, egress isolation, untrusted-content labels, or default non-persistence to improve coverage.
