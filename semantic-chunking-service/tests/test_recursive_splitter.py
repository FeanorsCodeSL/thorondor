from chunking.recursive_splitter import RecursiveCharacterTextSplitter


def test_empty_or_whitespace_returns_empty():
    assert RecursiveCharacterTextSplitter().split_text("") == []
    assert RecursiveCharacterTextSplitter().split_text("   \n  ") == []


def test_paragraphs_become_separate_segments():
    s = RecursiveCharacterTextSplitter(chunk_size=5)
    out = s.split_text("alpha beta gamma\n\ndelta epsilon zeta")
    assert len(out) >= 2
    # round-trips the source words (separators are kept)
    assert "".join(out).split() == "alpha beta gamma delta epsilon zeta".split()


def test_oversized_token_runs_force_split_by_size():
    s = RecursiveCharacterTextSplitter(chunk_size=3)
    out = s.split_text("one two three four five six seven eight")
    assert all(len(seg.split()) <= 3 for seg in out)


def test_word_with_no_separators_is_char_split():
    s = RecursiveCharacterTextSplitter(chunk_size=2)
    out = s.split_text("x" * 50)   # no separators present
    assert out and "".join(out) == "x" * 50


def test_span_splitter_uses_contiguous_source_offsets_for_repeated_unicode_crlf_text():
    text = "Repeat e\u0301.\r\n\r\nRepeat e\u0301.\r\n\r\nFinal 東京."
    spans = RecursiveCharacterTextSplitter(chunk_size=2).split_text_with_spans(text)

    assert spans
    assert "".join(segment for segment, _, _ in spans) == text
    for segment, start, end in spans:
        assert segment == text[start:end]
