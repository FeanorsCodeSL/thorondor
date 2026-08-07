import asyncio
import json
import time
from dataclasses import dataclass

from orchestrator import fakes
from orchestrator.models import MapRequest
from orchestrator.outcome_codes import FetchOutcomeCode
from orchestrator.site_pipeline import run_map
from orchestrator.types import DiscoveryOutcome, DiscoveryResult, FetchStageOutcome, Page

FIXTURE_REVISION = "web-intelligence-phase3-map-v1"
SEED = "https://fixture.test/docs/start"
TARGETS = {
    SEED,
    "https://fixture.test/docs/sitemap-page",
    "https://fixture.test/docs/shared",
    "https://fixture.test/docs/link-page",
    "https://fixture.test/docs/search-page",
}


def _content(url: str, body: str, links: list[str] | None = None) -> FetchStageOutcome:
    link_payload = {"internal": [{"href": value} for value in links or []]}
    page = Page(
        url=url,
        title=url,
        markdown=body,
        html=body,
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        links=link_payload,
    )
    return FetchStageOutcome(
        requested_url=url,
        final_url=url,
        code=FetchOutcomeCode.CONTENT,
        retrieval_method="fixture_browser",
        elapsed_ms=0,
        status_code=200,
        content_type="text/html",
        title=url,
        links=link_payload,
        page=page,
    )


def _missing(url: str) -> FetchStageOutcome:
    return FetchStageOutcome(
        requested_url=url,
        final_url=url,
        code=FetchOutcomeCode.UPSTREAM_FAILURE,
        retrieval_method="fixture_browser",
        elapsed_ms=0,
        status_code=404,
    )


class FixtureExtractor:
    supported_capabilities = frozenset(
        {"markdown", "javascript", "links", "metadata", "raw_html", "pdf", "document"}
    )

    def __init__(self):
        self.requests: list[str] = []
        self.outcomes = {
            SEED: _content(
                SEED,
                "# Seed",
                ["/docs/link-page", "/docs/shared", "/private/blocked"],
            ),
            "https://fixture.test/robots.txt": _content(
                "https://fixture.test/robots.txt",
                "User-agent: ThorondorBot\nDisallow: /private\nSitemap: /sitemap.xml",
            ),
            "https://fixture.test/sitemap.xml": _content(
                "https://fixture.test/sitemap.xml",
                """<urlset>
<url><loc>https://fixture.test/docs/sitemap-page</loc><lastmod>2026-08-04</lastmod></url>
<url><loc>https://fixture.test/docs/shared</loc></url>
<url><loc>https://fixture.test/private/blocked</loc></url>
</urlset>""",
            ),
            "https://fixture.test/sitemap_index.xml": _missing(
                "https://fixture.test/sitemap_index.xml"
            ),
            "https://fixture.test/docs/sitemap-page": _content(
                "https://fixture.test/docs/sitemap-page",
                "# Sitemap page",
            ),
            "https://fixture.test/docs/shared": _content(
                "https://fixture.test/docs/shared",
                "# Shared page",
            ),
            "https://fixture.test/docs/link-page": _content(
                "https://fixture.test/docs/link-page",
                "# Link page",
            ),
            "https://fixture.test/docs/search-page": _content(
                "https://fixture.test/docs/search-page",
                "# Search page",
            ),
        }

    async def fetch(self, urls, _capabilities, _include_raw_html):
        self.requests.extend(urls)
        return [self.outcomes.get(url, _missing(url)) for url in urls]


class FixtureDiscovery:
    async def search(self, query: str, freshness=None):
        if query != "site:fixture.test" or freshness is not None:
            return DiscoveryOutcome([], [])
        return DiscoveryOutcome(
            [
                DiscoveryResult(
                    "Search page",
                    "https://fixture.test/docs/search-page",
                    "",
                    "fixture",
                    1,
                )
            ],
            [],
        )


@dataclass(frozen=True)
class Mode:
    name: str
    sitemap: str
    include_search: bool


MODES = (
    Mode("sitemap_only", "only", False),
    Mode("bfs_only", "skip", False),
    Mode("fused", "include", False),
    Mode("fused_with_search", "include", True),
)


async def _run_mode(mode: Mode) -> dict:
    extractor = FixtureExtractor()
    deps = fakes.deps(extractor=extractor, discovery=FixtureDiscovery())
    deps.crawl_url_safety = lambda _url: True
    started = time.perf_counter()
    response = await run_map(
        MapRequest(
            url=SEED,
            sitemap=mode.sitemap,
            include_search=mode.include_search,
            max_depth=2,
            max_pages=10,
            max_discovered_urls=50,
        ),
        deps,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    fetched = {
        record.url
        for record in response.urls
        if record.states[-1] == "fetched"
    }
    overlaps = sum(1 for record in response.urls if len(record.sources) > 1)
    return {
        "coverage": len(fetched & TARGETS) / len(TARGETS),
        "covered_urls": sorted(fetched & TARGETS),
        "modeled_fetch_requests": len(extractor.requests),
        "network_requests": 0,
        "elapsed_ms": round(elapsed_ms, 3),
        "duplicate_source_overlap": overlaps,
        "filtered": response.stats.filtered,
        "failed": response.stats.failed,
        "omitted": response.stats.omitted,
        "outcome": response.outcome,
    }


def build_report() -> dict:
    return {
        "fixture_revision": FIXTURE_REVISION,
        "configuration": {
            "max_depth": 2,
            "max_pages": 10,
            "max_discovered_urls": 50,
            "same_origin": True,
            "seed_path_scope": True,
            "robots": True,
        },
        "modes": {
            mode.name: asyncio.run(_run_mode(mode))
            for mode in MODES
        },
    }


def main() -> None:
    print(json.dumps(build_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
