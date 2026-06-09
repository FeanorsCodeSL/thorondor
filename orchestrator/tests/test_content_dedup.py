from orchestrator.content_dedup import content_dedup
from orchestrator.types import Page


def test_whitespace_only_duplicates_collapse():
    pages = [Page("u1", "T1", "Hello   world"), Page("u2", "T2", "hello world")]
    assert content_dedup(pages) == [pages[0]]


def test_different_bodies_survive():
    pages = [Page("u1", "T1", "Hello world"), Page("u2", "T2", "Different")]
    assert content_dedup(pages) == pages
