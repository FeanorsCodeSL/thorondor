"""Crawl4AI extraction client."""
import asyncio
import logging

import httpx

from ..types import Page

logger = logging.getLogger(__name__)


class Crawl4aiExtractor:
    def __init__(self, base_url: str, concurrency: int, timeout_s: int):
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.timeout_s = timeout_s

    async def extract(self, urls: list[str]) -> list[Page]:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(url: str) -> Page | None:
            async with semaphore:
                try:
                    async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                        response = await client.post(f"{self.base_url}/crawl", json={"url": url})
                    if response.status_code != 200:
                        logger.warning("Crawl failed for %s: status %s", url, response.status_code)
                        return None
                    payload = response.json()
                    markdown = payload.get("markdown") or payload.get("content") or ""
                    title = payload.get("title") or url
                    return Page(url=url, title=title, markdown=markdown) if markdown else None
                except Exception as exc:
                    logger.warning("Crawl failed for %s: %s", url, exc)
                    return None

        pages = await asyncio.gather(*(one(url) for url in urls))
        return [page for page in pages if page is not None]
