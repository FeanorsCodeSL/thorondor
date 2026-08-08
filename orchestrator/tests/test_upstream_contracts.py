import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "web_intelligence"
SUPPORTED_METADATA = "supported"
UNSUPPORTED_METADATA = "unsupported_metadata"


def _load_json(name: str):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _first_result(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    item = results[0]
    return item if isinstance(item, dict) else None


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_integer_list(value: object) -> bool:
    return isinstance(value, list) and all(type(item) is int for item in value)


def _is_header_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())


def _is_link_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) and isinstance(item.get("href"), str) for item in value)


def _is_links(value: object) -> bool:
    return isinstance(value, dict) and all(_is_link_list(value.get(key)) for key in ("internal", "external"))


SEARXNG_CAPABILITIES: dict[str, Callable[[object], bool]] = {
    "engines": _is_string_list,
    "positions": _is_integer_list,
    "publishedDate": lambda value: isinstance(value, str),
}

CRAWL4AI_CAPABILITIES: dict[str, Callable[[object], bool]] = {
    "redirected_url": lambda value: isinstance(value, str),
    "status_code": lambda value: type(value) is int,
    "response_headers": _is_header_map,
    "links": _is_links,
    "metadata": lambda value: isinstance(value, dict),
}


def _metadata_status(payload: object, capabilities: dict[str, Callable[[object], bool]]) -> str:
    if not isinstance(payload, dict):
        return UNSUPPORTED_METADATA
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return UNSUPPORTED_METADATA
    for item in results:
        if not isinstance(item, dict):
            return UNSUPPORTED_METADATA
        for field, check in capabilities.items():
            if field not in item or not check(item[field]):
                return UNSUPPORTED_METADATA
    return SUPPORTED_METADATA


def _with_item_value(payload: dict, field: str, value: object) -> dict:
    changed = copy.deepcopy(payload)
    changed["results"][0][field] = value
    return changed


def _without_item_field(payload: dict, field: str) -> dict:
    changed = copy.deepcopy(payload)
    del changed["results"][0][field]
    return changed


def _with_item_values(payload: dict, values: dict[str, object]) -> dict:
    changed = copy.deepcopy(payload)
    changed["results"][0].update(values)
    return changed


def test_pinned_searxng_metadata_fixture_exposes_the_consumed_raw_fields():
    payload = _load_json("searxng-plural-result.json")
    item = _first_result(payload)

    assert _metadata_status(payload, SEARXNG_CAPABILITIES) == SUPPORTED_METADATA
    assert item is not None
    assert item["engines"] == ["fixture-news", "fixture-reference"]
    assert item["positions"] == [1, 3]
    assert item["publishedDate"] == "2026-08-04T10:15:00Z"


def test_pinned_crawl4ai_metadata_fixture_exposes_the_consumed_raw_fields():
    payload = _load_json("crawl4ai-metadata-result.json")
    item = _first_result(payload)

    assert _metadata_status(payload, CRAWL4AI_CAPABILITIES) == SUPPORTED_METADATA
    assert item is not None
    assert item["redirected_url"] == "https://fixtures.thorondor.test/articles/final"
    assert item["status_code"] == 200
    assert item["response_headers"]["etag"] == '"fixture-v1"'
    assert item["links"]["internal"][0]["href"] == "/articles/next"
    assert item["metadata"]["title"] == "Final fixture article"


@pytest.mark.parametrize(
    ("fixture_name", "capabilities", "wrong_values"),
    [
        (
            "searxng-plural-result.json",
            SEARXNG_CAPABILITIES,
            {"engines": "fixture-news", "positions": "first", "publishedDate": 1},
        ),
        (
            "crawl4ai-metadata-result.json",
            CRAWL4AI_CAPABILITIES,
            {
                "redirected_url": 1,
                "status_code": "200",
                "response_headers": [],
                "links": [],
                "metadata": [],
            },
        ),
    ],
)
def test_missing_or_wrong_type_capabilities_fail_closed(
    fixture_name: str,
    capabilities: dict[str, Callable[[object], bool]],
    wrong_values: dict[str, object],
):
    payload = _load_json(fixture_name)

    for field, wrong_value in wrong_values.items():
        assert _metadata_status(_without_item_field(payload, field), capabilities) == UNSUPPORTED_METADATA
        assert _metadata_status(_with_item_value(payload, field, wrong_value), capabilities) == UNSUPPORTED_METADATA


@pytest.mark.parametrize(
    ("fixture_name", "capabilities", "malformed_second_result"),
    [
        ("searxng-plural-result.json", SEARXNG_CAPABILITIES, "not-an-object"),
        (
            "searxng-plural-result.json",
            SEARXNG_CAPABILITIES,
            {"engines": [], "positions": []},
        ),
        (
            "crawl4ai-metadata-result.json",
            CRAWL4AI_CAPABILITIES,
            {
                "redirected_url": "https://fixtures.thorondor.test/articles/final",
                "status_code": "200",
                "response_headers": {},
                "links": {"internal": [], "external": []},
                "metadata": {},
            },
        ),
    ],
)
def test_every_result_must_expose_supported_metadata(
    fixture_name: str,
    capabilities: dict[str, Callable[[object], bool]],
    malformed_second_result: object,
):
    payload = _load_json(fixture_name)

    assert _metadata_status(payload, capabilities) == SUPPORTED_METADATA
    payload["results"].append(malformed_second_result)
    assert _metadata_status(payload, capabilities) == UNSUPPORTED_METADATA


@pytest.mark.parametrize(
    ("fixture_name", "capabilities", "empty_values"),
    [
        (
            "searxng-plural-result.json",
            SEARXNG_CAPABILITIES,
            {"engines": [], "positions": []},
        ),
        (
            "crawl4ai-metadata-result.json",
            CRAWL4AI_CAPABILITIES,
            {
                "response_headers": {},
                "links": {"internal": [], "external": []},
                "metadata": {},
            },
        ),
    ],
)
def test_empty_capability_values_remain_supported_but_missing_values_do_not(
    fixture_name: str,
    capabilities: dict[str, Callable[[object], bool]],
    empty_values: dict[str, object],
):
    payload = _load_json(fixture_name)
    empty_payload = _with_item_values(payload, empty_values)

    assert _metadata_status(empty_payload, capabilities) == SUPPORTED_METADATA
    for field in empty_values:
        assert _metadata_status(_without_item_field(empty_payload, field), capabilities) == UNSUPPORTED_METADATA


def test_checked_in_malformed_upstream_payloads_fail_closed():
    malformed = _load_json("malformed-upstream-payloads.json")

    for case in malformed["searxng"]:
        assert _metadata_status(case["payload"], SEARXNG_CAPABILITIES) == UNSUPPORTED_METADATA, case["name"]
    for case in malformed["crawl4ai"]:
        assert _metadata_status(case["payload"], CRAWL4AI_CAPABILITIES) == UNSUPPORTED_METADATA, case["name"]
