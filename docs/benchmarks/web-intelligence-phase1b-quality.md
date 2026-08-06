# Web Intelligence Phase 1B Quality Gate

## Scope

This is a deterministic, local-fixture gate for evidence-quality filtering and a reciprocal-rank-fusion diagnostic. It sends no network requests, starts no services, and does not claim live answer quality.

Run it from the repository root:

```bash
.venv/bin/python -m scripts.benchmark_web_intelligence_phase1b
```

## Evidence-quality result

Fixture revision `thorondor-evidence-quality-v2` labels three obvious noise fragments and six agent-useful shapes. The recorded configuration is `evidence-quality@1`, disabled by default; the run uses nine fixture items, zero network requests, and no degradation reason.

| Metric | Result |
|---|---:|
| Drop precision | 1.00 |
| Drop recall | 1.00 |
| Useful evidence retained before / after | 6 / 6 |
| Useful-evidence recall delta | 0.00 |
| Answer-coverage proxy before / after | 1.00 / 1.00 |
| Answer-coverage proxy delta | 0.00 |
| Noise items returned before / after | 3 / 0 |

The useful shapes are a concise factual sentence, code, an ordinary list, a table, a short policy answer that names two footer terms, and a curated six-link resource list. The rejected shapes are a navigation cluster, a structured footer-policy cluster, and a six-link fragment with generic labels and no substantive surrounding text. These labels are still authored fixtures, not independent evidence, so the quality gate remains disabled by default. Operators may enable it explicitly with `EVIDENCE_QUALITY_ENABLED=true`.

## Ranking experiment

Fixture revision `thorondor-ranking-comparison-v1` contains one case where independent agreement helps RRF and one where correlated noise hurts it. It uses two fixture queries, rank constant 60, zero network requests, and no degradation reason.

| Strategy | Mean reciprocal rank |
|---|---:|
| Current best upstream score | 0.75 |
| RRF with rank constant 60 | 0.75 |

This synthetic diagnostic does not model the shipped SearXNG score baseline and is not the Phase 0 quality benchmark required to authorize a ranking change. RRF therefore remains disabled on conservatism, not because the 0.75 tie proves equivalence. Production ranking continues to use the highest upstream discovery score plus the existing lexical selection signal; contributor diagnostics do not silently alter ranking.

## Regression enforcement

`orchestrator/tests/test_evidence_quality.py` consumes both fixture revisions and fails if precision, recall, useful-evidence retention, or the no-RRF decision changes. Pipeline tests separately prove bounded rule counters and retention of concise factual evidence.
