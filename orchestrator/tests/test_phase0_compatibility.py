import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree

from orchestrator.models import SearchRequest, SearchResponse, SearchStats, UnresponsiveEngine, UrlDiagnostic


TEST_ROOT = Path(__file__).parent
FIXTURE_ROOT = TEST_ROOT / "fixtures" / "web_intelligence"
COMPATIBILITY_CONTRACT = TEST_ROOT / "golden" / "search-v1-compatibility-contract.json"

EXPECTED_FIXTURE_FILES = {
    "canonical-conflicts.html",
    "challenge-shell.html",
    "crawl4ai-metadata-result.json",
    "document-metadata.json",
    "duplicate-preamble-a.md",
    "duplicate-preamble-b.md",
    "duplicate-preamble-urls.json",
    "javascript-shell.html",
    "malformed-upstream-payloads.json",
    "query-variants.json",
    "redirect-metadata.json",
    "robots-groups-and-states.json",
    "searxng-plural-result.json",
    "sitemap-index.xml",
    "static-page.html",
    "status-validator-transitions.json",
    "unicode-crlf-markdown.json",
}

EXPECTED_COVERAGE = {
    "canonical_conflicts",
    "challenge_shell",
    "crawl4ai_final_status_headers_links_metadata",
    "javascript_shell",
    "malformed_upstream_payloads",
    "minimal_pdf_document_metadata",
    "query_variants_repeated_keys",
    "redirect_metadata",
    "robots_4xx_5xx_state_descriptors",
    "robots_groups",
    "searxng_plural_engines_positions_published_date",
    "shared_long_preamble_distinct_bodies",
    "shared_long_preamble_distinct_urls",
    "sitemap_index",
    "static_html",
    "status_etag_last_modified_transitions",
    "unicode_crlf_markdown",
}

EXPECTED_REQUEST_ENUM_VALUES = {
    "search_profile": ["quick", "research", "deep"],
    "freshness": ["day", "week", "month", "year"],
}

EXPECTED_REQUIRED_RESPONSE_FIELDS = {
    "SearchResponse": {
        "query": "string",
        "passages": "array",
        "citations": "array",
        "stats": "object",
    },
    "Passage": {
        "text": "string",
        "score": "number",
        "token_count": "integer",
        "citation_id": "integer",
    },
    "Citation": {
        "id": "integer",
        "url": "string",
        "title": "string",
    },
    "UrlDiagnostic": {
        "url": "string",
        "title": "string",
        "engine": "string",
        "discovery_score": "number",
        "selected": "boolean",
    },
    "RawMarkdown": {
        "citation_id": "integer",
        "markdown": "string",
    },
    "UnresponsiveEngine": {
        "engine": "string",
        "reason": "string",
    },
}

