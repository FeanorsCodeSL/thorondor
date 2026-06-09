import json
import pathlib

from chunking.cluster_semantic import ClusterSemanticChunker
from tests.conftest import fake_embed

GOLDEN = pathlib.Path(__file__).parent / "golden" / "cluster_semantic@1.json"


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
