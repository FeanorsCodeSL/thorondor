import pytest

from orchestrator.outcome_codes import (
    CacheOutcomeCode,
    CrawlOutcomeCode,
    FetchOutcomeCode,
    JobOutcomeCode,
    MapOutcomeCode,
)


EXPECTED_CODES = {
    FetchOutcomeCode: (
        ("CONTENT", "content"),
        ("EMPTY_SHELL", "empty_shell"),
        ("CHALLENGE", "challenge"),
        ("ROBOTS_REFUSED", "robots_refused"),
        ("UPSTREAM_TIMEOUT", "upstream_timeout"),
        ("DEADLINE_CANCELLED", "deadline_cancelled"),
        ("UNSAFE_REDIRECT", "unsafe_redirect"),
        ("UNSUPPORTED_CONTENT", "unsupported_content"),
        ("UNSUPPORTED_CAPABILITY", "unsupported_capability"),
        ("CONTENT_TOO_LARGE", "content_too_large"),
        ("EXTRACTION_EMPTY", "extraction_empty"),
        ("MALFORMED_UPSTREAM_RESPONSE", "malformed_upstream_response"),
        ("UPSTREAM_FAILURE", "upstream_failure"),
        ("RATE_LIMITED", "rate_limited"),
        ("UNSAFE_TARGET", "unsafe_target"),
        ("LOCAL_PROCESSING_FAILURE", "local_processing_failure"),
    ),
    MapOutcomeCode: (
        ("COMPLETED", "completed"),
        ("PARTIAL", "partial"),
        ("CANCELLED", "cancelled"),
        ("UNSAFE_SEED", "unsafe_seed"),
        ("UNSAFE_REDIRECT", "unsafe_redirect"),
        ("ROBOTS_REFUSED", "robots_refused"),
        ("LIMIT_REACHED", "limit_reached"),
        ("UPSTREAM_TIMEOUT", "upstream_timeout"),
        ("DEADLINE_CANCELLED", "deadline_cancelled"),
        ("MALFORMED_UPSTREAM_RESPONSE", "malformed_upstream_response"),
        ("UPSTREAM_FAILURE", "upstream_failure"),
        ("RATE_LIMITED", "rate_limited"),
        ("UNSAFE_TARGET", "unsafe_target"),
        ("LOCAL_PROCESSING_FAILURE", "local_processing_failure"),
    ),
    CrawlOutcomeCode: (
        ("COMPLETED", "completed"),
        ("PARTIAL", "partial"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
        ("NO_ADMITTED_URLS", "no_admitted_urls"),
        ("DEADLINE_CANCELLED", "deadline_cancelled"),
        ("UPSTREAM_TIMEOUT", "upstream_timeout"),
        ("UPSTREAM_FAILURE", "upstream_failure"),
        ("RATE_LIMITED", "rate_limited"),
        ("UNSAFE_SEED", "unsafe_seed"),
        ("UNSAFE_TARGET", "unsafe_target"),
        ("UNSAFE_REDIRECT", "unsafe_redirect"),
        ("LOCAL_PROCESSING_FAILURE", "local_processing_failure"),
    ),
    CacheOutcomeCode: (
        ("MISS", "miss"),
        ("FRESH", "fresh"),
        ("STALE", "stale"),
        ("REVALIDATED", "revalidated"),
        ("BYPASS", "bypass"),
        ("CORRUPT", "corrupt"),
        ("UNSUPPORTED_CAPABILITY", "unsupported_capability"),
    ),
    JobOutcomeCode: (
        ("QUEUED", "queued"),
        ("RUNNING", "running"),
        ("COMPLETED", "completed"),
        ("PARTIAL", "partial"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
        ("EXPIRED", "expired"),
    ),
}


@pytest.mark.parametrize(("code_type", "expected"), EXPECTED_CODES.items())
def test_outcome_codes_are_closed_machine_readable_and_have_no_aliases(code_type, expected):
    expected_names = tuple(name for name, _ in expected)

    assert tuple(code_type.__members__) == expected_names
    assert len(code_type.__members__) == len(code_type)
    assert tuple((code.name, code.value) for code in code_type) == expected
    assert all(isinstance(code, str) and code.value == code.value.lower() and " " not in code.value for code in code_type)
    assert all(code_type(value).name == name for name, value in expected)

    with pytest.raises(ValueError):
        code_type("new_human_message")
