# Web Intelligence Phase 2 Fetch Route Gate

## Scope

This deterministic fixture benchmark validates the typed Crawl4AI adapter and decides whether a direct static HTTP route is eligible. It starts no services and sends no network requests. Its sub-millisecond timing measures in-process outcome parsing only, not browser, network, or end-to-end search latency.

Run it from the repository root:

```bash
.venv/bin/python -m scripts.benchmark_web_intelligence_phase2
```

## Configuration

- Fixture revision: `web-intelligence-phase0-v2`
- Maximum retained page content: 262,144 bytes
- Maximum upstream/serialized response: 2,097,152 bytes
- Raw HTML default: disabled
- Enabled production route: `crawl4ai_browser`

## Result

The 2026-08-07 verification run processed seven fixture outcomes in 0.294 ms, consumed 2,546 fixture payload bytes, and made zero network requests. All seven terminal reasons matched their labels. Static HTML, a modeled browser-rendered page, PDF, and document cases all returned bounded Markdown; metadata coverage was 4/4, and links coverage was 2/2 for the HTML cases where links apply. Challenge, JavaScript empty-shell, and malformed-upstream fixtures failed closed without evidence content.

Production Crawl4AI routing issues one internal API request per URL. Target-site request count and browser/network latency are intentionally not inferred from this fixture-only run.

| Route | Security eligible | Enabled | Fixture latency | Fixture requests | Decision |
|---|---:|---:|---:|---:|---|
| Crawl4AI browser | yes | yes | 0.294 ms | 0 | Keep as the only fetch route |
| Direct static HTTP | no | no | not run | 0 | Reject before performance comparison |

## Direct-route decision

Thorondor does not currently have a direct connector that preserves the reviewed connect-time DNS pinning, redirect validation, byte limits, crawler identity, and cancellation policy. A raw `httpx` path would therefore broaden the target-site trust boundary even if it were faster. The candidate was rejected at the security gate, so no latency number was manufactured and no static route was added.

The direct candidate may be reconsidered only with an eligible connector and a new fixture plus live benchmark. Until then, Crawl4AI remains responsible for static HTML, JavaScript rendering, PDF/document extraction, robots handling, and target-site dispatch.
