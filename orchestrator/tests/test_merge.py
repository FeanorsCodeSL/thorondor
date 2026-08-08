from orchestrator.merge import merge_dedup
from orchestrator.types import DiscoveryContribution, DiscoveryResult


def _r(url, score, published_at=None):
    return DiscoveryResult("T", url, "s", "e", score, published_at)


def test_duplicate_urls_keep_highest_score_and_sort_desc():
    out = merge_dedup([
        [_r("https://a.test/x?utm_source=n", 0.2), _r("https://b.test", 0.6)],
        [_r("https://a.test/x", 0.9)],
    ])
    assert [r.url for r in out] == ["https://a.test/x", "https://b.test"]


def test_equal_scores_break_ties_deterministically_by_normalized_url():
    out = merge_dedup([
        [_r("https://solo.test", 0.5), _r("https://many.test", 0.5)],
        [_r("https://many.test/", 0.5)],
        [_r("https://many.test?utm_source=x", 0.5)],
    ])

    assert [r.url for r in out][:2] == ["https://many.test", "https://solo.test"]
    assert out[0].score == 0.5


def test_highest_scored_duplicate_preserves_its_publication_date():
    out = merge_dedup([
        [_r("https://a.test/article", 0.4, "2026-08-03T10:00:00Z")],
        [_r("https://a.test/article", 0.9, "2026-08-04T10:00:00Z")],
    ])

    assert out[0].published_at == "2026-08-04T10:00:00Z"


def test_merge_distinguishes_same_subquery_duplicates_from_independent_agreement():
    repeated = DiscoveryResult(
        "Repeated",
        "https://a.test/article",
        "snippet",
        "engine-a",
        0.7,
        contributions=(
            DiscoveryContribution("q1", "engine-a", 1, 0.7),
        ),
    )
    second_engine = DiscoveryResult(
        "Repeated",
        "https://a.test/article?utm_source=x",
        "snippet",
        "engine-b",
        0.8,
        contributions=(
            DiscoveryContribution("q1", "engine-b", 3, 0.8),
        ),
    )
    independent = DiscoveryResult(
        "Repeated",
        "https://a.test/article",
        "snippet",
        "engine-a",
        0.6,
        contributions=(
            DiscoveryContribution("q2", "engine-a", 2, 0.6),
        ),
    )

    merged = merge_dedup([[repeated, repeated, second_engine], [independent]])[0]

    assert merged.score == 0.8
    assert [(item.subquery, item.engine, item.position) for item in merged.contributions] == [
        ("q1", "engine-a", 1),
        ("q1", "engine-b", 3),
        ("q2", "engine-a", 2),
    ]
