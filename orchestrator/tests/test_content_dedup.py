from orchestrator.content_dedup import content_dedup
from orchestrator.types import Page


def test_whitespace_only_duplicates_collapse():
    pages = [Page("u1", "T1", "Hello   world"), Page("u2", "T2", "Hello world")]
    assert content_dedup(pages) == [pages[0]]


def test_different_bodies_survive():
    pages = [Page("u1", "T1", "Hello world"), Page("u2", "T2", "Different")]
    assert content_dedup(pages) == pages


def test_long_shared_preamble_with_different_bodies_survives():
    preamble = "shared " * 400
    pages = [
        Page("u1", "T1", f"{preamble}first body"),
        Page("u2", "T2", f"{preamble}second body"),
    ]

    assert content_dedup(pages) == pages


def test_exact_content_dedup_ignores_canonical_disagreement():
    pages = [
        Page("https://a.test", "T1", "Same\n\nbody", final_url="https://a.test/canonical"),
        Page("https://b.test", "T2", "Same body", final_url="https://b.test/canonical"),
    ]

    assert content_dedup(pages) == [pages[0]]
