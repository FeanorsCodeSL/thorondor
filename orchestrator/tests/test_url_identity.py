import hashlib

import idna
import pytest
from pydantic import ValidationError

from orchestrator.normalize import normalize_url
from orchestrator.url_identity import (
    DEDUP_POLICY_VERSION,
    CacheIdentity,
    DocumentIdentity,
    UrlIdentity,
    build_cache_identity,
    build_document_identity,
    build_evidence_identity,
    build_url_identity,
    cleaned_markdown_sha256,
    dedup_key_for,
    document_id_for,
    evidence_id_for,
)


def test_redirect_identity_keeps_requested_final_display_and_safety_roles_separate():
    identity = build_url_identity(
        requested_url="https://search.example/redirect#request-fragment",
        final_url="https://www.example.test/articles/one?utm_source=search&tag=python#final-fragment",
        declared_canonical_url="https://canonical.example.test/articles/one",
    )

    assert identity.requested_url == "https://search.example/redirect#request-fragment"
    assert identity.final_url == "https://www.example.test/articles/one?utm_source=search&tag=python"
    assert identity.display_url == "https://www.example.test/articles/one?utm_source=search&tag=python#final-fragment"
    assert identity.safety_target == identity.final_url
    assert identity.dedup_key == "https://www.example.test/articles/one?tag=python"
    assert identity.declared_canonical_url == "https://canonical.example.test/articles/one"
    assert identity.dedup_policy_version == DEDUP_POLICY_VERSION


def test_fragments_remain_display_metadata_but_not_transport_or_dedup_identity():
    with_fragment = build_url_identity(
        requested_url="https://example.test/article#summary",
        final_url="https://example.test/article#summary",
    )
    without_fragment = build_url_identity(
        requested_url="https://example.test/article",
        final_url="https://example.test/article",
    )

    assert with_fragment.display_url != without_fragment.display_url
    assert with_fragment.final_url == without_fragment.final_url
    assert with_fragment.safety_target == without_fragment.safety_target
    assert with_fragment.dedup_key == without_fragment.dedup_key


def test_legacy_normalizer_remains_independent_from_the_formal_dedup_serializer():
    url = "HTTPS://Example.TEST/path/;section?UTM_Source=one&gclid=three&ref=four#fragment"

    assert normalize_url(url) == "https://example.test/path?gclid=three&ref=four"
    assert dedup_key_for(url) == "https://example.test/path/;section?gclid=three&ref=four"


def test_dedup_serializer_removes_only_utm_and_preserves_raw_query_components():
    url = (
        "HTTPS://Example.TEST/path/;section?keep=%2F&tag=&tag=one+two&&UTM_Source=ignored&gclid=three&ref=four"
        "&utmx=five&notutm_six=six&empty="
        "&%75tm_source=encoded-name&flag#fragment"
    )

    assert dedup_key_for(url) == (
        "https://example.test/path/;section?keep=%2F&tag=&tag=one+two&&gclid=three&ref=four&utmx=five"
        "&notutm_six=six&empty=&flag"
    )


def test_www_and_apex_hosts_have_distinct_dedup_keys():
    apex = build_url_identity("https://example.test/article", "https://example.test/article")
    www = build_url_identity("https://www.example.test/article", "https://www.example.test/article")

    assert apex.dedup_key != www.dedup_key


def test_unicode_host_is_displayed_as_fetched_but_deduped_as_idna():
    identity = build_url_identity(
        "https://bücher.example/Über",
        "https://bücher.example/Über?topic=identity",
    )

    assert identity.display_url == "https://bücher.example/Über?topic=identity"
    assert identity.safety_target == "https://bücher.example/Über?topic=identity"
    assert identity.dedup_key == "https://xn--bcher-kva.example/Über?topic=identity"


def test_dedup_serializer_uses_nontransitional_idna_without_merging_sharp_s_hosts():
    sharp_s = dedup_key_for("https://faß.de/article")
    plain_ss = dedup_key_for("https://fass.de/article")

    assert sharp_s == "https://xn--fa-hia.de/article"
    assert plain_ss == "https://fass.de/article"
    assert sharp_s != plain_ss


