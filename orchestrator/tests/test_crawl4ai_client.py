import anyio
import httpx
import ipaddress
import json
import time
import pytest

from orchestrator.clients.crawl4ai_client import (
    MAX_FINAL_URL_BYTES,
    MAX_JSON_DEPTH,
    MAX_LINK_BYTES,
    MAX_LINK_ITEMS,
    MAX_METADATA_BYTES,
    MAX_METADATA_ITEMS,
    MAX_RESPONSE_HEADER_BYTES,
    MAX_TITLE_BYTES,
    Crawl4aiExtractor,
)
from orchestrator.observability import reset_request_id, set_request_id
from orchestrator.url_safety import UrlSafetyPolicy, is_safe_crawl_url


def _extractor(**kwargs) -> Crawl4aiExtractor:
    policy = UrlSafetyPolicy(
        blocked_ip_categories={
            "loopback",
            "link_local",
            "private",
            "reserved",
            "multicast",
            "unspecified",
        },
        blocked_special_ips={
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("fd00:ec2::254"),
        },
        nat64_networks=[ipaddress.ip_network("64:ff9b::/96")],
        six_to_four_networks=[ipaddress.ip_network("2002::/16")],
        ipv4_compat_networks=[ipaddress.ip_network("::/96")],
    )
    values = {
        "base_url": "http://crawl4ai:11235",
        "concurrency": 1,
        "timeout_s": 1,
        "respect_robots_txt": True,
        "per_host_concurrency": 1,
        "crawler_user_agent": "ThorondorBot/1.0 (+https://example.test/contact)",
        "url_safety": lambda url: is_safe_crawl_url(url, policy),
    }
    values.update(kwargs)
    return Crawl4aiExtractor(**values)


def _utf8_text(byte_count: int) -> str:
    return "é" * (byte_count // 2) + ("x" if byte_count % 2 else "")


def _nested_json_mapping(depth: int, terminal: object) -> dict[str, object]:
    value = terminal
    for level in reversed(range(depth)):
        value = {f"depth-{level}": value}
    return value


def test_partial_failures_do_not_sink_batch(monkeypatch):
    def handler(req):
        payload = json.loads(req.read().decode())
        if "bad" in payload["urls"][0]:
            return httpx.Response(500, json={})
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": "Good"},
                        "markdown": {"fit_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        _extractor(concurrency=2).extract,
        ["https://good", "https://bad"],
    )

    assert len(out) == 1
    assert out[0].url == "https://good"


def test_uses_crawl4ai_docker_api_payload(monkeypatch):
    seen = []

    def handler(req):
        seen.append({"authorization": req.headers.get("authorization")})
        payload = json.loads(req.read().decode())
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": "Good"},
                        "markdown": {"raw_markdown": "content"},
                        "cleaned_html": "<article>content</article>",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        _extractor(
            api_key="crawl-secret",
            crawler_user_agent="ThorondorBot/9.4 (+https://example.test/contact)",
        ).extract,
        ["https://good"],
    )

    assert out and out[0].markdown == "content"
    assert out[0].html == "<article>content</article>"
    assert seen == [
        {"authorization": "Bearer crawl-secret"},
        {
            "urls": ["https://good"],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"user_agent": "ThorondorBot/9.4 (+https://example.test/contact)"},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"stream": False, "cache_mode": "bypass", "check_robots_txt": True},
            },
        }
    ]


def test_request_id_is_forwarded_to_crawl4ai(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        payload = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": "Good"},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-crawl")
    try:
        anyio.run(
            _extractor().extract,
            ["https://good"],
        )
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-crawl"


def test_can_disable_crawl4ai_robots_flag(monkeypatch):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://good",
                        "metadata": {"title": "Good"},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(
        _extractor(respect_robots_txt=False).extract,
        ["https://good"],
    )

    assert seen[0]["crawler_config"]["params"]["check_robots_txt"] is False


def test_unsafe_redirected_url_is_dropped(monkeypatch):
    seen = []

    async def url_safety(url):
        seen.append(url)
        return False

    def handler(req):
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://safe.example/start",
                        "redirected_url": "http://127.0.0.1/admin",
                        "markdown": {"raw_markdown": "internal secret"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        _extractor(url_safety=url_safety).extract,
        ["https://safe.example/start"],
    )

    assert out == []
    assert seen == ["http://127.0.0.1/admin"]


