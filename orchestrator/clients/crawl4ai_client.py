"""Crawl4AI extraction client."""
import asyncio
from collections import defaultdict
import json
import logging
import math
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from urllib.parse import urlparse

import httpx

from ..observability import redact_url, request_id_headers
from ..types import JsonValue, Page

logger = logging.getLogger(__name__)

MAX_TITLE_BYTES = 1024
MAX_FINAL_URL_BYTES = 8192
MAX_RESPONSE_HEADER_BYTES = 1024
MAX_METADATA_ITEMS = 32
MAX_METADATA_BYTES = 4096
MAX_LINK_ITEMS = 64
MAX_LINK_BYTES = 8192
MAX_JSON_DEPTH = 8


def _json_size(value: object) -> int | None:
    try:
        return len(
            json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError, UnicodeError):
        return None


def _is_json_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _fits_json_budget(value: object, max_bytes: int) -> bool:
    size = _json_size(value)
    return size is not None and size <= max_bytes


def _bounded_utf8(value: str, max_bytes: int) -> str:
    return value.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore")


def _is_utf8_within_limit(value: str | None, max_bytes: int) -> bool:
    if value is None:
        return False
    try:
        return len(value.encode("utf-8")) <= max_bytes
    except UnicodeError:
        return False


def _append_json_mapping(
    source: dict[object, object],
    target: dict[str, JsonValue],
    root: dict[str, JsonValue],
    remaining_items: list[int],
    max_bytes: int,
    depth: int,
) -> None:
    entries = sorted(
        ((key, value) for key, value in source.items() if isinstance(key, str)),
        key=lambda entry: entry[0],
    )
    for key, value in entries:
        if remaining_items[0] == 0:
            return
        outcome = _append_json_value(target, key, value, root, remaining_items, max_bytes, depth)
        if outcome == "truncated":
            return


def _append_json_list(
    source: list[object],
    target: list[JsonValue],
    root: dict[str, JsonValue],
    remaining_items: list[int],
    max_bytes: int,
    depth: int,
) -> None:
    for value in source:
        if remaining_items[0] == 0:
            return
        outcome = _append_json_value(target, None, value, root, remaining_items, max_bytes, depth)
        if outcome == "truncated":
            return


def _append_json_value(
    target: dict[str, JsonValue] | list[JsonValue],
    key: str | None,
    value: object,
    root: dict[str, JsonValue],
    remaining_items: list[int],
    max_bytes: int,
    depth: int,
) -> str:
    if _is_json_scalar(value):
        candidate: JsonValue = value
    elif depth >= MAX_JSON_DEPTH:
        return "invalid"
    elif isinstance(value, dict):
        candidate = {}
    elif isinstance(value, list):
        candidate = []
    else:
        return "invalid"

    if isinstance(target, dict):
        if key is None:
            return "invalid"
        target[key] = candidate
    else:
        target.append(candidate)

    if not _fits_json_budget(root, max_bytes):
        if isinstance(target, dict):
            del target[key]
        else:
            target.pop()
        return "truncated"

    remaining_items[0] -= 1
    if isinstance(value, dict):
        _append_json_mapping(value, candidate, root, remaining_items, max_bytes, depth + 1)
    elif isinstance(value, list):
        _append_json_list(value, candidate, root, remaining_items, max_bytes, depth + 1)
    return "included"


def _bounded_json_mapping(value: object, max_items: int, max_bytes: int) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, JsonValue] = {}
    _append_json_mapping(value, result, result, [max_items], max_bytes, 0)
    return result


