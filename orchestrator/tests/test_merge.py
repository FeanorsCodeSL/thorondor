from orchestrator.merge import merge_dedup
from orchestrator.types import DiscoveryResult


def _r(url, score, published_at=None):
    return DiscoveryResult("T", url, "s", "e", score, published_at)


def test_duplicate_urls_keep_highest_score_and_sort_desc():
    out = merge_dedup([
        [_r("https://a.test/x?utm_source=n", 0.2), _r("https://b.test", 0.6)],
        [_r("https://a.test/x", 0.9)],
    ])
    assert [r.url for r in out] == ["https://a.test/x", "https://b.test"]


def test_corroborated_url_beats_equal_max_single_source_url():
    out = merge_dedup([
        [_r("https://solo.test", 0.5), _r("https://many.test", 0.5)],
        [_r("https://many.test/", 0.5)],
        [_r("https://many.test?utm_source=x", 0.5)],
    ])

    assert [r.url for r in out][:2] == ["https://many.test", "https://solo.test"]


def test_highest_scored_duplicate_preserves_its_publication_date():
    out = merge_dedup([
        [_r("https://a.test/article", 0.4, "2026-08-03T10:00:00Z")],
        [_r("https://a.test/article", 0.9, "2026-08-04T10:00:00Z")],
    ])

    assert out[0].published_at == "2026-08-04T10:00:00Z"