@pytest.mark.parametrize(
    ("item", "expected_final_url"),
    [
        (
            {
                "redirected_url": "https://redirect.example/final",
                "url": "https://item.example/final",
            },
            "https://redirect.example/final",
        ),
        (
            {"redirected_url": None, "url": "https://item.example/final"},
            "https://item.example/final",
        ),
        ({"redirected_url": None, "url": None}, "https://requested.example/start"),
    ],
)
def test_final_url_uses_redirected_url_then_item_url_then_requested_url(item, expected_final_url):
    seen = []
    extractor = _extractor(url_safety=lambda url: seen.append(url) or True)
    payload = {
        "success": True,
        "results": [{"success": True, **item, "markdown": {"raw_markdown": "content"}}],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert page.url == "https://requested.example/start"
    assert page.requested_url == "https://requested.example/start"
    assert page.final_url == expected_final_url
    assert anyio.run(extractor._has_unsafe_final_url, page.url, page.final_url) is False
    assert seen == ([] if expected_final_url == page.url else [expected_final_url])


def test_unsafe_item_url_is_dropped_after_final_url_revalidation():
    seen = []
    extractor = _extractor(url_safety=lambda url: seen.append(url) or False)
    payload = {
        "success": True,
        "results": [
            {
                "success": True,
                "url": "https://unsafe.example/final",
                "markdown": {"raw_markdown": "content"},
            }
        ],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert anyio.run(extractor._has_unsafe_final_url, page.url, page.final_url) is True
    assert seen == ["https://unsafe.example/final"]


def test_final_url_accepts_exact_utf8_byte_limit_and_revalidates():
    prefix = "https://final.example/"
    final_url = prefix + _utf8_text(MAX_FINAL_URL_BYTES - len(prefix.encode("utf-8")))
    seen = []
    extractor = _extractor(url_safety=lambda url: seen.append(url) or True)
    page = extractor._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "redirected_url": final_url,
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert len(final_url.encode("utf-8")) == MAX_FINAL_URL_BYTES
    assert page is not None
    assert page.final_url == final_url
    assert anyio.run(extractor._has_unsafe_final_url, page.url, page.final_url) is False
    assert seen == [final_url]


def test_oversized_final_url_does_not_return_a_page():
    prefix = "https://final.example/"
    final_url = prefix + _utf8_text(MAX_FINAL_URL_BYTES - len(prefix.encode("utf-8"))) + "x"
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "redirected_url": final_url,
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert len(final_url.encode("utf-8")) == MAX_FINAL_URL_BYTES + 1
    assert page is None


def test_final_url_that_cannot_be_encoded_as_utf8_does_not_return_a_page():
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "redirected_url": "https://final.example/\ud800",
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is None


@pytest.mark.parametrize(
    ("header_name", "attribute"),
    [
        ("Content-Type", "content_type"),
        ("ETag", "etag"),
        ("Last-Modified", "last_modified"),
    ],
)
def test_allowlisted_response_headers_accept_exact_utf8_byte_limit(header_name, attribute):
    value = _utf8_text(MAX_RESPONSE_HEADER_BYTES)
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "response_headers": {header_name: value},
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert len(value.encode("utf-8")) == MAX_RESPONSE_HEADER_BYTES
    assert page is not None
    assert getattr(page, attribute) == value


@pytest.mark.parametrize(
    ("header_name", "attribute"),
    [
        ("Content-Type", "content_type"),
        ("ETag", "etag"),
        ("Last-Modified", "last_modified"),
    ],
)
def test_allowlisted_response_headers_omit_utf8_overflow(header_name, attribute):
    value = _utf8_text(MAX_RESPONSE_HEADER_BYTES) + "x"
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "response_headers": {header_name: value},
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert len(value.encode("utf-8")) == MAX_RESPONSE_HEADER_BYTES + 1
    assert page is not None
    assert getattr(page, attribute) is None


@pytest.mark.parametrize(
    ("canonical_header", "fallback_header", "attribute"),
    [
        ("CONTENT-TYPE", "content-type", "content_type"),
        ("ETAG", "etag", "etag"),
        ("LAST-MODIFIED", "last-modified", "last_modified"),
    ],
)
@pytest.mark.parametrize(
    "canonical_value",
    (_utf8_text(MAX_RESPONSE_HEADER_BYTES) + "x", "\ud800"),
    ids=("overflow", "invalid_utf8"),
)
def test_allowlisted_response_headers_do_not_fall_back_from_invalid_canonical_variant(
    canonical_header, fallback_header, attribute, canonical_value
):
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "response_headers": {
                        canonical_header: canonical_value,
                        fallback_header: "fallback",
                    },
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is not None
    assert getattr(page, attribute) is None


def test_allowlisted_response_headers_reject_unicode_casefold_lookalike():
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "response_headers": {"la\u017ft-modified": "untrusted"},
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is not None
    assert page.content_type is None
    assert page.etag is None
    assert page.last_modified is None


def test_allowlisted_response_headers_select_lexicographically_first_case_variant():
    extractor = _extractor(url_safety=lambda _url: True)
    payload = {
        "success": True,
        "results": [
            {
                "success": True,
                "status_code": 203,
                "response_headers": {
                    "content-type": "lower-content-type",
                    "Content-Type": "title-content-type",
                    "CONTENT-TYPE": "upper-content-type",
                    "etag": "lower-etag",
                    "ETag": "title-etag",
                    "ETAG": "upper-etag",
                    "last-modified": "lower-last-modified",
                    "Last-Modified": "title-last-modified",
                    "LAST-MODIFIED": "upper-last-modified",
                    "set-cookie": "secret-cookie",
                    "x-internal-token": "secret-token",
                },
                "markdown": {"raw_markdown": "content"},
            }
        ],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert page.status_code == 203
    assert page.content_type == "upper-content-type"
    assert page.etag == "upper-etag"
    assert page.last_modified == "upper-last-modified"
    assert not hasattr(page, "response_headers")
    assert "secret-cookie" not in page.__dict__.values()
    assert "secret-token" not in page.__dict__.values()


def test_malformed_fetch_provenance_is_ignored_without_affecting_page_content():
    extractor = _extractor(url_safety=lambda _url: True)
    payload = {
        "success": True,
        "results": [
            {
                "success": True,
                "redirected_url": 7,
                "url": [],
                "status_code": True,
                "response_headers": ["not", "headers"],
                "metadata": ["not", "metadata"],
                "links": "not links",
                "title": ["not", "a title"],
                "markdown": {"raw_markdown": "content"},
            }
        ],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert page.title == "https://requested.example/start"
    assert page.markdown == "content"
    assert page.requested_url == "https://requested.example/start"
    assert page.final_url == "https://requested.example/start"
    assert page.status_code is None
    assert page.content_type is None
    assert page.etag is None
    assert page.last_modified is None
    assert page.metadata == {}
    assert page.links == {}


@pytest.mark.parametrize("title_source", ("item", "metadata", "source"))
def test_page_title_preserves_utf8_boundary_and_precedence(title_source):
    boundary_title = _utf8_text(MAX_TITLE_BYTES)
    source_url = "https://requested.example/start"
    item = {"success": True, "markdown": {"raw_markdown": "content"}}

    if title_source == "item":
        item["title"] = boundary_title
        item["metadata"] = {"title": "metadata fallback"}
        expected_title = boundary_title
    elif title_source == "metadata":
        item["metadata"] = {"title": boundary_title}
        expected_title = boundary_title
    else:
        source_prefix = "https://requested.example/"
        source_url = source_prefix + _utf8_text(MAX_TITLE_BYTES - len(source_prefix.encode("utf-8")))
        expected_title = source_url

    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        source_url,
        {"success": True, "results": [item]},
    )

    assert page is not None
    assert page.title == expected_title
    assert len(page.title.encode("utf-8")) == MAX_TITLE_BYTES


@pytest.mark.parametrize("title_source", ("item", "metadata", "source"))
def test_page_title_truncates_utf8_overflow_without_changing_precedence(title_source):
    boundary_title = _utf8_text(MAX_TITLE_BYTES)
    source_url = "https://requested.example/start"
    item = {"success": True, "markdown": {"raw_markdown": "content"}}

    if title_source == "item":
        item["title"] = boundary_title + "🙂"
        item["metadata"] = {"title": "metadata fallback"}
        expected_title = boundary_title
    elif title_source == "metadata":
        item["metadata"] = {"title": boundary_title + "🙂"}
        expected_title = boundary_title
    else:
        source_prefix = "https://requested.example/"
        source_url = source_prefix + _utf8_text(MAX_TITLE_BYTES - len(source_prefix.encode("utf-8"))) + "🙂"
        expected_title = source_url[:-1]

    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        source_url,
        {"success": True, "results": [item]},
    )

    assert page is not None
    assert page.url == source_url
    assert page.title == expected_title
    assert len(page.title.encode("utf-8")) == MAX_TITLE_BYTES


def test_oversized_metadata_title_is_not_retained_or_unbounded():
    oversized_title = _utf8_text(MAX_METADATA_BYTES + 2)
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    "metadata": {"title": oversized_title},
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is not None
    assert page.metadata == {}
    assert page.title == _utf8_text(MAX_TITLE_BYTES)
    assert len(page.title.encode("utf-8")) == MAX_TITLE_BYTES


def test_metadata_and_links_nested_item_limits_are_exact():
    extractor = _extractor(url_safety=lambda _url: True)
    payload = {
        "success": True,
        "results": [
            {
                "success": True,
                "metadata": {
                    "group": {f"item-{index:02d}": index for index in reversed(range(MAX_METADATA_ITEMS))}
                },
                "links": {"group": list(range(MAX_LINK_ITEMS))},
                "markdown": {"raw_markdown": "content"},
            }
        ],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert page.metadata == {
        "group": {f"item-{index:02d}": index for index in range(MAX_METADATA_ITEMS - 1)}
    }
    assert page.links == {"group": list(range(MAX_LINK_ITEMS - 1))}


@pytest.mark.parametrize("field", ("metadata", "links"))
def test_metadata_and_links_filter_nested_json_in_deterministic_mapping_order(field):
    upstream_value = {
        "z": "outer-last",
        "nested": {
            "z": "nested-last",
            "list": [
                "first",
                float("nan"),
                {"z": "inner-last", "drop": object(), "a": "inner-first"},
                ["retained", object()],
                float("inf"),
            ],
            "nan": float("nan"),
            "negative_infinity": float("-inf"),
            "finite": 1.5,
            "object": object(),
            7: "ignored-key",
        },
        "a": "outer-first",
        1: "ignored-key",
    }
    expected = {
        "a": "outer-first",
        "nested": {
            "finite": 1.5,
            "list": ["first", {"a": "inner-first", "z": "inner-last"}, ["retained"]],
            "z": "nested-last",
        },
        "z": "outer-last",
    }

    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    field: upstream_value,
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is not None
    retained = getattr(page, field)
    assert retained == expected
    assert list(retained) == ["a", "nested", "z"]
    assert list(retained["nested"]) == ["finite", "list", "z"]
    assert list(retained["nested"]["list"][1]) == ["a", "z"]
    assert json.loads(json.dumps(retained, allow_nan=False)) == expected


@pytest.mark.parametrize("field", ("metadata", "links"))
def test_metadata_and_links_retain_max_json_depth_and_drop_deeper_branch(field):
    at_limit = _nested_json_mapping(MAX_JSON_DEPTH, "retained")
    beyond_limit = _nested_json_mapping(MAX_JSON_DEPTH + 1, "dropped")
    page = _extractor(url_safety=lambda _url: True)._page_from_payload(
        "https://requested.example/start",
        {
            "success": True,
            "results": [
                {
                    "success": True,
                    field: {"within": at_limit, "outside": beyond_limit},
                    "markdown": {"raw_markdown": "content"},
                }
            ],
        },
    )

    assert page is not None
    retained = getattr(page, field)
    assert retained == {
        "outside": _nested_json_mapping(MAX_JSON_DEPTH - 1, {}),
        "within": at_limit,
    }
    assert json.loads(json.dumps(retained, allow_nan=False, sort_keys=True)) == retained
    at_limit_value = retained["within"]
    beyond_limit_value = retained["outside"]
    for level in range(MAX_JSON_DEPTH):
        at_limit_value = at_limit_value[f"depth-{level}"]
    for level in range(MAX_JSON_DEPTH - 1):
        beyond_limit_value = beyond_limit_value[f"depth-{level}"]
    assert at_limit_value == "retained"
    assert beyond_limit_value == {}
    assert f"depth-{MAX_JSON_DEPTH - 1}" not in beyond_limit_value


@pytest.mark.parametrize(
    ("field", "max_bytes"),
    [("metadata", MAX_METADATA_BYTES), ("links", MAX_LINK_BYTES)],
)
def test_metadata_and_links_byte_limits_use_utf8_without_logging_discarded_content(field, max_bytes, caplog):
    value = "é" * ((max_bytes - len(b'{"a":""}')) // 2)
    discarded_value = f"{field}-discarded-secret"
    field_value = {"z_discarded": discarded_value, "a": value}
    extractor = _extractor(url_safety=lambda _url: True)
    payload = {
        "success": True,
        "results": [{"success": True, field: field_value, "markdown": {"raw_markdown": "content"}}],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    retained = getattr(page, field)
    assert retained == {"a": value}
    assert len(value) < max_bytes
    assert len(json.dumps(retained, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) == max_bytes
    assert discarded_value not in caplog.text


def test_old_payload_keeps_existing_page_behavior_with_additive_provenance_defaults():
    extractor = _extractor(url_safety=lambda _url: True)
    payload = {
        "success": True,
        "results": [
            {
                "success": True,
                "url": "https://requested.example/start",
                "metadata": {"title": "Good"},
                "markdown": {"raw_markdown": "content"},
                "cleaned_html": "<article>content</article>",
            }
        ],
    }

    page = extractor._page_from_payload("https://requested.example/start", payload)

    assert page is not None
    assert page.url == "https://requested.example/start"
    assert page.title == "Good"
    assert page.markdown == "content"
    assert page.html == "<article>content</article>"
    assert page.requested_url == "https://requested.example/start"
    assert page.final_url == "https://requested.example/start"
    assert page.status_code is None
    assert page.content_type is None
    assert page.etag is None
    assert page.last_modified is None
    assert page.metadata == {"title": "Good"}
    assert page.links == {}


def test_posts_original_url_without_target_site_preflight(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.method)
        assert req.method == "POST"
        payload = json.loads(req.content)
        assert payload["urls"] == ["https://safe.example/start"]
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://safe.example/start",
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(
        _extractor().extract,
        ["https://safe.example/start"],
    )

    assert [page.url for page in out] == ["https://safe.example/start"]
    assert seen == ["POST"]


def test_extract_returns_completed_pages_within_overall_deadline(monkeypatch):
    async def handler(req):
        payload = json.loads(req.content)
        url = payload["urls"][0]
        if "slow" in url:
            await anyio.sleep(1)
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": url,
                        "metadata": {"title": url},
                        "markdown": {"raw_markdown": f"content {url}"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    started = time.perf_counter()
    out = anyio.run(
        _extractor(concurrency=2, timeout_s=0.05).extract,
        ["https://fast.example", "https://slow.example"],
    )

    assert time.perf_counter() - started < 0.5
    assert [page.url for page in out] == ["https://fast.example"]


def test_concurrency_is_bounded(monkeypatch):
    active = 0
    peak = 0

    async def exercise():
        two_active = anyio.Event()

        async def handler(req):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
            with anyio.fail_after(1):
                await two_active.wait()
            active -= 1
            return httpx.Response(200, json={"title": "T", "markdown": "content"})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **k: real_async_client(transport=transport),
        )

        await _extractor(concurrency=2).extract(
            ["https://a.test", "https://b.test", "https://c.test"]
        )

    anyio.run(exercise)

    assert peak == 2


def test_per_host_concurrency_is_bounded(monkeypatch):
    active_by_host = {}
    peak_by_host = {}

    async def handler(req):
        payload = json.loads(req.content)
        host = payload["urls"][0].split("/")[2]
        active_by_host[host] = active_by_host.get(host, 0) + 1
        peak_by_host[host] = max(peak_by_host.get(host, 0), active_by_host[host])
        await anyio.sleep(0.01)
        active_by_host[host] -= 1
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": payload["urls"][0],
                        "metadata": {"title": host},
                        "markdown": {"raw_markdown": "content"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(
        _extractor(concurrency=3).extract,
        ["https://a.test/1", "https://a.test/2", "https://b.test/1"],
    )

    assert peak_by_host["a.test"] == 1