EXPECTED_RESPONSE_ENUM_VALUES = {
    "passages.0.provenance": ["external_web"],
    "passages.0.trust": ["untrusted"],
    "stats.discovery_status": ["ok", "degraded", "unavailable"],
    "stats.reason": [
        "search_provider_unavailable",
        "no_results_from_discovery",
        "no_urls_after_selection",
        "all_crawls_failed",
        "no_chunks_after_dedup",
        "no_chunks_after_rerank",
    ],
    "schema_version": ["thorondor.search.v1"],
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_fragment(schema: dict, name: str) -> dict:
    if name == "SearchResponse":
        return schema
    return schema["$defs"][name]


def _property_type(schema: dict, property_schema: dict) -> str | None:
    reference = property_schema.get("$ref")
    if reference:
        definition = reference.rsplit("/", 1)[1]
        return schema["$defs"][definition].get("type")
    return property_schema.get("type")


def _set_path(payload: dict, path: str, value: object) -> None:
    target = payload
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if part.isdigit() else target[part]
    final_part = parts[-1]
    if final_part.isdigit():
        target[int(final_part)] = value
    else:
        target[final_part] = value


def _value_at_path(payload: dict, path: str) -> object:
    value = payload
    for part in path.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    return value


def _legacy_projection(value: object, legacy_shape: object) -> object:
    if isinstance(legacy_shape, dict):
        assert isinstance(value, dict)
        return {
            key: _legacy_projection(value[key], legacy_value)
            for key, legacy_value in legacy_shape.items()
        }
    if isinstance(legacy_shape, list):
        assert isinstance(value, list)
        assert len(value) == len(legacy_shape)
        return [
            _legacy_projection(item, legacy_item)
            for item, legacy_item in zip(value, legacy_shape, strict=True)
        ]
    assert type(value) is type(legacy_shape)
    return value


def test_v1_compatibility_contract_pins_the_complete_monotonic_floor():
    contract = _load_json(COMPATIBILITY_CONTRACT)

    assert contract["contract_revision"] == "thorondor.search.v1-compatibility-v1"
    assert contract["request_enum_values"] == EXPECTED_REQUEST_ENUM_VALUES
    assert contract["response_required_fields"] == EXPECTED_REQUIRED_RESPONSE_FIELDS
    assert contract["response_enum_values"] == EXPECTED_RESPONSE_ENUM_VALUES


def test_v1_request_examples_remain_valid():
    contract = _load_json(COMPATIBILITY_CONTRACT)

    for example in contract["request_examples"]:
        request = SearchRequest.model_validate(example["input"])
        assert _legacy_projection(request.model_dump(mode="json"), example["normalized"]) == example["normalized"], example["name"]


def test_v1_request_enum_values_remain_accepted():
    contract = _load_json(COMPATIBILITY_CONTRACT)

    for field, values in contract["request_enum_values"].items():
        for value in values:
            request = SearchRequest.model_validate({**contract["request_baseline"], field: value})
            assert getattr(request, field) == value


def test_v1_required_response_fields_remain_required_with_baseline_types():
    contract = _load_json(COMPATIBILITY_CONTRACT)
    schema = SearchResponse.model_json_schema()

    for name, fields in contract["response_required_fields"].items():
        fragment = _schema_fragment(schema, name)
        required = set(fragment["required"])
        for field, expected_type in fields.items():
            assert field in required
            assert _property_type(schema, fragment["properties"][field]) == expected_type


def test_v1_preexisting_response_fields_are_not_narrowed_by_output_envelopes():
    diagnostics = [
        UrlDiagnostic(
            url="https://example.test/" + ("x" * 3000),
            title="t" * 700,
            engine="e" * 200,
            discovery_score=1.0,
            selected=False,
            selection_reason="s" * 100,
            filtered_reason="f" * 100,
        )
        for _ in range(51)
    ]

    stats = SearchStats(
        sub_queries=["q"] * 9,
        unresponsive_engines=[UnresponsiveEngine(engine="e" * 200, reason="r" * 300)] * 33,
        url_diagnostics=diagnostics,
    )

    assert len(stats.sub_queries) == 9
    assert len(stats.unresponsive_engines) == 33
    assert len(stats.url_diagnostics) == 51


def test_v1_wire_compatibility_response_sample_and_non_terminal_enum_values_remain_accepted():
    contract = _load_json(COMPATIBILITY_CONTRACT)
    wire_compatibility_sample = contract["wire_compatibility_response_sample"]
    response = SearchResponse.model_validate(wire_compatibility_sample)

    assert _legacy_projection(response.model_dump(mode="json"), wire_compatibility_sample) == wire_compatibility_sample

    for path, values in contract["response_enum_values"].items():
        if path in {"stats.reason", "stats.discovery_status"}:
            continue
        for value in values:
            payload = copy.deepcopy(wire_compatibility_sample)
            _set_path(payload, path, value)
            validated = SearchResponse.model_validate(payload).model_dump(mode="json")
            assert _value_at_path(validated, path) == value

    for discovery_status in contract["response_enum_values"]["stats.discovery_status"]:
        payload = copy.deepcopy(
            contract["reason_value_validation_template"]
            if discovery_status == "unavailable"
            else wire_compatibility_sample
        )
        _set_path(payload, "stats.discovery_status", discovery_status)
        if discovery_status == "unavailable":
            _set_path(payload, "stats.reason", "search_provider_unavailable")
        validated = SearchResponse.model_validate(payload).model_dump(mode="json")
        assert validated["stats"]["discovery_status"] == discovery_status


def test_v1_response_defaults_remain_legacy_compatible():
    contract = _load_json(COMPATIBILITY_CONTRACT)
    example = contract["v1_response_default_example"]
    response = SearchResponse.model_validate(example["input"])

    assert _legacy_projection(response.model_dump(mode="json"), example["normalized_legacy_output"]) == example["normalized_legacy_output"], example["name"]


def test_v1_reason_value_validation_template_keeps_reason_codes_empty():
    contract = _load_json(COMPATIBILITY_CONTRACT)

    for reason in contract["response_enum_values"]["stats.reason"]:
        payload = copy.deepcopy(contract["reason_value_validation_template"])
        _set_path(payload, "stats.reason", reason)
        if reason == "search_provider_unavailable":
            _set_path(payload, "stats.discovery_status", "unavailable")
        validated = SearchResponse.model_validate(payload).model_dump(mode="json")

        assert validated["passages"] == []
        assert validated["citations"] == []
        assert validated["stats"]["reason"] == reason


def test_phase0_fixture_corpus_has_stable_identity_and_coverage():
    manifest = _load_json(FIXTURE_ROOT / "corpus-manifest.json")
    entries = manifest["files"]
    manifest_paths = {entry["path"] for entry in entries}

    assert manifest["fixture_revision"] == "web-intelligence-phase0-v2"
    assert manifest["identity"] == "thorondor-local-first-party-web-intelligence"
    assert manifest_paths == EXPECTED_FIXTURE_FILES
    assert {
        path.relative_to(FIXTURE_ROOT).as_posix()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    } == manifest_paths | {"corpus-manifest.json"}
    assert {
        coverage
        for entry in entries
        for coverage in entry["coverage"]
    } == EXPECTED_COVERAGE

    for entry in entries:
        content = (FIXTURE_ROOT / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_phase0_fixture_corpus_represents_required_edge_cases():
    static_html = (FIXTURE_ROOT / "static-page.html").read_text(encoding="utf-8")
    javascript_shell = (FIXTURE_ROOT / "javascript-shell.html").read_text(encoding="utf-8")
    challenge_shell = (FIXTURE_ROOT / "challenge-shell.html").read_text(encoding="utf-8")
    canonical_conflicts = (FIXTURE_ROOT / "canonical-conflicts.html").read_text(encoding="utf-8")
    sitemap = ElementTree.fromstring((FIXTURE_ROOT / "sitemap-index.xml").read_text(encoding="utf-8"))
    redirects = _load_json(FIXTURE_ROOT / "redirect-metadata.json")
    robots = _load_json(FIXTURE_ROOT / "robots-groups-and-states.json")
    query_variants = _load_json(FIXTURE_ROOT / "query-variants.json")
    unicode_markdown = _load_json(FIXTURE_ROOT / "unicode-crlf-markdown.json")
    documents = _load_json(FIXTURE_ROOT / "document-metadata.json")
    transitions = _load_json(FIXTURE_ROOT / "status-validator-transitions.json")
    searxng = _load_json(FIXTURE_ROOT / "searxng-plural-result.json")
    crawl4ai = _load_json(FIXTURE_ROOT / "crawl4ai-metadata-result.json")
    malformed = _load_json(FIXTURE_ROOT / "malformed-upstream-payloads.json")

    assert 'data-fixture="static-source"' in static_html
    assert 'id="client-root"' in javascript_shell
    assert 'data-fixture="challenge-shell"' in challenge_shell
    assert redirects["requested_url"] != redirects["final_url"]
    assert [hop["status"] for hop in redirects["hops"]] == [302, 308]
    assert {group["user_agent"] for group in robots["groups"]} == {"ThorondorBot", "*"}
    assert {(state["status"], state["descriptor"]) for state in robots["states"]} == {
        (404, "robots_unavailable_allow"),
        (503, "robots_unreachable_disallow"),
    }
    assert sitemap.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemapindex"
    assert [element.text for element in sitemap.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap/{http://www.sitemaps.org/schemas/sitemap/0.9}loc")] == [
        "https://fixtures.thorondor.test/sitemaps/articles.xml",
        "https://fixtures.thorondor.test/sitemaps/archive.xml",
    ]

    duplicate_a = (FIXTURE_ROOT / "duplicate-preamble-a.md").read_text(encoding="utf-8")
    duplicate_b = (FIXTURE_ROOT / "duplicate-preamble-b.md").read_text(encoding="utf-8")
    duplicate_urls = _load_json(FIXTURE_ROOT / "duplicate-preamble-urls.json")
    preamble_a, body_a = duplicate_a.split("\n\n", 1)
    preamble_b, body_b = duplicate_b.split("\n\n", 1)

    assert {entry["path"] for entry in duplicate_urls["fixtures"]} == {
        "duplicate-preamble-a.md",
        "duplicate-preamble-b.md",
    }
    assert {entry["url"] for entry in duplicate_urls["fixtures"]} == {
        "https://fixtures.thorondor.test/dedup/shared-preamble-a",
        "https://fixtures.thorondor.test/dedup/shared-preamble-b",
    }
    assert preamble_a == preamble_b
    assert len(preamble_a) > 2_000
    assert body_a != body_b
    assert canonical_conflicts.count('rel="canonical"') == 2
    assert "https://other-origin.fixture.test/articles/claim" in canonical_conflicts
    first_query = urlsplit(query_variants["variants"][0]["url"])
    second_query = urlsplit(query_variants["variants"][1]["url"])
    assert parse_qsl(first_query.query, keep_blank_values=True) == [
        ("tag", "one"),
        ("tag", "two"),
        ("utm_source", "fixture"),
    ]
    assert parse_qsl(second_query.query, keep_blank_values=True) == [
        ("tag", "two"),
        ("tag", "one"),
        ("utm_source", "fixture"),
    ]
    assert first_query.fragment == "section"
    assert "\r\n" in unicode_markdown["markdown"]
    assert "東京" in unicode_markdown["markdown"]
    assert "e\u0301" in unicode_markdown["markdown"]
    assert {item["content_type"] for item in documents["documents"]} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert all(item["requested_url"] == item["final_url"] for item in documents["documents"])
    assert {item["metadata"]["filename"] for item in documents["documents"]} == {"phase0.pdf", "phase0.docx"}
    assert [
        (item["status"], item["etag"], item["last_modified"])
        for item in transitions["observations"]
    ] == [
        (200, '"fixture-v1"', "Mon, 03 Aug 2026 09:00:00 GMT"),
        (304, '"fixture-v1"', "Mon, 03 Aug 2026 09:00:00 GMT"),
        (200, '"fixture-v2"', "Tue, 04 Aug 2026 10:30:00 GMT"),
        (404, None, None),
    ]
    assert searxng["results"][0]["engines"] == ["fixture-news", "fixture-reference"]
    assert searxng["results"][0]["positions"] == [1, 3]
    assert searxng["results"][0]["publishedDate"] == "2026-08-04T10:15:00Z"
    crawl4ai_result = crawl4ai["results"][0]
    assert crawl4ai_result["redirected_url"] == "https://fixtures.thorondor.test/articles/final"
    assert crawl4ai_result["status_code"] == 200
    assert {"content-type", "etag", "last-modified"} <= set(crawl4ai_result["response_headers"])
    assert crawl4ai_result["links"]["internal"][0]["href"] == "/articles/next"
    assert crawl4ai_result["metadata"]["title"] == "Final fixture article"
    assert {case["name"] for case in malformed["searxng"]} >= {
        "non-object-envelope",
        "missing-plural-engines",
        "wrong-position-type",
    }
    assert {case["name"] for case in malformed["crawl4ai"]} >= {
        "empty-results",
        "missing-response-headers",
        "wrong-status-type",
        "wrong-nested-link-href-type",
    }
