import json
import time
from pathlib import Path

from orchestrator.evidence_quality import EVIDENCE_QUALITY_STRATEGY, filter_evidence_quality
from orchestrator.types import Chunk, ScoredChunk

FIXTURE_ROOT = Path(__file__).parents[1] / "orchestrator" / "tests" / "fixtures" / "phase1b"


def _rank_best_upstream(rankings: list[list[str]]) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for index, document in enumerate(ranking, start=1):
            scores[document] = max(scores.get(document, 0.0), 1.0 / index)
    return sorted(scores, key=lambda document: (-scores[document], document))


def _rank_rrf(rankings: list[list[str]], rank_constant: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for index, document in enumerate(ranking, start=1):
            scores[document] = scores.get(document, 0.0) + 1.0 / (rank_constant + index)
    return sorted(scores, key=lambda document: (-scores[document], document))


def _reciprocal_rank(ranking: list[str], relevant: str) -> float:
    return 1.0 / (ranking.index(relevant) + 1)


def _quality_report() -> dict:
    started = time.perf_counter()
    fixture = json.loads((FIXTURE_ROOT / "evidence-quality.json").read_text())
    scored = [
        ScoredChunk(
            Chunk(item["text"], len(item["text"].split()), item["id"], item["id"], 0),
            1.0,
        )
        for item in fixture["items"]
    ]
    outcome = filter_evidence_quality(scored)
    retained = {item.chunk.source_url for item in outcome.scored}
    expected_drop = {item["id"] for item in fixture["items"] if item["expected_drop_rule"]}
    predicted_drop = {item["id"] for item in fixture["items"] if item["id"] not in retained}
    useful = {item["id"] for item in fixture["items"] if item["agent_useful"]}
    true_positive = len(expected_drop & predicted_drop)
    useful_before = len(useful)
    useful_after = len(useful & retained)
    return {
        "fixture_revision": fixture["revision"],
        "configuration": {
            "strategy": EVIDENCE_QUALITY_STRATEGY,
            "enabled_by_default": False,
        },
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "network_request_count": 0,
        "fixture_item_count": len(fixture["items"]),
        "degradation_reasons": [],
        "drop_precision": true_positive / len(predicted_drop),
        "drop_recall": true_positive / len(expected_drop),
        "useful_evidence_retained_before": useful_before,
        "useful_evidence_retained_after": useful_after,
        "useful_evidence_recall_delta": (useful_after - useful_before) / useful_before,
        "answer_coverage_before": useful_before / useful_before,
        "answer_coverage_after": useful_after / useful_before,
        "answer_coverage_delta": (useful_after - useful_before) / useful_before,
        "noise_items_returned_before": len(expected_drop),
        "noise_items_returned_after": len(expected_drop & retained),
    }


def _ranking_report() -> dict:
    started = time.perf_counter()
    fixture = json.loads((FIXTURE_ROOT / "ranking-comparison.json").read_text())
    best_scores = []
    rrf_scores = []
    for query in fixture["queries"]:
        best_scores.append(
            _reciprocal_rank(
                _rank_best_upstream(query["rankings"]),
                query["relevant"],
            )
        )
        rrf_scores.append(_reciprocal_rank(_rank_rrf(query["rankings"]), query["relevant"]))
    best_mrr = sum(best_scores) / len(best_scores)
    rrf_mrr = sum(rrf_scores) / len(rrf_scores)
    return {
        "fixture_revision": fixture["revision"],
        "configuration": {
            "candidate_strategy": "rrf",
            "rank_constant": 60,
            "production_strategy_modeled": False,
        },
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "network_request_count": 0,
        "fixture_query_count": len(fixture["queries"]),
        "degradation_reasons": [],
        "best_upstream_mrr": best_mrr,
        "rrf_mrr": rrf_mrr,
        "rrf_enabled": rrf_mrr > best_mrr,
    }


def build_report() -> dict:
    return {
        "evidence_quality": _quality_report(),
        "ranking": _ranking_report(),
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
