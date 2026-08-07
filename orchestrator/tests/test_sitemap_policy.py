from orchestrator.sitemap_policy import parse_sitemap


def test_sitemap_urlset_orders_valid_signals_then_document_order():
    document = parse_sitemap(
        """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/plain</loc></url>
  <url><loc>https://example.com/old</loc><lastmod>2026-07-01</lastmod><priority>1.0</priority></url>
  <url><loc>https://example.com/new-low</loc><lastmod>2026-08-04</lastmod><priority>0.2</priority></url>
  <url><loc>https://example.com/new-high</loc><lastmod>2026-08-04</lastmod><priority>0.8</priority></url>
  <url><loc>https://example.com/priority-only</loc><priority>0.9</priority></url>
</urlset>""",
        max_bytes=4096,
        max_entries=10,
    )

    assert document.kind == "urlset"
    assert [entry.url for entry in document.entries] == [
        "https://example.com/new-high",
        "https://example.com/new-low",
        "https://example.com/old",
        "https://example.com/priority-only",
        "https://example.com/plain",
    ]


def test_sitemap_index_is_bounded_and_reports_truncation():
    document = parse_sitemap(
        """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/one.xml</loc></sitemap>
  <sitemap><loc>https://example.com/two.xml</loc></sitemap>
  <sitemap><loc>https://example.com/three.xml</loc></sitemap>
</sitemapindex>""",
        max_bytes=4096,
        max_entries=2,
    )

    assert document.kind == "index"
    assert [entry.url for entry in document.entries] == [
        "https://example.com/one.xml",
        "https://example.com/two.xml",
    ]
    assert document.truncated is True


def test_sitemap_rejects_oversized_malformed_and_dtd_documents():
    oversized = parse_sitemap("<urlset>" + "x" * 100, max_bytes=16, max_entries=10)
    malformed = parse_sitemap("<urlset><url>", max_bytes=1024, max_entries=10)
    dtd = parse_sitemap(
        "<!DOCTYPE x [<!ENTITY y SYSTEM 'file:///etc/passwd'>]><urlset/>",
        max_bytes=1024,
        max_entries=10,
    )

    assert oversized.reason == "body_too_large"
    assert malformed.reason == "malformed_xml"
    assert dtd.reason == "unsafe_xml"


def test_sitemap_invalid_dates_and_priorities_are_unsignalled():
    document = parse_sitemap(
        """<urlset>
  <url><loc>https://example.com/a</loc><lastmod>not-a-date</lastmod><priority>7</priority></url>
  <url><loc>https://example.com/b</loc></url>
</urlset>""",
        max_bytes=2048,
        max_entries=10,
    )

    assert [entry.url for entry in document.entries] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert document.entries[0].modified_at is None
    assert document.entries[0].priority is None


def test_sitemap_rejects_timezone_less_datetimes_but_accepts_dates():
    document = parse_sitemap(
        """<urlset>
  <url><loc>https://example.com/naive</loc><lastmod>2026-08-07T10:00:00</lastmod></url>
  <url><loc>https://example.com/date</loc><lastmod>2026-08-06</lastmod></url>
</urlset>""",
        max_bytes=2048,
        max_entries=10,
    )

    assert [entry.url for entry in document.entries] == [
        "https://example.com/date",
        "https://example.com/naive",
    ]
    assert document.entries[0].modified_at == "2026-08-06"
    assert document.entries[1].modified_at is None


def test_sitemap_accepts_chromium_xml_viewer_wrapper():
    document = parse_sitemap(
        """<html><body><div id="webkit-xml-viewer-source-xml">
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/from-browser</loc></url>
</urlset>
</div></body></html>""",
        max_bytes=4096,
        max_entries=10,
    )

    assert document.kind == "urlset"
    assert [entry.url for entry in document.entries] == [
        "https://example.com/from-browser"
    ]


def test_sitemap_recovers_xml_root_from_malformed_chromium_viewer_html():
    document = parse_sitemap(
        """<html><body><div id="webkit-xml-viewer-source-xml">
<urlset><url><loc>https://example.com/recovered</loc></url></urlset>
</div><div><span>viewer chrome</body></html>""",
        max_bytes=4096,
        max_entries=10,
    )

    assert document.kind == "urlset"
    assert [entry.url for entry in document.entries] == [
        "https://example.com/recovered"
    ]
