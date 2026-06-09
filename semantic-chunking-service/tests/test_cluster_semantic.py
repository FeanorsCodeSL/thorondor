import chunking.cluster_semantic as cs
from chunking.cluster_semantic import ClusterSemanticChunker
from tests.conftest import fake_embed


def test_single_segment_returns_one_chunk():
    out = ClusterSemanticChunker(fake_embed, max_chunk_size=400).split_text_with_metadata(
        "one short line"
    )
    assert len(out) == 1


def test_determinism():
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=40)
    text = "Alpha beta gamma. " * 50
    a = [c.text for c in ch.split_text_with_metadata(text)]
    b = [c.text for c in ch.split_text_with_metadata(text)]
    assert a == b and len(a) >= 2


def test_oom_guard_uses_greedy_semantic(monkeypatch):
    monkeypatch.setattr(cs, "MAX_SEGMENTS_FOR_DP", 3)
    out = ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata(
        "word " * 200
    )
    assert len(out) >= 1


def test_embedding_failure_falls_back_to_token_chunking():
    def boom(_):
        raise RuntimeError("embed down")

    out = ClusterSemanticChunker(boom, max_chunk_size=40).split_text_with_metadata(
        "word " * 100
    )
    assert len(out) >= 1


def test_token_offsets_are_within_input():
    text = "Alpha beta gamma. " * 20
    for c in ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata(text):
        assert 0 <= c.start_index <= c.end_index <= len(text)