class Crawl4aiExtractor:
    def __init__(
        self,
        base_url: str,
        concurrency: int,
        timeout_s: float,
        respect_robots_txt: bool,
        per_host_concurrency: int,
        crawler_user_agent: str,
        url_safety: Callable[[str], bool | Awaitable[bool]],
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ):
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if per_host_concurrency < 1:
            raise ValueError("per_host_concurrency must be >= 1")
        if not crawler_user_agent:
            raise ValueError("crawler_user_agent must not be blank")
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.respect_robots_txt = respect_robots_txt
        self.per_host_concurrency = per_host_concurrency
        self.api_key = api_key
        self.crawler_user_agent = crawler_user_agent
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
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"user_agent": self.crawler_user_agent},
            },
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
            for key in ("fit_markdown", "raw_markdown", "markdown_with_citations"):
                value = markdown.get(key)
                if isinstance(value, str) and value:
                    return value
            return ""
        if isinstance(markdown, str):
            return markdown
        for key in ("content", "fit_markdown", "raw_markdown"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

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

    async def _fetch_payload(self, client: httpx.AsyncClient, source_url: str) -> dict | None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        response = await client.post(
            f"{self.base_url}/crawl",
            json=self._crawl_payload(source_url),
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
            final_url = self._final_url(source_url, item)
            if not _is_utf8_within_limit(final_url, MAX_FINAL_URL_BYTES):
                return None
            markdown = self._markdown_from_result(item, payload)
            if not markdown:
                continue
            html = self._html_from_result(item, payload)
            raw_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata = _bounded_json_mapping(raw_metadata, MAX_METADATA_ITEMS, MAX_METADATA_BYTES)
            links = _bounded_json_mapping(item.get("links"), MAX_LINK_ITEMS, MAX_LINK_BYTES)
            title = self._title_from_result(item, raw_metadata, source_url)
            content_type, etag, last_modified = self._allowlisted_response_headers(item)
            return Page(
                url=source_url,
                title=title,
                markdown=markdown,
                html=html,
                requested_url=source_url,
                final_url=final_url,
                status_code=self._status_code(item),
                content_type=content_type,
                etag=etag,
                last_modified=last_modified,
                metadata=metadata,
                links=links,
            )
        return None

    @staticmethod
    def _final_url(source_url: str, item: dict) -> str:
        for value in (item.get("redirected_url"), item.get("url"), source_url):
            if isinstance(value, str) and value:
                return value
        return source_url

    @staticmethod
    def _status_code(item: dict) -> int | None:
        value = item.get("status_code")
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
        return None

    @staticmethod
    def _title_from_result(item: dict, metadata: dict, source_url: str) -> str:
        for value in (item.get("title"), metadata.get("title"), source_url):
            if isinstance(value, str) and value:
                return _bounded_utf8(value, MAX_TITLE_BYTES)
        return _bounded_utf8(source_url, MAX_TITLE_BYTES)

    @staticmethod
    def _allowlisted_response_headers(item: dict) -> tuple[str | None, str | None, str | None]:
        headers = item.get("response_headers")
        if not isinstance(headers, dict):
            return None, None, None
        values: dict[str, str] = {}
        entries = sorted(
            (
                (name.lower(), name, value)
                for name, value in headers.items()
                if isinstance(name, str) and name.isascii() and isinstance(value, str) and value
            ),
            key=lambda entry: (entry[0], entry[1]),
        )
        for name, _original_name, value in entries:
            if name in {"content-type", "etag", "last-modified"} and name not in values:
                values[name] = value
        return tuple(
            value if _is_utf8_within_limit(value, MAX_RESPONSE_HEADER_BYTES) else None
            for value in (values.get("content-type"), values.get("etag"), values.get("last-modified"))
        )

    async def _has_unsafe_final_url(self, source_url: str, final_url: str) -> bool:
        if final_url == source_url:
            return False
        try:
            is_safe = self.url_safety(final_url)
            if isawaitable(is_safe):
                is_safe = await is_safe
        except Exception:
            is_safe = False
        if is_safe is not True:
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
                    page = self._page_from_payload(url, payload) if payload is not None else None
                    if page is None or await self._has_unsafe_final_url(url, page.final_url or url):
                        return None
                    return page
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