def test_dedup_serializer_rejects_invalid_unicode_hostname():
    with pytest.raises(idna.IDNAError):
        dedup_key_for("https://☃.example/article")


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        (
            "HTTP://[2001:0DB8:0:0:0:0:0:1]:80/path/;part?keep=one#fragment",
            "http://[2001:db8::1]/path/;part?keep=one",
        ),
        (
            "HTTPS://[2001:db8::1]:443/path/",
            "https://[2001:db8::1]/path/",
        ),
        (
            "https://[2001:db8::1]:444/path/",
            "https://[2001:db8::1]:444/path/",
        ),
    ),
)
def test_dedup_serializer_retains_bracketed_ipv6_and_only_elides_default_ports(url, expected):
    assert dedup_key_for(url) == expected


def test_dedup_serializer_preserves_userinfo_and_elides_default_https_port():
    with_userinfo = dedup_key_for("HTTPS://alice:pa%3Ass@Example.test:443/article#fragment")
    without_userinfo = dedup_key_for("https://example.test/article")

    assert with_userinfo == "https://alice:pa%3Ass@example.test/article"
    assert without_userinfo == "https://example.test/article"
    assert with_userinfo != without_userinfo


@pytest.mark.parametrize(
    "declared_canonical_url",
    (
        "https://source.example/rewritten-article",
        "https://different.example/rewritten-article",
    ),
)
def test_declared_canonical_is_provenance_only_for_same_and_cross_origin_urls(declared_canonical_url):
    with_canonical = build_url_identity(
        "https://source.example/article",
        "https://source.example/article?edition=web",
        declared_canonical_url=declared_canonical_url,
    )
    without_canonical = build_url_identity(
        "https://source.example/article",
        "https://source.example/article?edition=web",
    )

    assert with_canonical.final_url == "https://source.example/article?edition=web"
    assert with_canonical.safety_target == "https://source.example/article?edition=web"
    assert with_canonical.dedup_key == without_canonical.dedup_key
    assert with_canonical.declared_canonical_url == declared_canonical_url


def test_cache_identity_uses_dedup_key_and_both_retrieval_and_extraction_variants():
    identity = build_url_identity(
        "https://example.test/article",
        "https://example.test/article?view=full#summary",
    )
    browser_markdown = build_cache_identity(
        identity,
        retrieval_variant="browser-rendered-v1",
        extraction_variant="markdown-v1",
    )
    static_markdown = build_cache_identity(
        identity,
        retrieval_variant="static-http-v1",
        extraction_variant="markdown-v1",
    )
    browser_html = build_cache_identity(
        identity,
        retrieval_variant="browser-rendered-v1",
        extraction_variant="html-v1",
    )

    assert browser_markdown.dedup_key == "https://example.test/article?view=full"
    assert browser_markdown != static_markdown
    assert browser_markdown != browser_html
    assert "display_url" not in CacheIdentity.model_fields

    with pytest.raises(ValidationError):
        build_cache_identity(identity, retrieval_variant="", extraction_variant="markdown-v1")


def test_document_id_is_a_hash_of_fragment_free_final_url_and_full_exact_cleaned_markdown():
    final_url = "https://example.test/article#summary"
    markdown = ("shared preamble\n" * 200) + "first complete ending\r\n"
    document = build_document_identity(final_url, markdown)
    content_digest = hashlib.sha256(markdown.encode("utf-8")).digest()
    expected = hashlib.sha256(b"https://example.test/article\x00" + content_digest).hexdigest()

    assert document.final_url == "https://example.test/article"
    assert document.cleaned_markdown_sha256 == content_digest.hex()
    assert document.document_id == expected
    assert document_id_for(final_url, markdown) == expected
    assert set(DocumentIdentity.model_fields) == {"final_url", "cleaned_markdown_sha256", "document_id"}


def test_document_id_changes_for_content_after_the_legacy_prefix_and_exact_line_endings():
    final_url = "https://example.test/article"
    prefix = "x" * 2_000

    assert document_id_for(final_url, prefix + "first ending") != document_id_for(final_url, prefix + "second ending")
    assert document_id_for(final_url, "line one\nline two") != document_id_for(final_url, "line one\r\nline two")
    assert document_id_for(final_url, "same markdown") != document_id_for("https://example.test/other", "same markdown")


def test_document_identity_is_fragment_stable():
    markdown = "exact markdown"

    assert document_id_for("https://example.test/article#first", markdown) == document_id_for(
        "https://example.test/article#second", markdown
    )


