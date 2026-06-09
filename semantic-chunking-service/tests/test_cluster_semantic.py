import chunking.cluster_semantic as cs
import numpy as np
import pytest
from chunking.cluster_semantic import ClusterSemanticChunker
from chunking.strategies import resolve_strategy
from tests.conftest import fake_embed


def test_cluster_semantic_at_1_params_are_pinned():
    params = resolve_strategy("cluster-semantic@1")
    assert params.max_chunk_tokens == 400
    assert params.min_chunk_tokens == 50
    assert params.initial_segment_tokens == 50


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


def test_dynamic_programming_valve_3_uses_greedy_fallback_for_pathological_lengths():
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=10, min_chunk_size=9)
    groupings = ch._dynamic_programming_chunking(
        ["alpha", "bravo", "charlie"],
        np.eye(3, dtype=np.float32),
        [4, 4, 4],
    )

    assert groupings == [(0, 2), (2, 3)]


def test_dynamic_programming_uses_similarity_matrix_not_greedy_max_cap():
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=4, min_chunk_size=1)
    segments = ["alpha", "bravo", "charlie", "delta"]
    lengths = [2, 2, 2, 2]
    similarity_matrix = np.eye(4, dtype=np.float32)
    similarity_matrix[1, 2] = 10.0
    similarity_matrix[2, 1] = 10.0

    groupings = ch._dynamic_programming_chunking(segments, similarity_matrix, lengths)

    assert ch._greedy_fallback_chunking(lengths) == [(0, 2), (2, 4)]
    assert groupings == [(0, 1), (1, 3), (3, 4)]


def test_dynamic_programming_reward_cache_is_per_matrix():
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=4, min_chunk_size=1)
    segments = ["alpha", "bravo", "charlie", "delta"]
    lengths = [2, 2, 2, 2]
    first = np.eye(4, dtype=np.float32)
    first[0, 1] = first[1, 0] = 10.0
    first[2, 3] = first[3, 2] = 10.0
    second = np.eye(4, dtype=np.float32)
    second[1, 2] = second[2, 1] = 10.0

    assert ch._dynamic_programming_chunking(segments, first, lengths) == [(0, 2), (2, 4)]
    assert ch._dynamic_programming_chunking(segments, second, lengths) == [(0, 1), (1, 3), (3, 4)]


def test_oom_guard_skips_similarity_matrix_above_cap_and_matrix_is_float32(monkeypatch):
    monkeypatch.setattr(cs, "MAX_SEGMENTS_FOR_DP", 3)
    called = False
    original_compute = ClusterSemanticChunker._compute_similarity_matrix

    def fail_if_called(self, embeddings):
        nonlocal called
        called = True
        raise AssertionError("similarity matrix must not be allocated above the DP cap")

    monkeypatch.setattr(ClusterSemanticChunker, "_compute_similarity_matrix", fail_if_called)
    out = ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata(
        "word " * 200
    )

    matrix = original_compute(
        ClusterSemanticChunker(fake_embed),
        [[1.0, 0.0], [0.0, 1.0]]
    )
    assert called is False
    assert out
    assert matrix.dtype == np.float32
    assert matrix.nbytes == 2 * 2 * 4


def test_greedy_fallback_respects_max_except_lone_oversized_segment():
    ch = ClusterSemanticChunker(fake_embed, max_chunk_size=10, min_chunk_size=1)

    assert ch._greedy_fallback_chunking([3, 4, 5, 2]) == [(0, 2), (2, 4)]
    assert ch._greedy_fallback_chunking([12, 3]) == [(0, 1), (1, 2)]


def test_greedy_semantic_places_boundary_on_clear_topic_shift():
    segments = ["eagle cliff", "eagle nest", "quantum gate", "quantum qubit"]
    positions = [(0, 11), (12, 22), (23, 35), (36, 49)]
    lengths = [2, 2, 2, 2]

    def topic_embed(texts):
        return [[1.0, 0.0] if "eagle" in text else [0.0, 1.0] for text in texts]

    out = ClusterSemanticChunker(
        topic_embed,
        max_chunk_size=10,
        min_chunk_size=1,
    )._greedy_semantic_chunking(segments, positions, lengths)

    assert [item.segment_indices for item in out] == [[0, 1], [2, 3]]


def test_token_offsets_are_within_input():
    text = "Alpha beta gamma. " * 20
    for c in ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata(text):
        assert 0 <= c.start_index <= c.end_index <= len(text)
