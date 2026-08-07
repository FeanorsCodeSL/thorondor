import pytest

from orchestrator.markdown_cleaner import MarkdownCleanerImpl
from orchestrator.types import Page


def _cleaner(calls):
    def extract(html: str, **kwargs):
        calls.append({"html": html, "kwargs": kwargs})
        return "# Article\n\nThe useful article body remains."

    return MarkdownCleanerImpl(
        extractor=extract,
        extractor_name="trafilatura",
        extractor_version="2.2.0",
        favor_recall=True,
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )


def test_cleaner_uses_configured_html_extractor_and_preserves_original_markdown():
    calls = []
    page = Page(
        "https://example.test/article",
        "Article",
        "Navigation Menu\n# Article\nThe generated markdown body.",
        html="<html><nav>Navigation Menu</nav><article>The useful article body remains.</article></html>",
    )

    cleaned = _cleaner(calls).clean(page)

    assert cleaned.cleaner_version == "trafilatura@2.2.0"
    assert cleaned.page.markdown == "# Article\n\nThe useful article body remains."
    assert cleaned.page.original_markdown == page.markdown
    assert cleaned.blocks_dropped == 2
    assert calls == [
        {
            "html": page.html,
            "kwargs": {
                "url": page.url,
                "output_format": "markdown",
                "favor_recall": True,
                "include_comments": False,
                "include_tables": True,
                "deduplicate": True,
            },
        }
    ]


def test_markdown_only_pages_are_passed_through_without_site_rules():
    calls = []
    page = Page(
        "https://example.test/article",
        "Article",
        "Navigation Menu\n# Article\nThe generated markdown body.",
    )

    cleaned = _cleaner(calls).clean(page)

    assert cleaned.page.markdown == page.markdown
    assert cleaned.page.original_markdown == page.markdown
    assert calls == []
    assert cleaned.blocks_dropped == 0


@pytest.mark.parametrize("failure", (None, RuntimeError("extract failed")))
def test_cleaner_falls_back_to_crawl4ai_markdown_when_html_extraction_fails(failure):
    def extract(_html, **_kwargs):
        if failure is not None:
            raise failure
        return None

    cleaner = MarkdownCleanerImpl(
        extractor=extract,
        extractor_name="trafilatura",
        extractor_version="2.2.0",
        favor_recall=True,
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    page = Page(
        "https://example.test/article",
        "Article",
        "# Crawl4AI fallback\n\nUseful body.",
        html="<article>Useful body.</article>",
    )

    cleaned = cleaner.clean(page)

    assert cleaned.page.markdown == page.markdown
    assert cleaned.page.original_markdown == page.markdown
    assert cleaned.blocks_dropped == 0


def test_removed_line_count_does_not_hide_removal_when_cleaner_wraps_more_lines():
    cleaner = MarkdownCleanerImpl(
        extractor=lambda html, **kwargs: "Useful answer\ncontinued detail\nadditional context",
        extractor_name="trafilatura",
        extractor_version="2.2.0",
        favor_recall=True,
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    page = Page(
        "https://example.test/article",
        "Article",
        "Navigation Menu\nUseful answer continued detail",
        html="<article>Useful answer continued detail</article>",
    )

    cleaned = cleaner.clean(page)

    assert cleaned.blocks_dropped == 2
