from scripts.benchmark_web_intelligence_phase5 import build_report


def test_phase5_threshold_report_is_complete_and_reproducible():
    report = build_report()

    assert report["fixture_revision"] == "web-intelligence-phase5-jobs-v1"
    assert report["configuration"]["synchronous_max_pages"] == 10
    assert set(report["cases"]) == {"1", "10", "20"}
    for pages, modes in report["cases"].items():
        expected = int(pages)
        for mode in modes.values():
            assert mode["modeled_target_requests"] == expected
            assert mode["network_requests"] == 0
            assert mode["terminal_reason"] == "completed"
            assert mode["results_available"] == expected
            assert mode["result_recall"] == 1.0
            assert mode["duplicate_rate"] == 0.0
            assert mode["elapsed_ms"] >= 0
    assert "at most 10 pages" in report["decision"]
    assert "do not represent" in report["limitations"]
