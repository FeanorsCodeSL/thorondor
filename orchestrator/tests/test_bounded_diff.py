from orchestrator.diffing import bounded_diff


def test_diff_handles_empty_removed_and_crlf_content():
    new = bounded_diff(None, "A\r\nB", 100, 1000, 10)
    removed = bounded_diff("A\nB", None, 100, 1000, 10)

    assert new.added_lines == ["A", "B"]
    assert new.removed_lines == []
    assert removed.added_lines == []
    assert removed.removed_lines == ["A", "B"]


def test_diff_returns_summary_only_above_input_or_complexity_caps():
    line_limited = bounded_diff("A\nB\nC", "D", 2, 1000, 10)
    complexity_limited = bounded_diff("A\nB", "C\nD", 10, 3, 10)

    assert line_limited.truncated is True
    assert line_limited.added_lines == []
    assert line_limited.removed_lines == []
    assert complexity_limited.truncated is True
    assert complexity_limited.added_lines == []
    assert complexity_limited.removed_lines == []


def test_diff_reports_changed_markdown_sections():
    result = bounded_diff(
        "# Product\n\n## Stock\n\nOut of stock\n\n## Shipping\n\nTomorrow",
        "# Product\n\n## Stock\n\nIn stock\n\n## Shipping\n\nTomorrow",
        100,
        10_000,
        10,
    )

    assert result.changed_sections == ["Stock"]


def test_diff_returns_none_for_equal_content():
    assert bounded_diff("Same", "Same", 100, 1000, 10) is None


def test_diff_truncates_output_without_exceeding_item_cap():
    result = bounded_diff(
        "\n".join(f"old-{index}" for index in range(20)),
        "\n".join(f"new-{index}" for index in range(20)),
        100,
        10_000,
        4,
    )

    assert result.truncated is True
    assert len(result.added_lines) == 4
    assert len(result.removed_lines) == 4


def test_diff_rejects_realistic_large_document_before_matching():
    previous = "\n".join(f"old-{index}" for index in range(2001))
    current = "\n".join(f"new-{index}" for index in range(2001))

    result = bounded_diff(previous, current, 2000, 1_000_000, 24)

    assert result.truncated is True
    assert result.previous_lines == result.current_lines == 2001
    assert result.added_lines == []
    assert result.removed_lines == []