def test_models_reject_extra_fields_type_coercion_and_inconsistent_derived_values():
    url_values = {
        "requested_url": "https://request.example/article",
        "final_url": "https://final.example/article",
        "display_url": "https://final.example/article#summary",
    }
    markdown = "exact markdown"
    digest = cleaned_markdown_sha256(markdown)
    document_id = document_id_for(url_values["final_url"], markdown)

    with pytest.raises(ValidationError):
        UrlIdentity(**url_values, safety_target="https://attacker.example/")
    with pytest.raises(ValidationError):
        UrlIdentity(**url_values, dedup_key="https://attacker.example/")
    with pytest.raises(ValidationError):
        UrlIdentity(**(url_values | {"display_url": "https://other.example/article"}))
    with pytest.raises(ValidationError):
        UrlIdentity(
            requested_url=url_values["requested_url"],
            final_url="https://final.example/article#summary",
            display_url="https://final.example/article#summary",
        )
    with pytest.raises(ValidationError):
        UrlIdentity(requested_url=1, final_url=url_values["final_url"], display_url=url_values["final_url"])

    with pytest.raises(ValidationError):
        CacheIdentity(dedup_key="https://example.test/article", retrieval_variant=1, extraction_variant="markdown-v1")
    with pytest.raises(ValidationError):
        CacheIdentity(
            dedup_key="https://example.test/article",
            retrieval_variant="browser-rendered-v1",
            extraction_variant="markdown-v1",
            unexpected="value",
        )

    valid_document = DocumentIdentity(
        final_url=url_values["final_url"],
        cleaned_markdown_sha256=digest,
        document_id=document_id,
    )
    assert valid_document.document_id == document_id
    with pytest.raises(ValidationError):
        DocumentIdentity(
            final_url=url_values["final_url"],
            cleaned_markdown_sha256="not-a-sha256",
            document_id=document_id,
        )
    with pytest.raises(ValidationError):
        DocumentIdentity(
            final_url=url_values["final_url"],
            cleaned_markdown_sha256=1,
            document_id=document_id,
        )
    with pytest.raises(ValidationError):
        DocumentIdentity(
            final_url=url_values["final_url"],
            cleaned_markdown_sha256=digest,
            document_id="0" * 64,
        )
    with pytest.raises(ValidationError):
        DocumentIdentity(
            final_url="https://final.example/article#summary",
            cleaned_markdown_sha256=digest,
            document_id=document_id,
        )
    with pytest.raises(ValidationError):
        DocumentIdentity(
            final_url=url_values["final_url"],
            cleaned_markdown_sha256=digest,
            document_id=document_id,
            unexpected="value",
        )


def test_models_are_frozen():
    identity = build_url_identity("https://request.example/article", "https://final.example/article#summary")
    cache_identity = build_cache_identity(identity, "browser-rendered-v1", "markdown-v1")
    document_identity = build_document_identity("https://final.example/article", "exact markdown")

    with pytest.raises(ValidationError):
        identity.final_url = "https://other.example/article"
    with pytest.raises(ValidationError):
        cache_identity.retrieval_variant = "static-http-v1"
    with pytest.raises(ValidationError):
        document_identity.document_id = "0" * 64


def test_evidence_identity_is_stable_across_result_order_and_backend_changes():
    document = "# Heading\n\nAlpha evidence.\n\nBeta evidence."
    first = build_evidence_identity("https://example.test/final#fragment", document, 11, 26)
    reordered = build_evidence_identity("https://example.test/final", document, 11, 26)

    assert first == reordered
    assert first.document_id == document_id_for("https://example.test/final", document)
    assert first.evidence_id == evidence_id_for(
        first.final_url,
        first.cleaned_markdown_sha256,
        11,
        26,
    )


def test_evidence_identity_changes_with_document_bytes_or_span_boundaries():
    original = build_evidence_identity("https://example.test/final", "Alpha Beta", 0, 5)
    changed_document = build_evidence_identity("https://example.test/final", "Alpha beta", 0, 5)
    changed_span = build_evidence_identity("https://example.test/final", "Alpha Beta", 0, 6)

    assert len({original.evidence_id, changed_document.evidence_id, changed_span.evidence_id}) == 3
