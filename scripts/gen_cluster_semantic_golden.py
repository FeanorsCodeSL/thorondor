"""Regenerate reviewed cluster-semantic@1 golden files."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "semantic-chunking-service"
GOLDEN_DIR = SERVICE / "tests" / "golden"

sys.path.insert(0, str(SERVICE))

from chunking.cluster_semantic import ClusterSemanticChunker  # noqa: E402


def fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:16]
        out.append((v / (np.linalg.norm(v) or 1)).tolist())
    return out


def topic_embed(texts: list[str]) -> list[list[float]]:
    return [
        [1.0, 0.0] if "eagle" in text.lower() else [0.0, 1.0]
        for text in texts
    ]


def baseline_golden() -> list[dict[str, object]]:
    text = "The eagle soared. " * 30 + "\n\n" + "Markets fell sharply. " * 30
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=60, min_chunk_size=10)
    return [
        {"text": c.text, "tokens": c.token_count}
        for c in ch.split_text_with_metadata(text)
    ]


def mixed_topics_golden() -> list[dict[str, object]]:
    text = (
        "Eagle scans bright cliffs. "
        "Quantum gates rotate phases. "
        "Eagle guards high nests. "
        "Quantum circuits bind qubits."
    )
    ch = ClusterSemanticChunker(
        topic_embed,
        max_chunk_size=8,
        min_chunk_size=1,
        initial_segment_size=4,
    )
    return [
        {"text": c.text, "tokens": c.token_count, "segments": c.segment_indices}
        for c in ch.split_text_with_metadata(text)
    ]


def write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, indent=2) + "\n")


def main() -> int:
    write_json(GOLDEN_DIR / "cluster_semantic@1.json", baseline_golden())
    write_json(GOLDEN_DIR / "cluster_semantic@1-mixed-topics.json", mixed_topics_golden())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
