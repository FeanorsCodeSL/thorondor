import json
import pathlib

from chunking.cluster_semantic import ClusterSemanticChunker
from tests.conftest import fake_embed

GOLDEN = pathlib.Path(__file__).parent / "golden" / "cluster_semantic@1.json"
MIXED_GOLDEN = pathlib.Path(__file__).parent / "golden" / "cluster_semantic@1-mixed-topics.json"


def _run():
    text = "The eagle soared. " * 30 + "\n\n" + "Markets fell sharply. " * 30
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=60, min_chunk_size=10)
    return [
        {"text": c.text, "tokens": c.token_count}
        for c in ch.split_text_with_metadata(text)
    ]


def test_golden_parity():
    assert GOLDEN.exists(), "Missing golden file; generate it deliberately before running parity."
    out = _run()
    assert out == json.loads(
        GOLDEN.read_text()
    ), "Chunking drifted from the golden file — re-chunk deliberately, do not silently update."


def test_mixed_interleaved_topics_golden_parity_differs_from_greedy_max_cap():
    assert MIXED_GOLDEN.exists(), "Missing mixed-topic golden; generate it deliberately."
    text = (
        "Eagle scans bright cliffs. "
        "Quantum gates rotate phases. "
        "Eagle guards high nests. "
        "Quantum circuits bind qubits."
    )

    def topic_embed(texts):
        out = []
        for item in texts:
            out.append([1.0, 0.0] if "eagle" in item.lower() else [0.0, 1.0])
        return out

    ch = ClusterSemanticChunker(
        topic_embed,
        max_chunk_size=8,
        min_chunk_size=1,
        initial_segment_size=4,
    )
    out = [
        {"text": c.text, "tokens": c.token_count, "segments": c.segment_indices}
        for c in ch.split_text_with_metadata(text)
    ]

    assert out == json.loads(MIXED_GOLDEN.read_text())
    assert [item["segments"] for item in out] != [[0, 1], [2, 3]]
