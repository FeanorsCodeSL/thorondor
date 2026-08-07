from scripts.benchmark_web_intelligence_phase3 import build_report


def test_phase3_fused_mapping_improves_fixture_coverage_deterministically():
    report = build_report()
    modes = report["modes"]

    assert report["fixture_revision"] == "web-intelligence-phase3-map-v1"
    assert modes["sitemap_only"]["coverage"] == 0.6
    assert modes["bfs_only"]["coverage"] == 0.6
    assert modes["fused"]["coverage"] == 0.8
    assert modes["fused_with_search"]["coverage"] == 1.0
    assert all(mode["network_requests"] == 0 for mode in modes.values())
    assert all(mode["omitted"] == 0 for mode in modes.values())
    assert modes["fused"]["duplicate_source_overlap"] == 2
