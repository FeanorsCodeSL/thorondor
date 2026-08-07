from dataclasses import replace

import anyio
import pytest

from orchestrator import fakes
from orchestrator.fetch_pipeline import run_fetch
from orchestrator.models import FetchRequest
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.tests.test_page_cache import SequenceFetcher, _cache_deps, _outcome
from orchestrator.types import FetchStageOutcome

WATCH = {
    "target": {"css": "#stock"},
    "expected": {"text": "Out of stock"},
    "desired": {"text": "In stock"},
}


def _request(force_refresh=False, watch=WATCH):
    return FetchRequest(
        urls=["https://shop.test/product"],
        force_refresh=force_refresh,
        watch=watch,
    )


def test_watch_ignores_ads_and_triggers_only_target_transition(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome(
                "Ad: one\n\nOut of stock",
                '<aside>Ad: one</aside><button id="stock">Out of stock</button>',
            ),
            _outcome(
                "Ad: two\n\nOut of stock",
                '<aside>Ad: two</aside><button id="stock">Out of stock</button>',
            ),
            _outcome(
                "Ad: three\n\nIn stock",
                '<aside>Ad: three</aside><button id="stock">In stock</button>',
            ),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)

    first = anyio.run(run_fetch, _request(), deps).results[0]
    ad_change = anyio.run(run_fetch, _request(True), deps).results[0]
    available = anyio.run(run_fetch, _request(True), deps).results[0]

    assert first.watch.resolution == "found"
    assert first.watch.state == "new"
    assert first.watch.condition_met is False
    assert ad_change.change.state == "changed"
    assert ad_change.watch.state == "same"
    assert ad_change.watch.condition_met is False
    assert available.watch.state == "changed"
    assert available.watch.previous.text == "Out of stock"
    assert available.watch.current.text == "In stock"
    assert available.watch.condition_met is True


def test_desired_text_elsewhere_does_not_satisfy_watch(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("Out of stock", '<button id="stock">Out of stock</button>'),
            _outcome(
                "In stock deals\n\nOut of stock",
                '<aside>In stock deals</aside><button id="stock">Out of stock</button>',
            ),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)

    anyio.run(run_fetch, _request(), deps)
    result = anyio.run(run_fetch, _request(True), deps).results[0]

    assert result.watch.state == "same"
    assert result.watch.condition_met is False


def test_watch_missing_ambiguous_removed_and_fetch_failure_fail_closed(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("Out", '<button id="stock">Out of stock</button>'),
            _outcome("Gone", "<main>Gone</main>"),
            _outcome(
                "Two",
                '<button id="stock">In stock</button><button id="stock">In stock</button>',
            ),
            FetchStageOutcome(
                requested_url="https://shop.test/product",
                final_url=None,
                code=FetchOutcomeCode.UPSTREAM_TIMEOUT,
                retrieval_method="test_browser",
                elapsed_ms=10,
            ),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)

    anyio.run(run_fetch, _request(), deps)
    missing = anyio.run(run_fetch, _request(True), deps).results[0]
    ambiguous = anyio.run(run_fetch, _request(True), deps).results[0]
    cached_ambiguous = anyio.run(run_fetch, _request(), deps).results[0]
    failure = anyio.run(run_fetch, _request(True), deps).results[0]

    assert missing.watch.resolution == "missing"
    assert missing.watch.state == "removed"
    assert missing.watch.condition_met is False
    assert ambiguous.watch.resolution == "ambiguous"
    assert ambiguous.watch.state is None
    assert ambiguous.watch.condition_met is False
    assert cached_ambiguous.watch.resolution == "ambiguous"
    assert cached_ambiguous.watch.state is None
    assert failure.watch.resolution == "unsupported"
    assert failure.watch.state is None
    assert failure.watch.condition_met is False


def test_role_name_attribute_and_named_section_targets(tmp_path):
    html = """
    <section aria-label="Purchase">
      <button aria-label="Availability" data-state="sold-out">Notify me</button>
    </section>
    <section aria-label="Recommendations">
      <button aria-label="Availability" data-state="available">Buy</button>
    </section>
    """
    changed = html.replace('data-state="sold-out"', 'data-state="available"', 1)
    watch = {
        "target": {
            "role": "button",
            "name": "Availability",
            "section": "Purchase",
        },
        "expected": {"attribute": "data-state", "value": "sold-out"},
        "desired": {"attribute": "data-state", "value": "available"},
    }
    fetcher = SequenceFetcher([_outcome("Sold out", html), _outcome("Available", changed)])
    deps = _cache_deps(tmp_path, fetcher)

    first = anyio.run(run_fetch, _request(watch=watch), deps).results[0]
    second = anyio.run(run_fetch, _request(True, watch), deps).results[0]

    assert first.watch.resolution == "found"
    assert second.watch.resolution == "found"
    assert second.watch.condition_met is True


