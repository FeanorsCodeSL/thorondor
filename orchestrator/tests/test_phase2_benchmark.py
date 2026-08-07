from scripts.benchmark_web_intelligence_phase2 import build_report


def test_phase2_route_gate_keeps_only_the_eligible_backend():
    report = build_report()
    crawl4ai = report["routes"]["crawl4ai_browser"]
    direct = report["routes"]["direct_static_http"]

    assert crawl4ai["eligible"] is True
    assert crawl4ai["enabled"] is True
    assert crawl4ai["terminal_reason_accuracy"] == 1.0
    assert crawl4ai["content_markdown_coverage"] == 1.0
    assert crawl4ai["metadata_coverage"] == 1.0
    assert crawl4ai["links_coverage"] == 1.0
    assert {item["name"] for item in crawl4ai["observations"]} == {
        "static_html",
        "javascript_rendered",
        "pdf",
        "document",
        "challenge_shell",
        "javascript_empty_shell",
        "malformed_upstream",
    }
    assert direct == {
        "eligible": False,
        "enabled": False,
        "benchmark_status": "security_gate_rejected",
        "reason": "No direct connector currently provides the reviewed connect-time DNS pinning and redirect policy.",
        "fixture_elapsed_ms": None,
        "fixture_network_request_count": 0,
    }
    assert report["decision"] == (
        "Keep Crawl4AI as the only fetch route; do not add a direct static path."
    )
