import pytest

from orchestrator.evidence_metadata import extract_document_metadata, normalize_timestamp
from orchestrator.types import Page


def _selected(metadata):
    return {candidate.field: candidate for candidate in metadata.selected}


def test_extracts_bounded_metadata_with_sources_and_preserves_conflicts():
    html = """
    <html lang="en-GB">
      <head>
        <title>HTML title</title>
        <meta name="description" content="HTML description">
        <meta property="article:published_time" content="2026-08-04T12:15:00+02:00">
        <meta property="article:modified_time" content="2026-08-05T09:30:00+02:00">
        <link rel="canonical" href="https://example.test/html-canonical">
        <script type="application/ld+json">
          {
            "@type": "Article",
            "headline": "JSON-LD title",
            "description": "JSON-LD description",
            "datePublished": "2026-08-04T09:45:00Z",
            "dateModified": "2026-08-05T07:45:00Z",
            "author": {"name": "Ada Author"},
            "inLanguage": "en",
            "mainEntityOfPage": {"@id": "https://example.test/jsonld-canonical"}
          }
        </script>
      </head>
    </html>
    """
    page = Page(
        "https://example.test/requested",
        "Fetch title",
        "# Evidence\n\nBody",
        html=html,
        final_url="https://example.test/final",
        metadata={
            "title": "Crawl title",
            "published_time": "2026-08-04T08:00:00Z",
            "canonical": "https://example.test/crawl-canonical",
        },
        discovery_published_at="2026-08-04T07:30:00Z",
    )

    metadata = extract_document_metadata(page)
    selected = _selected(metadata)

    assert selected["title"].value == "HTML title"
    assert selected["title"].source == "html_title"
    assert selected["description"].value == "HTML description"
    assert selected["published_at"].value == "2026-08-04T09:45:00Z"
    assert selected["published_at"].source == "json_ld"
    assert selected["modified_at"].value == "2026-08-05T07:45:00Z"
    assert selected["author"].value == "Ada Author"
    assert selected["language"].value == "en-gb"
    assert selected["declared_canonical_url"].value == "https://example.test/html-canonical"
    conflicts = {conflict.field: conflict for conflict in metadata.conflicts}
    assert {"title", "description", "published_at", "modified_at", "language", "declared_canonical_url"} <= set(conflicts)
    assert {candidate.source for candidate in conflicts["published_at"].candidates} == {
        "html_meta",
        "json_ld",
        "page_metadata",
        "discovery",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-04T12:15:00+02:00", "2026-08-04T10:15:00Z"),
        ("2026-08-04T10:15:00Z", "2026-08-04T10:15:00Z"),
        ("Tue, 04 Aug 2026 10:15:00 GMT", "2026-08-04T10:15:00Z"),
        ("2026-08-04", "2026-08-04T00:00:00Z"),
        ("2026-08-04T10:15:00", None),
        ("not-a-date", None),
        (None, None),
    ],
)
def test_timestamp_normalization_is_timezone_explicit_and_fail_closed(value, expected):
    assert normalize_timestamp(value) == expected


def test_sitemap_lastmod_is_modification_only_and_malformed_publication_is_ignored():
    page = Page(
        "https://example.test/article",
        "Article",
        "Body",
        metadata={"published_time": "not-a-date", "modified_time": "also-not-a-date"},
    )

    metadata = extract_document_metadata(page, sitemap_lastmod="2026-08-04")
    selected = _selected(metadata)

    assert "published_at" not in selected
    assert selected["modified_at"].value == "2026-08-04T00:00:00Z"
    assert selected["modified_at"].source == "sitemap"


def test_missing_metadata_retains_only_traceable_fetch_title():
    metadata = extract_document_metadata(Page("https://example.test", "Title", "Body"))

    assert metadata.selected == (
        metadata.get("title"),
    )
    assert metadata.get("title").source == "fetch_title"
    assert metadata.conflicts == ()


def test_relative_declared_canonical_uses_the_effective_page_url():
    page = Page(
        "https://example.test/requested",
        "Title",
        "Body",
        final_url="https://www.example.test/articles/current",
        html='<link rel="canonical" href="../canonical">',
    )

    metadata = extract_document_metadata(page)

    assert metadata.get("declared_canonical_url").value == (
        "https://www.example.test/canonical"
    )
    assert metadata.get("declared_canonical_url").source == "html_canonical"


def test_deeply_nested_json_ld_is_ignored_without_failing_metadata_extraction():
    nested = "[" * 30_000 + "]" * 30_000
    page = Page(
        "https://example.test/article",
        "Title",
        "Body",
        html=f'<script type="application/ld+json">{nested}</script>',
    )

    metadata = extract_document_metadata(page)

    assert metadata.selected == (metadata.get("title"),)
    assert metadata.conflicts == ()


def test_canonical_is_dropped_when_relative_resolution_exceeds_the_wire_limit():
    page = Page(
        "https://example.test/article",
        "Title",
        "Body",
        html=f'<link rel="canonical" href="{"b" * 8_190}">',
    )

    metadata = extract_document_metadata(page)

    assert metadata.get("declared_canonical_url") is None
