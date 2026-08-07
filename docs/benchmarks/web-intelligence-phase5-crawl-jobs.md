# Phase 5 Crawl-Mode Threshold Benchmark

## Purpose

Measure the local response and persistence cost around Thorondor's bounded crawl result shape, then record the default boundary between the existing synchronous route and the explicit durable job route.

## Fixture and command

- Fixture revision: `web-intelligence-phase5-jobs-v1`
- Page counts: 1, 10, and 20
- Cleaned Markdown per result: 2,048 bytes
- Network requests: 0; the benchmark uses deterministic page results
- Terminal reason: `completed` for every case

Run from the repository root:

```bash
.venv/bin/python -m scripts.benchmark_web_intelligence_phase5
```

The regression assertion is:

```bash
.venv/bin/python -m pytest orchestrator/tests/test_phase5_benchmark.py -q
```

## Result — 2026-08-07

The checked test requires 100% result recall, zero duplicate results, the modeled page-request count, the terminal reason, and a recorded local latency for both modes at every page count. The measured timings are fixture-only orchestration and SQLite costs; they are not target-site, browser, or production end-to-end latency and must not be presented as such.

| Pages | Synchronous local time | Durable local time | Modeled requests | Result recall | Duplicates | Outcome |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 0.022 ms | 42.707 ms | 1 | 100% | 0% | completed |
| 10 | 0.044 ms | 86.388 ms | 10 | 100% | 0% | completed |
| 20 | 0.081 ms | 181.746 ms | 20 | 100% | 0% | completed |

The configured threshold is 10 pages. `POST /v1/crawl` remains the simple synchronous path for slices at or below that size. `POST /v1/crawl/jobs` is explicit rather than automatic and is appropriate for larger slices, or whenever polling, restart recovery, cancellation, partial results, and absolute retention are more important than a single response.

## Decision

Keep `CRAWL_SYNC_MAX_PAGES=10`. The benchmark shows that both contracts retain every modeled result without duplication through Thorondor's maximum 20-page crawl size; the choice is therefore a product and lifecycle boundary, not a claim that a particular live site always completes within a fixed time. Operators can lower the advertised threshold after measuring their own Crawl4AI and target-site latency, while callers may select durable mode at any size.