def test_attribute_state_names_are_case_insensitive(tmp_path):
    watch = {
        "target": {"css": "#stock"},
        "expected": {"attribute": "DATA-STATE", "value": "sold-out"},
        "desired": {"attribute": "DATA-STATE", "value": "available"},
    }
    fetcher = SequenceFetcher(
        [
            _outcome("Sold out", '<button id="stock" data-state="sold-out">Notify</button>'),
            _outcome("Available", '<button id="stock" data-state="available">Buy</button>'),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)

    anyio.run(run_fetch, _request(watch=watch), deps)
    changed = anyio.run(run_fetch, _request(True, watch), deps).results[0]

    assert changed.watch.condition_met is True


def test_deep_target_markup_fails_closed(tmp_path):
    html = "<div>" * 200 + '<button id="stock">In stock</button>' + "</div>" * 200
    fetcher = SequenceFetcher([_outcome("In stock", html)])
    deps = _cache_deps(tmp_path, fetcher)

    result = anyio.run(run_fetch, _request(), deps).results[0]

    assert result.watch.resolution == "unsupported"
    assert result.watch.condition_met is False


def test_watch_is_unsupported_without_durable_cache():
    deps = fakes.deps()
    deps.extractor = SequenceFetcher([_outcome("Out", '<button id="stock">Out</button>')])
    deps.crawl_url_safety = lambda _url: True

    result = anyio.run(run_fetch, _request(), deps).results[0]

    assert result.watch.resolution == "unsupported"
    assert result.watch.state is None
    assert result.watch.condition_met is False


def test_normalized_text_target_is_scoped_to_named_section(tmp_path):
    first_html = """
    <section aria-label="Purchase">
      <p>Availability status: Out of stock</p>
    </section>
    <section aria-label="Recommendations">
      <p>Availability status: In stock</p>
    </section>
    """
    second_html = first_html.replace(
        "Availability status: Out of stock",
        "Availability status: In stock",
        1,
    )
    watch = {
        "target": {"text": "Availability status", "section": "Purchase"},
        "expected": {"text": "Availability status: Out of stock"},
        "desired": {"text": "Availability status: In stock"},
    }
    fetcher = SequenceFetcher(
        [_outcome("Out", first_html), _outcome("In", second_html)]
    )
    deps = _cache_deps(tmp_path, fetcher)

    first = anyio.run(run_fetch, _request(watch=watch), deps).results[0]
    second = anyio.run(run_fetch, _request(True, watch), deps).results[0]

    assert first.watch.resolution == "found"
    assert second.watch.resolution == "found"
    assert second.watch.condition_met is True


def test_watch_preserves_mixed_inline_text_order(tmp_path):
    fetcher = SequenceFetcher(
        [_outcome("Out of stock", '<button id="stock">Out <span>of</span> stock</button>')]
    )
    deps = _cache_deps(tmp_path, fetcher)

    result = anyio.run(run_fetch, _request(), deps).results[0]

    assert result.watch.resolution == "found"
    assert result.watch.current.text == "Out of stock"


def test_same_locator_with_different_watch_definition_starts_new_history(tmp_path):
    html = '<button id="stock" data-state="sold-out">Out of stock</button>'
    first_watch = WATCH
    second_watch = {
        "target": {"css": "#stock"},
        "expected": {"attribute": "data-state", "value": "sold-out"},
        "desired": {"attribute": "data-state", "value": "available"},
    }
    fetcher = SequenceFetcher([_outcome("Out", html), _outcome("Out", html)])
    deps = _cache_deps(tmp_path, fetcher)

    first = anyio.run(run_fetch, _request(watch=first_watch), deps).results[0]
    second = anyio.run(run_fetch, _request(True, second_watch), deps).results[0]

    assert first.watch.state == "new"
    assert second.watch.state == "new"
    assert second.watch.condition_met is False


def test_transient_missing_target_keeps_last_valid_transition_baseline(tmp_path):
    fetcher = SequenceFetcher(
        [
            _outcome("Out", '<button id="stock">Out of stock</button>'),
            _outcome("Missing", "<main>Temporarily unavailable</main>"),
            _outcome("In", '<button id="stock">In stock</button>'),
        ]
    )
    deps = _cache_deps(tmp_path, fetcher)

    anyio.run(run_fetch, _request(), deps)
    missing = anyio.run(run_fetch, _request(True), deps).results[0]
    available = anyio.run(run_fetch, _request(True), deps).results[0]

    assert missing.watch.resolution == "missing"
    assert missing.watch.condition_met is False
    assert available.watch.previous.text == "Out of stock"
    assert available.watch.current.text == "In stock"
    assert available.watch.condition_met is True


def test_watch_uses_raw_dom_when_cleaned_html_removed_target(tmp_path):
    outcome = _outcome("Out", "<main>Cleaned content</main>")
    outcome = replace(
        outcome,
        page=replace(
            outcome.page,
            raw_html='<button id="stock">Out of stock</button>',
        ),
    )
    deps = _cache_deps(tmp_path, SequenceFetcher([outcome]))

    result = anyio.run(run_fetch, _request(), deps).results[0]

    assert result.watch.resolution == "found"
    assert result.watch.current.text == "Out of stock"


def test_contains_attribute_watch_rejects_blank_state():
    with pytest.raises(ValueError, match="non-blank value"):
        _request(
            watch={
                "target": {"css": "#stock"},
                "expected": {"attribute": "data-state", "value": "sold-out"},
                "desired": {"attribute": "data-state", "value": ""},
                "match": "contains",
            }
        )
