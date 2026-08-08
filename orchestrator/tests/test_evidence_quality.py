import json
from pathlib import Path

import pytest

from orchestrator.evidence_quality import (
    EVIDENCE_QUALITY_STRATEGY,
    filter_evidence_quality,
)
from orchestrator.types import Chunk, ScoredChunk

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "phase1b"


def _scored(text: str, index: int) -> ScoredChunk:
    return ScoredChunk(
        Chunk(text, len(text.split()), f"https://{index}.test", f"T{index}", 0),
        1.0 - (index * 0.01),
    )


def test_quality_gate_drops_obvious_noise_and_preserves_agent_useful_shapes():
    fixture = json.loads((FIXTURE_ROOT / "evidence-quality.json").read_text())
    inputs = [_scored(item["text"], index) for index, item in enumerate(fixture["items"])]

    outcome = filter_evidence_quality(inputs)

    assert outcome.strategy == EVIDENCE_QUALITY_STRATEGY
    expected_kept = [
        item["text"]
        for item in fixture["items"]
        if item["expected_drop_rule"] is None
    ]
    assert [item.chunk.text for item in outcome.scored] == expected_kept
    assert outcome.dropped_by_rule == {
        "navigation_boilerplate": 1,
        "footer_boilerplate": 1,
        "link_dominated": 1,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Under the GDPR, our Privacy Policy and Cookie Policy must disclose all third-party processors within 30 days.",
        "- [asyncio](https://docs.test/asyncio)\n- [threading](https://docs.test/threading)\n- [multiprocessing](https://docs.test/multiprocessing)\n- [concurrent.futures](https://docs.test/futures)\n- [subprocess](https://docs.test/subprocess)\n- [queue](https://docs.test/queue)",
    ],
)
def test_quality_gate_keeps_short_policy_answers_and_curated_link_lists(text):
    outcome = filter_evidence_quality([_scored(text, 0)])

    assert [item.chunk.text for item in outcome.scored] == [text]
    assert outcome.dropped_by_rule == {}
