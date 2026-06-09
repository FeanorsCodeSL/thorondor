from chunking.base import ChunkResult


def test_chunkresult_len_and_repr():
    c = ChunkResult(text="hello world", start_index=0, end_index=11, token_count=2)
    assert len(c) == 11
    assert "tokens=2" in repr(c)
