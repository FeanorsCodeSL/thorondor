"""Crawl4AI extraction client."""
import asyncio
from collections import defaultdict
import logging
from urllib.parse import urljoin, urlparse

import httpx

from ..types import Page
from ..url_safety import is_safe_crawl_url

logger = logging.getLogger(__name__)

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MAX_PREFLIGHT_REDIRECTS = 5


class Crawl4aiExtractor:
    def __init__(
        self,
        base_url: str,
        concurrency: int,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
        respect_robots_txt: bool = True,
        per_host_concurrency: int = 1,
        api_key: str | None = None,
        validate_redirects: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.respect_robots_txt = respect_robots_txt
        self.per_host_concurrency = max(1, per_host_concurrency)
        self.api_key = api_key
        self.validate_redirects = validate_redirects
        timeout = httpx.Timeout(
            self.timeout_s,
            connect=min(5.0, self.timeout_s),
            read=self.timeout_s,
            write=self.timeout_s,
            pool=self.timeout_s,
        )
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=max(1, concurrency), max_keepalive_connections=max(1, concurrency)),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract(self, urls: list[str]) -> list[Page]:
        semaphore = asyncio.Semaphore(self.concurrency)

        def _crawl_payload(url: str) -> dict:
            return {
                "urls": [url],
                "crawler_config": {
                    "type": "CrawlerRunConfig",
                    "params": {
                        "stream": False,
                        "cache_mode": "bypass",
                        "check_robots_txt": self.respect_robots_txt,
                    },
                },
            }

        def _result_items(payload: dict) -> list[dict]:
            results = payload.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
            return [payload]

        def _markdown_from_result(item: dict, payload: dict) -> str:
            markdown = item.get("markdown") or payload.get("markdown")
            if isinstance(markdown, dict):
                return (
                    markdown.get("fit_markdown")
                    or markdown.get("raw_markdown")
                    or markdown.get("markdown_with_citations")
                    or ""
                )
            if isinstance(markdown, str):
                return markdown
            return item.get("content") or item.get("fit_markdown") or item.get("raw_markdown") or ""

        async def _preflight_redirects(client: httpx.AsyncClient, url: str) -> str | None:
            if not self.validate_redirects:
                return url
            current = url
            for _ in range(MAX_PREFLIGHT_REDIRECTS):
                response = await client.head(current, follow_redirects=False)
                if response.status_code not in REDIRECT_STATUS_CODES:
                    return current
                location = response.headers.get("location")
                if not location:
                    return None
                current = urljoin(current, location)
                if not is_safe_crawl_url(current):
                    logger.warning("Crawl preflight dropped for %s: unsafe redirect target", url)
                    return None
            logger.warning("Crawl preflight dropped for %s: redirect chain too deep", url)
            return None

        async def one(
            client: httpx.AsyncClient,
            url: str,
            host_semaphores: dict[str, asyncio.Semaphore],
        ) -> Page | None:
            host = urlparse(url).hostname or ""
            async with semaphore:
                async with host_semaphores[host]:
                    try:
                        crawl_url = await _preflight_redirects(client, url)
                        if crawl_url is None:
                            return None
                        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
                        response = await client.post(
                            f"{self.base_url}/crawl",
                            json=_crawl_payload(crawl_url),
                            headers=headers,
                        )
                        if response.status_code != 200:
                            logger.warning("Crawl failed for %s: status %s", url, response.status_code)
                            return None
                        payload = response.json()
                        for item in _result_items(payload):
                            if item.get("success") is False:
                                continue
                            final_url = item.get("redirected_url") or item.get("url")
                            if final_url and final_url != url and not is_safe_crawl_url(final_url):
                                logger.warning("Crawl dropped for %s: unsafe final URL", url)
                                return None
                            markdown = _markdown_from_result(item, payload)
                            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                            title = item.get("title") or metadata.get("title") or url
                            return Page(url=url, title=title, markdown=markdown) if markdown else None
                        return None
                    except Exception as exc:
                        logger.warning("Crawl failed for %s: %s", url, exc)
                        return None

        host_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.per_host_concurrency)
        )
        tasks = [asyncio.create_task(one(self._client, url, host_semaphores)) for url in urls]
        done, pending = await asyncio.wait(tasks, timeout=self.timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        pages = [
            task.result()
            for task in tasks
            if task in done and not task.cancelled()
        ]
        return [page for page in pages if page is not None]
