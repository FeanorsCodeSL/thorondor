# Web Intelligence Phase 0 Baseline

## Scope

This is a deterministic local-fixture baseline, not a live-performance result. It covers the v1 compatibility contract and raw upstream metadata shapes without starting services or sending network requests.

## Fixture identity

- Revision: `web-intelligence-phase0-v2`
- Identity: `thorondor-local-first-party-web-intelligence`
- Corpus: 17 original, UTF-8 fixture artifacts under `orchestrator/tests/fixtures/web_intelligence/`; `corpus-manifest.json` records a SHA-256 for each artifact.
- Schema baseline: the existing exact `thorondor.search.v1` JSON-schema golden remains `orchestrator/tests/golden/search-response-schema.json`. The separate `search-v1-compatibility-contract.json` is the monotonic v1 floor; its response examples are wire-level validation fixtures, not captured `run_search` responses or live runtime/performance traces.

## Deterministic coverage

| Area | Fixture artifact |
| --- | --- |
| Static HTML and JavaScript shell | `static-page.html`, `javascript-shell.html` |
| Redirect metadata and challenge shell | `redirect-metadata.json`, `challenge-shell.html` |
| Robots groups plus 404/503 state descriptors | `robots-groups-and-states.json` |
| Sitemap index | `sitemap-index.xml` |
| Shared long preamble with distinct bodies and fixture URLs | `duplicate-preamble-a.md`, `duplicate-preamble-b.md`, `duplicate-preamble-urls.json` |
| Conflicting canonical declarations | `canonical-conflicts.html` |
| Query variants and repeated keys | `query-variants.json` |
| Unicode, combining character, and CRLF Markdown | `unicode-crlf-markdown.json` |
| PDF and document metadata | `document-metadata.json` |
| Status, ETag, and Last-Modified transitions | `status-validator-transitions.json` |
| SearXNG plural engines, positions, and publishedDate | `searxng-plural-result.json` |
| Crawl4AI final URL, status, headers, links, and metadata | `crawl4ai-metadata-result.json` |
| Malformed upstream envelopes and fields | `malformed-upstream-payloads.json` |

The corpus is data coverage only. It does not claim that the pending fetch, robots, sitemap, cache, or metadata features are implemented.

## Deterministic request-count expectations

The focused compatibility and raw-contract tests consume checked-in files in process. Their expected HTTP request count is zero for every scenario. The SearXNG and Crawl4AI cases each represent one synthetic upstream payload, not a live upstream request. No container, local fixture server, DNS lookup, or provider call is part of this baseline.

## Current counters and blind spots

The following is code-inspection context for the current implementation, not an observation or proof produced by this local fixture suite. `SearchStats.urls_crawled_failed` is calculated from selected URLs minus returned pages. It cannot distinguish redirect safety rejection, robots refusal, empty/challenge content, upstream timeout/status, malformed upstream response, unsupported content, or local extraction failure. The current no-chunk reason codes identify broad pipeline termination (`no_chunks_after_dedup` and `no_chunks_after_rerank`) but do not attribute the affected URL or a per-document cause.

The corpus contains these cases so later fetch-outcome work can break those aggregates down. It is not evidence that the current runtime reports those detailed terminal reasons.

## Duplicate baseline

The following is code-inspection context for the current implementation, not an observation or proof produced by this local fixture suite. `duplicate-preamble-a.md` and `duplicate-preamble-b.md` have an identical preamble longer than 2,000 characters and intentionally different bodies. The current deduplicator lowercases, collapses whitespace, and fingerprints only the first 2,000 characters, so this preserves a regression input for its prefix-based behavior; the fixture is not a claim that the two documents should be discarded as exact duplicates. Exact duplicate rate is unavailable because this test-only baseline does not execute a corpus driver through the pipeline.

## Terminal reasons

The current v1 response contract recognizes these aggregate terminal reasons: `search_provider_unavailable`, `no_results_from_discovery`, `no_urls_after_selection`, `all_crawls_failed`, `no_chunks_after_dedup`, and `no_chunks_after_rerank`. The local corpus also labels future-facing source observations such as robots 404/503, challenge shell, malformed metadata, and status transitions. Those labels are fixture descriptors, not current runtime result codes.

## Timing and live performance

Per-stage timing is unavailable in the current deterministic corpus. `SearchStats.elapsed_ms` is a response-level aggregate, and this baseline does not run the search pipeline or collect a timing sample. Live latency, live request count, live duplicate rate, source success/degradation totals, bytes, and output-quality metrics are all unavailable.

To collect them reproducibly later, run a first-party local fixture harness with the manifest revision and hashes fixed, record the checked-out revision and configuration, count every synthetic dependency dispatch, capture monotonic start/end timestamps for discovery, selection, fetch, cleaning, chunking, reranking, and assembly, then write raw per-item terminal outcomes alongside aggregate counters. Do this only after the relevant fetch and egress work is implemented and an operator authorizes the live or fixture-site procedure.
