"""Crawl4AI extraction client."""
import asyncio
from collections import defaultdict
import logging
from urllib.parse import urljoin, urlparse
from collections.abc import Callable

import httpx

from ..observability import redact_url, request_id_headers
from ..types import Page

logger = logging.getLogger(__name__)

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class Crawl4aiExtractor:
    def __init__(
        self,
        base_url: str,
        concurrency: int,
        timeout_s: float,
        respect_robots_txt: bool,
        per_host_concurrency: int,
        validate_redirects: bool,
        max_preflight_redirects: int,
        url_safety: Callable[[str], bool],
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ):
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if per_host_concurrency < 1:
            raise ValueError("per_host_concurrency must be >= 1")
        if max_preflight_redirects < 1:
            raise ValueError("max_preflight_redirects must be >= 1")
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.respect_robots_txt = respect_robots_txt
        self.per_host_concurrency = per_host_concurrency
        self.api_key = api_key
        self.validate_redirects = validate_redirects
        self.max_preflight_redirects = max_preflight_redirects
        self.url_safety = url_safety
        timeout = httpx.Timeout(
            self.timeout_s,
            connect=min(5.0, self.timeout_s),
            read=self.timeout_s,
            write=self.timeout_s,
            pool=self.timeout_s,
        )
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract(self, urls: list[str]) -> list[Page]:
        semaphore = asyncio.Semaphore(self.concurrency)
        host_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.per_host_concurrency)
        )
        tasks = [
            asyncio.create_task(self._extract_one(self._client, url, semaphore, host_semaphores))
            for url in urls
        ]
        return await self._collect_pages(tasks)

    def _crawl_payload(self, url: str) -> dict:
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

    @staticmethod
    def _result_items(payload: dict) -> list[dict]:
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        return [payload]

    @staticmethod
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

    @staticmethod
    def _html_from_result(item: dict, payload: dict) -> str | None:
        markdown = item.get("markdown") or payload.get("markdown")
        if isinstance(markdown, dict):
            value = markdown.get("fit_html")
            if isinstance(value, str) and value.strip():
                return value
        for key in ("fit_html", "cleaned_html", "html"):
            value = item.get(key) or payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    async def _preflight_redirects(self, client: httpx.AsyncClient, url: str) -> str | None:
        if not self.validate_redirects:
            return url
        current = url
        for _ in range(self.max_preflight_redirects):
            response = await client.head(
                current,
                follow_redirects=False,
                headers=request_id_headers(),
            )
            if response.status_code not in REDIRECT_STATUS_CODES:
                return current
            location = response.headers.get("location")
            if not location:
                return None
            current = urljoin(current, location)
            if not self.url_safety(current):
                logger.warning(
                    "Crawl preflight dropped for %s: unsafe redirect target",
                    redact_url(url),
                )
                return None
        logger.warning("Crawl preflight dropped for %s: redirect chain too deep", redact_url(url))
        return None

    async def _fetch_payload(self, client: httpx.AsyncClient, source_url: str) -> dict | None:
        crawl_url = await self._preflight_redirects(client, source_url)
        if crawl_url is None:
            return None
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        response = await client.post(
            f"{self.base_url}/crawl",
            json=self._crawl_payload(crawl_url),
            headers=request_id_headers(headers),
        )
        if response.status_code != 200:
            logger.warning(
                "Crawl failed for %s: status %s",
                redact_url(source_url),
                response.status_code,
            )
            return None
        return response.json()

    def _page_from_payload(self, source_url: str, payload: dict) -> Page | None:
        for item in self._result_items(payload):
            if item.get("success") is False:
                continue
            if self._has_unsafe_final_url(source_url, item):
                return None
            markdown = self._markdown_from_result(item, payload)
            if not markdown:
                continue
            html = self._html_from_result(item, payload)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            title = item.get("title") or metadata.get("title") or source_url
            return Page(url=source_url, title=title, markdown=markdown, html=html)
        return None

    def _has_unsafe_final_url(self, source_url: str, item: dict) -> bool:
        final_url = item.get("redirected_url") or item.get("url")
        if final_url and final_url != source_url and not self.url_safety(final_url):
            logger.warning("Crawl dropped for %s: unsafe final URL", redact_url(source_url))
            return True
        return False

    async def _extract_one(
        self,
        client: httpx.AsyncClient,
        url: str,
        semaphore: asyncio.Semaphore,
        host_semaphores: dict[str, asyncio.Semaphore],
    ) -> Page | None:
        host = urlparse(url).hostname or ""
        async with semaphore:
            async with host_semaphores[host]:
                try:
                    payload = await self._fetch_payload(client, url)
                    return self._page_from_payload(url, payload) if payload is not None else None
                except Exception as exc:
                    logger.warning("Crawl failed for %s: %s", redact_url(url), exc.__class__.__name__)
                    return None

    async def _collect_pages(self, tasks: list[asyncio.Task[Page | None]]) -> list[Page]:
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
