from orchestrator.selection import SelectionPolicyImpl
from orchestrator.types import DiscoveryResult


def _r(url, score):
    return DiscoveryResult(url, url, "", "e", score)


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
