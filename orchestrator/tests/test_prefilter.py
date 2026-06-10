from orchestrator.prefilter import CandidatePrefilterImpl, PREFILTER_STRATEGY
from orchestrator.types import Chunk


def _chunk(text, source_id, position=0, title="Title"):
    return Chunk(
        text=text,
        token_count=len(text.split()),
        source_url=f"https://{source_id}.test/article",
        title=title,
        position=position,
        source_id=source_id,
    )


def test_prefilter_keeps_relevant_chunks_and_limits_candidate_count():
    chunks = [
        _chunk("irrelevant filler", 1, 1),
        _chunk("oppenheimer born new york city", 1, 0),
        _chunk("another unrelated paragraph", 2, 1),
        _chunk("robert oppenheimer early life", 2, 0),
        _chunk("noise", 3, 0),
    ]

    result = CandidatePrefilterImpl(max_candidates=3).filter("Where was Oppenheimer born?", chunks)

    assert result.prefilter_strategy == PREFILTER_STRATEGY
    assert result.chunks_sent_to_reranker == 3
    assert result.chunks_prefiltered == 2
    assert any("born new york" in chunk.text for chunk in result.chunks)


def test_prefilter_preserves_at_least_one_chunk_per_source_when_possible():
    chunks = [
        _chunk("strong query match born oppenheimer new york", 1, 0),
        _chunk("another strong query match born oppenheimer", 1, 1),
        _chunk("weak but only source two", 2, 0),
        _chunk("weak but only source three", 3, 0),
    ]

    result = CandidatePrefilterImpl(max_candidates=3).filter("oppenheimer born", chunks)

    assert {chunk.source_id for chunk in result.chunks} == {1, 2, 3}


def test_prefilter_noops_when_chunk_count_is_under_limit():
    chunks = [_chunk("a", 1), _chunk("b", 2)]

    result = CandidatePrefilterImpl(max_candidates=5).filter("query", chunks)

    assert result.chunks == chunks
    assert result.chunks_prefiltered == 0
    assert result.chunks_sent_to_reranker == 2
