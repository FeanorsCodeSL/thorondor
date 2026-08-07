import asyncio
import json
import time
from pathlib import Path

import httpx

from orchestrator.clients.crawl4ai_client import Crawl4aiExtractor


FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "orchestrator"
    / "tests"
    / "fixtures"
    / "web_intelligence"
)


def _load_json(name: str):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _load_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def _cases() -> list[dict]:
    metadata_payload = _load_json("crawl4ai-metadata-result.json")
    documents = _load_json("document-metadata.json")["documents"]
    malformed = _load_json("malformed-upstream-payloads.json")["crawl4ai"][0]
    cases = [
        {
            "name": "static_html",
            "fixture": "crawl4ai-metadata-result.json",
            "url": "https://fixtures.thorondor.test/articles/requested",
            "capabilities": frozenset({"markdown", "javascript", "links", "metadata"}),
            "payload": metadata_payload,
            "expected": "content",
        },
        {
            "name": "javascript_rendered",
            "fixture": "javascript-shell.html",
            "url": "https://fixtures.thorondor.test/rendered",
            "capabilities": frozenset({"markdown", "javascript", "links", "metadata"}),
            "payload": {
                "results": [
                    {
                        "success": True,
                        "status_code": 200,
                        "response_headers": {"Content-Type": "text/html"},
                        "html": "<article><h1>Rendered fixture</h1><p>Client content loaded.</p></article>",
                        "markdown": "# Rendered fixture\n\nClient content loaded.",
                        "metadata": {"title": "Rendered fixture"},
                        "links": {"internal": []},
                    }
                ]
            },
            "expected": "content",
        },
    ]
    for document in documents:
        capability = "pdf" if document["content_type"] == "application/pdf" else "document"
        cases.append(
            {
                "name": capability,
                "fixture": "document-metadata.json",
                "url": document["requested_url"],
                "capabilities": frozenset({"markdown", capability}),
                "payload": {
                    "results": [
                        {
                            "success": True,
                            "url": document["final_url"],
                            "status_code": document["status"],
                            "response_headers": {
                                "Content-Type": document["content_type"]
                            },
                            "metadata": document["metadata"],
                            "markdown": f"# {document['metadata']['title']}\n\nExtracted document evidence.",
                        }
                    ]
                },
                "expected": "content",
            }
        )
    cases.extend(
        [
            {
                "name": "challenge_shell",
                "fixture": "challenge-shell.html",
                "url": "https://fixtures.thorondor.test/challenge",
                "capabilities": frozenset({"markdown", "javascript"}),
                "payload": {
                    "results": [
                        {
                            "success": True,
                            "status_code": 403,
                            "html": _load_text("challenge-shell.html"),
                            "markdown": "",
                        }
                    ]
                },
                "expected": "challenge",
            },
            {
                "name": "javascript_empty_shell",
                "fixture": "javascript-shell.html",
                "url": "https://fixtures.thorondor.test/shell",
                "capabilities": frozenset({"markdown", "javascript"}),
                "payload": {
                    "results": [
                        {
                            "success": True,
                            "status_code": 200,
                            "html": _load_text("javascript-shell.html"),
                            "markdown": "",
                        }
                    ]
                },
                "expected": "empty_shell",
            },
            {
                "name": "malformed_upstream",
                "fixture": "malformed-upstream-payloads.json",
                "url": "https://fixtures.thorondor.test/malformed",
                "capabilities": frozenset({"markdown"}),
                "payload": malformed["payload"],
                "expected": "malformed_upstream_response",
            },
        ]
    )
    return cases


async def _crawl4ai_report() -> dict:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500)
        )
    )
    extractor = Crawl4aiExtractor(
        "http://crawl4ai:11235",
        concurrency=4,
        timeout_s=15,
        respect_robots_txt=True,
        per_host_concurrency=1,
        crawler_user_agent="ThorondorBot/1.0 (+https://example.test/contact)",
        url_safety=lambda _url: True,
        client=client,
    )
    observations = []
    started = time.perf_counter()
    try:
        for case in _cases():
            case_started = time.perf_counter()
            outcome = await extractor._outcome_from_payload(
                case["url"],
                case["payload"],
                case["capabilities"],
                case_started,
            )
            observations.append(
                {
                    "name": case["name"],
                    "fixture": case["fixture"],
                    "expected_outcome": case["expected"],
                    "outcome": outcome.code.value,
                    "elapsed_ms": outcome.elapsed_ms,
                    "payload_bytes": len(
                        json.dumps(
                            case["payload"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                    "has_markdown": bool(outcome.page and outcome.page.markdown),
                    "metadata_items": len(outcome.metadata),
                    "link_groups": len(outcome.links),
                }
            )
    finally:
        await client.aclose()
    content_cases = [item for item in observations if item["expected_outcome"] == "content"]
    link_cases = [
        item
        for item in content_cases
        if item["name"] in {"static_html", "javascript_rendered"}
    ]
    return {
        "eligible": True,
        "enabled": True,
        "retrieval_method": "crawl4ai_browser",
        "fixture_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "fixture_network_request_count": 0,
        "production_crawl4ai_api_requests_per_url": 1,
        "fixture_payload_bytes": sum(item["payload_bytes"] for item in observations),
        "terminal_reason_accuracy": sum(
            item["outcome"] == item["expected_outcome"] for item in observations
        )
        / len(observations),
        "content_markdown_coverage": sum(item["has_markdown"] for item in content_cases)
        / len(content_cases),
        "metadata_coverage": sum(item["metadata_items"] > 0 for item in content_cases)
        / len(content_cases),
        "links_coverage": sum(item["link_groups"] > 0 for item in link_cases)
        / len(link_cases),
        "observations": observations,
    }


def build_report() -> dict:
    return {
        "fixture_revision": "web-intelligence-phase0-v2",
        "configuration": {
            "max_content_bytes": 262144,
            "max_response_body_bytes": 2097152,
            "raw_html_default": False,
        },
        "routes": {
            "crawl4ai_browser": asyncio.run(_crawl4ai_report()),
            "direct_static_http": {
                "eligible": False,
                "enabled": False,
                "benchmark_status": "security_gate_rejected",
                "reason": "No direct connector currently provides the reviewed connect-time DNS pinning and redirect policy.",
                "fixture_elapsed_ms": None,
                "fixture_network_request_count": 0,
            },
        },
        "decision": "Keep Crawl4AI as the only fetch route; do not add a direct static path.",
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
