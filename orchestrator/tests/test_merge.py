from orchestrator.merge import merge_dedup
from orchestrator.types import DiscoveryResult


def _r(url, score):
    return DiscoveryResult("T", url, "s", "e", score)


def test_duplicate_urls_keep_highest_score_and_sort_desc():
    out = merge_dedup([
        [_r("https://a.test/x?utm_source=n", 0.2), _r("https://b.test", 0.6)],
        [_r("https://a.test/x", 0.9)],
    ])
    assert [r.url for r in out] == ["https://a.test/x", "https://b.test"]
