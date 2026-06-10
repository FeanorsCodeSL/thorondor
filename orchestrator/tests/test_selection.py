from orchestrator.selection import SelectionPolicyImpl
from orchestrator.types import DiscoveryResult


def _r(url, score):
    return DiscoveryResult(url, url, "", "e", score)


def _result(title, url, snippet, engine, score):
    return DiscoveryResult(title, url, snippet, engine, score)


def test_blocklist_allowlist_cap_and_stable_order():
    results = [
        _r("https://b.test/x", 0.8),
        _r("https://a.test/x", 0.8),
        _r("https://blocked.test/x", 0.9),
        _r("https://c.test/x", 0.7),
    ]
    out = SelectionPolicyImpl().select(results, 2, {"blocked.test"}, {"a.test", "b.test", "c.test"})
    assert [r.url for r in out] == ["https://a.test/x", "https://b.test/x"]


def test_allowlist_keeps_only_matching_hosts():
    out = SelectionPolicyImpl().select(
        [_r("https://a.test/x", 0.9), _r("https://b.test/x", 0.8)],
        10,
        set(),
        {"b.test"},
    )
    assert [r.url for r in out] == ["https://b.test/x"]


def test_blocklist_matches_canonical_host_suffixes():
    out = SelectionPolicyImpl().select(
        [
            _r("https://EVIL.COM./x", 1.0),
            _r("https://sub.evil.com/x", 0.9),
            _r("https://safe.com/x", 0.8),
        ],
        10,
        {"evil.com"},
    )

    assert [r.url for r in out] == ["https://safe.com/x"]


def test_allowlist_matches_canonical_host_suffixes():
    out = SelectionPolicyImpl().select(
        [
            _r("https://sub.example.com/x", 1.0),
            _r("https://other.com/x", 0.9),
        ],
        10,
        set(),
        {"example.com."},
    )

    assert [r.url for r in out] == ["https://sub.example.com/x"]


def test_selection_applies_per_domain_cap_before_filling_rank_cap():
    results = [
        _r("https://same.test/a", 1.0),
        _r("https://same.test/b", 0.99),
        _r("https://same.test/c", 0.98),
        _r("https://other.test/a", 0.5),
        _r("https://third.test/a", 0.4),
    ]

    out = SelectionPolicyImpl().select(results, 4, set())

    assert [r.url for r in out] == [
        "https://same.test/a",
        "https://other.test/a",
        "https://third.test/a",
        "https://same.test/b",
    ]


def test_selection_preserves_engine_diversity_before_score_fill():
    results = [
        _result("A", "https://a.test/x", "", "engine-a", 1.0),
        _result("B", "https://b.test/x", "", "engine-a", 0.99),
        _result("C", "https://c.test/x", "", "engine-b", 0.7),
    ]

    out = SelectionPolicyImpl().select(results, 2, set())

    assert [r.url for r in out] == ["https://a.test/x", "https://c.test/x"]


def test_selection_uses_lexical_query_signal_as_tie_breaker():
    results = [
        _result("Unrelated", "https://b.test/x", "nothing about the question", "e", 0.8),
        _result("J Robert Oppenheimer", "https://a.test/x", "born in New York City", "e", 0.8),
    ]

    out = SelectionPolicyImpl().select(results, 2, set(), query="Where was Oppenheimer born?")

    assert [r.url for r in out] == ["https://a.test/x", "https://b.test/x"]


def test_selection_diagnostics_report_selected_and_filtered_reasons():
    selected, diagnostics = SelectionPolicyImpl().select_with_diagnostics(
        [
            _r("https://blocked.test/x", 1.0),
            _r("https://same.test/a", 0.9),
            _r("https://same.test/b", 0.8),
            _r("https://same.test/c", 0.7),
        ],
        2,
        {"blocked.test"},
    )

    assert [r.url for r in selected] == ["https://same.test/a", "https://same.test/b"]
    reasons = {item.result.url: item.filtered_reason or item.selection_reason for item in diagnostics}
    assert reasons["https://blocked.test/x"] == "blocked_domain"
    assert reasons["https://same.test/a"] == "engine_diversity"
    assert reasons["https://same.test/c"] == "domain_cap"
