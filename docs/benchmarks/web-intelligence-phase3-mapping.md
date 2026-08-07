# Phase 3 Site Mapping Benchmark

## Purpose

Compare the deterministic coverage and request cost of sitemap-only discovery, bounded breadth-first link traversal, source fusion, and optional SearXNG `site:` augmentation before changing Thorondor's search pipeline.

## Fixture and command

- Fixture revision: `web-intelligence-phase3-map-v1`
- Relevant URLs: 5
- Policy: same origin, seed-directory scope, robots enabled
- Limits: depth 2, 10 pages, 50 discovered URLs
- Network requests: 0; the benchmark uses deterministic fakes

Run from the repository root:

```bash
.venv/bin/python -m scripts.benchmark_web_intelligence_phase3
```

The regression assertion is:

```bash
.venv/bin/python -m pytest orchestrator/tests/test_phase3_benchmark.py -q
```

## Result — 2026-08-07

| Mode | Coverage | Modeled fetches | Source overlaps | Filtered | Failed | Omitted | Outcome |
|---|---:|---:|---:|---:|---:|---:|---|
| Sitemap only | 60% | 6 | 0 | 1 | 0 | 0 | completed |
| BFS only | 60% | 4 | 0 | 1 | 0 | 0 | completed |
| Fused sitemap + BFS | 80% | 7 | 2 | 1 | 0 | 0 | completed |
| Fused + SearXNG augmentation | 100% | 8 | 2 | 1 | 0 | 0 | completed |

Observed local execution times were 1.842 ms, 0.353 ms, 0.522 ms, and 0.568 ms respectively. These are fixture-processing measurements, not live-network latency, and are retained only so the run records every required metric.

Fusion recovered one relevant URL missed by each individual source. Optional search augmentation recovered the final relevant URL at one modeled fetch request. Two records in the fused modes preserved multiple source contributions instead of being emitted as duplicate URLs. The robots-blocked URL remained filtered in every mode.

## Decision

Use fused sitemap and bounded BFS for `web_map` and `web_crawl`, with SearXNG augmentation opt-in. Do not alter ordinary `web_search`: this fixture demonstrates mapping-source complementarity, not a production search-quality or latency improvement.
