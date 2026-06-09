# 05 - Security and Crawl-Safety Review

Read-only review of Thorondor SSRF / crawl-target safety, prompt-injection boundary, polite-crawling
posture, secret handling, and input/DoS bounds. Code is ground truth; CLAUDE.md no-code-yet status is stale.

## Severity tally
- Critical: 2  (SEC-001 SSRF to internal/metadata; CRAWL-001 unvalidated redirects to SSRF bypass)
- High: 4  (SEC-002 no scheme restriction; SEC-003 blocklist host-exact-match bypassable; CRAWL-002 robots.txt never enforced; SEC-005 no upper bounds on token_budget/max_urls/max_passages/text)
- Medium: 3  (SEC-004 no allowlist-only mode; CRAWL-003 no per-host rate limit; SEC-006 crawl errors log full URLs / no body-retention guard)
- Low: 2  (SEC-007 no untrusted-content boundary marker; SEC-008 BYO endpoints no auth-header support)

Exploitable now (not just hardening): SEC-001, SEC-002, SEC-003, CRAWL-001, CRAWL-002, SEC-005.


## SEC-001 - No SSRF guard: crawler reaches loopback, RFC1918, and the 169.254.169.254 metadata endpoint
- Severity: Critical
- Category: Security
- Evidence: orchestrator/selection.py:6-22 (stage-4 gate) filters only by exact host membership in blocklist/allowlist. No IP-range, loopback, link-local, or cloud-metadata check exists anywhere in the repo (grep for 169.254 / 127.0.0.1 / is_private / ip_address / link-local / metadata across **/*.py returns zero guard code, only test fixtures). orchestrator/pipeline.py:78,84 feeds selected URLs straight to Crawl4aiExtractor.extract (orchestrator/clients/crawl4ai_client.py:18-38), which POSTs the URL to Crawl4AI with no host validation.
- Problem: SearXNG returns attacker-influenceable URLs (a public page can list any URL). Any discovered URL pointing at http://169.254.169.254/latest/meta-data/, http://localhost, http://10.0.0.x, http://chunker:8000, http://searxng:8080 is crawled and its body returned to the agent. On cloud this exfiltrates IAM credentials / instance metadata; on-prem it reaches internal panels and the sibling containers on the Compose internal network.
- Impact: Full SSRF. Cloud credential theft and internal recon via one /search (or MCP web_search) call. The stated first-class risk, unmitigated.
- Recommendation: Add an impure URL-safety resolution step before the stage-4 selection gate, BEFORE any fetch. Resolve each candidate host to its IP(s) and reject if any resolved address is loopback / link-local / private / reserved / multicast / unspecified (ipaddress is_private etc.), and reject IP-literal hosts in those ranges. Block metadata IPs (169.254.169.254, fd00:ec2::254). Feed deterministic safety facts into the selector so `SelectionPolicy` stays pure. Pin the resolved IP through to connect (or use an httpx transport re-validating on connect) to close DNS-rebinding. Non-bypassable by config.
- Acceptance Criteria: A request whose discovery yields 169.254.169.254, 127.0.0.1, 10.1.2.3, [::1], or chunker:8000 selects zero such URLs and never crawls them (assert via a fake extractor recording attempted URLs).
- Regression Tests: URL-safety resolver tests for metadata IP, loopback, each RFC1918 block, IPv6 ULA/loopback, and internal Compose hostnames; pure selector tests with synthetic safety facts proving unsafe candidates are excluded and a public URL still selects.

## CRAWL-001 - Redirects followed with no post-redirect re-validation (SSRF gate bypass)
- Severity: Critical
- Category: Security
- Evidence: orchestrator/clients/crawl4ai_client.py:24-25 posts the raw URL to Crawl4AI /crawl with no redirect policy. Crawl4AI (Playwright-based) follows HTTP/JS redirects by default and nothing here re-checks the final hop. The orchestrator httpx clients also use httpx defaults.
- Problem: Even with SEC-001 fixed, a public allowlisted URL can 30x- or JS-redirect to http://169.254.169.254/... or an internal host. The gate validated only the pre-redirect URL; the final target is fetched unchecked - the canonical public-to-internal redirect SSRF, explicitly in scope.
- Impact: Defeats any selection-time blocklist/allowlist and the SEC-001 guard. Same blast radius.
- Recommendation: Validate the FINAL target, not just the seed. Disable redirect-following and re-run the SSRF guard on each redirect Location, or run the guard via a connection-time hook for every hop. Cap redirect count. Since Crawl4AI is opaque, pass a crawler run config restricting redirects, and/or egress-firewall the crawl4ai container off link-local + RFC1918 as defense-in-depth.
- Acceptance Criteria: A crawl of a public URL that redirects to an internal/metadata target yields no content from the internal target and is dropped with a logged reason.
- Regression Tests: Redirect-to-internal test (mock 302 to 169.254.169.254 and 127.0.0.1) asserts the final fetch is blocked; redirect-chain-depth cap test.

## SEC-002 - Crawl scheme is unrestricted (no http/https allowlist)
- Severity: High
- Category: Security
- Evidence: orchestrator/selection.py and orchestrator/normalize.py:5-7 never inspect the URL scheme. host_for returns urlparse(url).hostname and falls back to the RAW url when there is no hostname (parsed.hostname or url), so schemeless/odd inputs pass through. The crawl client forwards any string.
- Problem: Non-web schemes (file://, gopher://, ftp://, data:) are not rejected at the gate. file:///etc/passwd parses to hostname=None, so host_for returns the whole string, which never matches a blocklist host and (no allowlist) passes selection. The gate offers no defense.
- Impact: Expands SSRF/LFI surface beyond http(s); gopher:// and file:// are classic SSRF escalation schemes.
- Recommendation: In the selection gate, reject any URL whose scheme is not exactly http or https. Treat host_for returning the raw string (no hostname) as an automatic reject.
- Acceptance Criteria: file://, gopher://, data:, ftp://, schemeless candidates dropped at selection and never crawled.
- Regression Tests: Scheme-allowlist tests over select() for each forbidden scheme.

## SEC-003 - Blocklist/allowlist is host-exact-match; bypassable and SSRF-ineffective
- Severity: High
- Category: Security
- Evidence: orchestrator/selection.py:14-21 compares host_for(result.url) against a lowercased exact-string set. host_for (normalize.py:5-7) lowercases and strips userinfo but does NOT strip a trailing dot or normalize IP encodings. Probe confirmed: EVIL.com./x -> host evil.com. (not evil.com); 0x7f000001 and 2130706433 pass through as opaque strings (both loopback, neither matches 127.0.0.1/localhost).
- Problem: (a) DOMAIN_BLOCKLIST internal.corp is bypassed by internal.corp. or a subdomain. (b) Dec/hex/oct IP encodings of a blocked IP do not match a host-string blocklist, so a blocklist cannot reliably stop SSRF - hence SEC-001 IP-range guard is the real control. (c) Exact-host only: evil.com does not block sub.evil.com.
- Impact: Operators believing DOMAIN_BLOCKLIST guards internal hosts are wrong; cosmetic for SSRF, weak for ToS-style exclusion.
- Recommendation: Canonicalize hosts before comparison: strip a trailing dot, IDNA-encode, normalize/reject IP-literal encodings (ipaddress), and match blocklist entries as suffix/registrable-domain. Keep SSRF defense as the IP-range guard (SEC-001), not the host blocklist.
- Acceptance Criteria: evil.com., EVIL.COM, sub.evil.com, 0x7f000001, 2130706433 all blocked when the operator blocks the corresponding host/IP.
- Regression Tests: Canonicalization bypass tests (trailing dot, case, subdomain, IP-encoding) on select().

## CRAWL-002 - robots.txt is never honored (invariant 7 violated)
- Severity: High
- Category: Crawl-Safety
- Evidence: No robots handling in first-party code: grep for robots hits only docs/foundational design/05-licensing-and-sovereignty.md:106 (Respect robots.txt ... Configure Crawl4AI accordingly) - a doc aspiration, never implemented. crawl4ai_client.py:24-25 passes only the URL; no check_robots_txt flag is set, so it relies on the Crawl4AI default, which does NOT enforce robots unless configured.
- Problem: The service crawls regardless of a site robots policy, contradicting the polite-crawling invariant and the sovereignty/ToS posture in doc 05.
- Impact: ToS/legal exposure for operators; impolite crawling; reputational/IP-block risk.
- Recommendation: Set the Crawl4AI robots-check option explicitly (check_robots_txt) as default; expose an env toggle for operators who own the targets. Cache robots within the per-call lifetime only.
- Acceptance Criteria: A URL disallowed by its site robots.txt is not crawled by default.
- Regression Tests: Robots-respected test (mock Disallow -> URL skipped); robots-allowed test.

## SEC-005 - No upper bounds on token_budget / max_urls / max_passages / chunk text (DoS)
- Severity: High
- Category: Security
- Evidence: orchestrator/models.py:39-48 types token_budget, max_urls, max_passages as plain int | None with no Field(le=...) ceiling and no query length limit. app.py:32-37 only fills defaults when falsy. MCP web_search (mcp_server.py:24-25) takes token_budget/max_urls ints with no cap. The chunker ChunkRequest.text (semantic-chunking-service/chunking/models.py:19-24) is an unbounded str, and the DP chunker builds an NxN similarity matrix (architecture map sec 6; OOM guard only at 10000 segments).
- Problem: max_urls 100000 fans out 100k crawls (gated only by CRAWL_CONCURRENCY); token_budget 1e12 defeats the assembly cap and returns the whole corpus; unbounded query/text drives the N^2 chunker blow-up (cross-reference CHUNK findings). One request can exhaust orchestrator and chunker memory/CPU.
- Impact: Trivial unauthenticated resource-exhaustion DoS via one request on either surface.
- Recommendation: Add validated ceilings on the wire models: query max length; token_budget gt=0 le=cap; max_urls gt=0 le=small-cap (e.g. 20); max_passages gt=0 le=cap. Bound chunker text length. Mirror caps in the MCP tool validation.
- Acceptance Criteria: Out-of-range values rejected with 422 (REST) / tool error (MCP) before any discovery/crawl; oversized chunk text rejected.
- Regression Tests: Input-bound tests: over-cap values rejected; oversized query/text rejected; in-range accepted.

## SEC-004 - No allowlist-only locked-down deployment mode
- Severity: Medium
- Category: Security
- Evidence: domains (per-request allowlist) exists (models.py:46, applied pipeline.py:77), but there is no operator-level allowlist env var or allowlist-only mode. settings.py exposes only DOMAIN_BLOCKLIST. With no per-request domains, allowlist is None and everything passes the gate.
- Problem: Locked-down deployments (scope asks for an allowlist mode) cannot constrain the crawler to a vetted domain set at the deployment level; they must trust every caller to pass domains.
- Impact: No defense-in-depth for restricted environments; weakens the selection-gate volume guarantee.
- Recommendation: Add DOMAIN_ALLOWLIST env (and optional ALLOWLIST_ONLY=true) merged into the selection gate so that, when set, only those hosts are ever crawled regardless of request.
- Acceptance Criteria: With ALLOWLIST_ONLY, a request for a non-allowlisted domain selects zero URLs.
- Regression Tests: Operator-allowlist-enforced test; allowlist-only-overrides-request test.

## CRAWL-003 - Concurrency/timeout exist but no per-host rate limiting; concurrency cap not bounded
- Severity: Medium
- Category: Crawl-Safety
- Evidence: crawl4ai_client.py:19 uses asyncio.Semaphore(self.concurrency) and a per-request timeout (settings.py:42-43, defaults 4 / 15s) - good. But there is no per-target-host throttle: all selected URLs for one host can fire within the global concurrency window. CRAWL_CONCURRENCY has no validated upper bound (ties to SEC-005).
- Problem: Multiple URLs on the same domain hit simultaneously; not maximally polite. A large CRAWL_CONCURRENCY removes the politeness throttle.
- Impact: Bursty load on individual sites (ToS/impoliteness), and unbounded concurrency under DoS.
- Recommendation: Add a small per-host concurrency/delay limit alongside the global semaphore; validate CRAWL_CONCURRENCY against a sane ceiling at settings load.
- Acceptance Criteria: Two URLs on the same host not fetched concurrently beyond a per-host limit; CRAWL_CONCURRENCY above the ceiling rejected/clamped.
- Regression Tests: Per-host rate-limit test; concurrency-ceiling settings test.

## SEC-006 - Crawl errors log full target URLs; no explicit body-retention guard
- Severity: Medium
- Category: Security
- Evidence: crawl4ai_client.py:27,34 log the full failing URL at WARNING. The chunker logs at INFO globally (semantic-chunking-service/chunking/app.py:12). Crawled bodies are held in memory through content_dedup -> chunker -> assembler, then dropped per call (architecture map sec 7 confirms no cache class and no persistent store - invariant 1 upheld in code).
- Problem: Ephemerality holds, but (a) full discovered URLs (which can carry sensitive query strings) land in logs; (b) no test asserts page bodies and queries are never logged, so a future logger.debug(markdown) could silently create a retention surface. No secret is logged today (none reach these paths), but the surface is unguarded.
- Impact: Minor data-exposure surface via logs; latent retention/PII risk if logging is later expanded.
- Recommendation: Log only the URL host (or hashed/elided URL) on crawl failure; keep page bodies and full queries out of all log levels; document and test that no body/query is logged.
- Acceptance Criteria: Logs contain no page body and no full query/secret; a regression test scans emitted log records for body/query substrings.
- Regression Tests: Secret/body-not-logged tests over crawl-failure and chunk paths.

## SEC-007 - Crawled evidence carries no untrusted-content boundary marker (prompt-injection)
- Severity: Low (hardening; the service correctly returns evidence not prose and does not itself execute instructions - but it offers no defense to downstream agents)
- Category: Security
- Evidence: Passages flow verbatim: crawl4ai_client.py:30 -> chunker -> assembly.py:30-39 (Passage text=item.chunk.text) -> SearchResponse/MCP passages+citations (mcp_server.py:31-34). No provenance/labeling/sanitization wraps the text. The LLM planner hop (clients/planner.py:24-31) sends the USER QUERY (not crawled text) to the model, so query-planner steering by crawled content is not currently possible - good - but returned passages are unlabeled.
- Problem: Invariant 5 (evidence not prose) is honored. But a consuming agent cannot distinguish untrusted web text from its own instructions; an injected instruction inside a crawled page reaches the agent unmarked.
- Impact: Downstream prompt-injection risk; the tool misses a cheap boundary the scope recommends.
- Recommendation: Wrap each passage text with an explicit untrusted-content delimiter, or add a per-passage provenance=external_web / trust=untrusted field, and document that downstream agents must treat passage text as data, never instructions. Keep the planner hop free of crawled text (it already is).
- Acceptance Criteria: Every returned passage is labeled/wrapped as external-untrusted on REST and MCP.
- Regression Tests: Boundary-marker-present test on REST and MCP; planner-receives-no-crawled-text test.

## SEC-008 - BYO model endpoints carry no auth-header support (forces plaintext / blocks secret servers)
- Severity: Low
- Category: Security
- Evidence: No Authorization/Bearer/api_key handling exists in any client (grep confirms only docs/.gitignore mention secrets). reranker_client.py:20-23, planner.py:20-32, chunker_client.py:18-27 send no auth header. The chunker EmbeddingFunction (embedding_function.py:21-31,73) hard-forces http://host:port, dropping scheme/path - so an HTTPS, token-authenticated embedding server cannot be used.
- Problem: Operators running an authenticated/managed model endpoint cannot supply credentials; the design forces unauthenticated plaintext HTTP to model servers. Conversely, no secret is currently logged or echoed in stats/errors (SearchStats models.py:26-36 has no secret; the 503 detail in app.py:41-44 is dependency+reason only) - that part of the invariant holds.
- Impact: Forces insecure transport and blocks secret-bearing deployments; not directly exploitable.
- Recommendation: Add optional per-seam API_KEY env vars sent as Authorization Bearer headers, and let EMBEDDING_ENDPOINT honor https + path like the orchestrator clients. Confirm keys are env-only, never logged, never placed in responses/stats/error details.
- Acceptance Criteria: A configured API key is sent to the model endpoint and never appears in any log, stats, or error response.
- Regression Tests: Secret-not-logged / secret-not-in-response tests; auth-header-sent test.

---

## Invariant check
- (1) Ephemeral corpus / no persistent index - HOLDS (no cache class, no store; architecture sec 7).
- (7) Crawl politely - VIOLATED: no robots (CRAWL-002), no per-host rate limit (CRAWL-003), and the selection gate does NOT block internal/private targets (SEC-001/003), so it does not protect the crawl.
- Blocklist/allowlist enforced pre-fetch - partial: the host filter runs at stage 4 before crawl (correct location) but is SSRF-ineffective (SEC-001/003) and there is no operator allowlist (SEC-004).
- No secret in logs/stats - HOLDS today (no secrets traverse the logged paths), but unguarded by tests and URLs are logged (SEC-006); no auth-header path exists (SEC-008).
