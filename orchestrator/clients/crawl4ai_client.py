"""Crawl4AI extraction client."""
import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from urllib.parse import urlparse
from weakref import WeakValueDictionary

import httpx

from ..observability import redact_url, request_id_headers
from ..outcome_codes import FetchOutcomeCode
from ..types import FetchStageOutcome, JsonValue, Page
from thorondor_contracts import (
    DEFAULT_ADMISSION_WAIT_S,
    DEFAULT_MAX_CONTENT_BYTES,
    DEFAULT_MAX_RESPONSE_BODY_BYTES,
    FETCH_CAPABILITIES,
)

logger = logging.getLogger(__name__)

MAX_TITLE_BYTES = 1024
MAX_FINAL_URL_BYTES = 8192
MAX_RESPONSE_HEADER_BYTES = 1024
MAX_METADATA_ITEMS = 32
MAX_METADATA_BYTES = 4096
MAX_LINK_ITEMS = 64
MAX_LINK_BYTES = 8192
MAX_JSON_DEPTH = 8
CHALLENGE_MARKERS = (
    "captcha",
    "cf-chl",
    "challenge-platform",
    "checking your connection before continuing",
    "please verify your browser",
    "verify you are human",
    "just a moment",
)
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG = re.compile(r"<[^>]+>")


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
    supported_capabilities = frozenset(FETCH_CAPABILITIES)

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
        max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES,
        max_response_body_bytes: int = DEFAULT_MAX_RESPONSE_BODY_BYTES,
        admission_wait_s: float = DEFAULT_ADMISSION_WAIT_S,
    ):
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if per_host_concurrency < 1:
            raise ValueError("per_host_concurrency must be >= 1")
        if not crawler_user_agent:
            raise ValueError("crawler_user_agent must not be blank")
        if max_content_bytes < 1:
            raise ValueError("max_content_bytes must be >= 1")
        if max_response_body_bytes < max_content_bytes:
            raise ValueError("max_response_body_bytes must be >= max_content_bytes")
        if admission_wait_s < 0:
            raise ValueError("admission_wait_s must be >= 0")
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.respect_robots_txt = respect_robots_txt
        self.per_host_concurrency = per_host_concurrency
        self.api_key = api_key
        self.crawler_user_agent = crawler_user_agent
        self.url_safety = url_safety
        self.max_content_bytes = max_content_bytes
        self.max_response_body_bytes = max_response_body_bytes
        self.admission_wait_s = admission_wait_s
        self._global_semaphore = asyncio.Semaphore(concurrency)
        self._host_semaphores: WeakValueDictionary[str, asyncio.Semaphore] = (
            WeakValueDictionary()
        )
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
        outcomes = await self.fetch(
            urls,
            self.supported_capabilities,
            False,
        )
        return [
            outcome.page
            for outcome in outcomes
            if outcome.code == FetchOutcomeCode.CONTENT and outcome.page is not None
        ]

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

    async def fetch(
        self,
        urls: list[str],
        capabilities: frozenset[str],
        include_raw_html: bool,
    ) -> list[FetchStageOutcome]:
        if not urls:
            return []
        requested_capabilities = frozenset(capabilities)
        if include_raw_html:
            requested_capabilities |= {"raw_html"}
        tasks = [
            (
                url,
                asyncio.create_task(
                    self._guarded_fetch_one_outcome(url, requested_capabilities)
                ),
            )
            for url in urls
        ]
        try:
            done, pending = await asyncio.wait(
                [task for _url, task in tasks],
                timeout=self.timeout_s,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return [
                task.result()
                if task in done
                else FetchStageOutcome(
                    requested_url=url,
                    final_url=None,
                    code=FetchOutcomeCode.UPSTREAM_TIMEOUT,
                    retrieval_method="crawl4ai_browser",
                    elapsed_ms=int(self.timeout_s * 1000),
                )
                for url, task in tasks
            ]
        except BaseException:
            for _url, task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for _url, task in tasks),
                return_exceptions=True,
            )
            raise

    async def _guarded_fetch_one_outcome(
        self,
        source_url: str,
        capabilities: frozenset[str],
    ) -> FetchStageOutcome:
        started = time.perf_counter()
        try:
            return await self._fetch_one_outcome(source_url, capabilities)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Crawl failed during local processing for %s", redact_url(source_url))
            return self._failure_outcome(
                source_url,
                FetchOutcomeCode.LOCAL_PROCESSING_FAILURE,
                started,
            )

    async def _fetch_one_outcome(
        self,
        source_url: str,
        capabilities: frozenset[str],
    ) -> FetchStageOutcome:
        started = time.perf_counter()
        host = urlparse(source_url).hostname or ""
        host_semaphore = self._host_semaphores.get(host)
        if host_semaphore is None:
            host_semaphore = asyncio.Semaphore(self.per_host_concurrency)
            self._host_semaphores[host] = host_semaphore
        global_acquired = await self._acquire(self._global_semaphore)
        if not global_acquired:
            return self._failure_outcome(
                source_url,
                FetchOutcomeCode.CAPACITY_UNAVAILABLE,
                started,
            )
        host_acquired = False
        try:
            host_acquired = await self._acquire(host_semaphore)
            if not host_acquired:
                return self._failure_outcome(
                    source_url,
                    FetchOutcomeCode.CAPACITY_UNAVAILABLE,
                    started,
                )
            headers = (
                {"Authorization": f"Bearer {self.api_key}"}
                if self.api_key
                else None
            )
            try:
                request = self._client.build_request(
                    "POST",
                    f"{self.base_url}/crawl",
                    json=self._crawl_payload(source_url),
                    headers=request_id_headers(headers),
                )
                response = await self._client.send(request, stream=True)
                try:
                    if response.status_code == 429:
                        return self._failure_outcome(
                            source_url,
                            FetchOutcomeCode.RATE_LIMITED,
                            started,
                            status_code=429,
                        )
                    if response.status_code != 200:
                        return self._failure_outcome(
                            source_url,
                            FetchOutcomeCode.UPSTREAM_FAILURE,
                            started,
                            status_code=response.status_code,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self.max_response_body_bytes:
                            return self._failure_outcome(
                                source_url,
                                FetchOutcomeCode.CONTENT_TOO_LARGE,
                                started,
                                status_code=response.status_code,
                            )
                        body.extend(chunk)
                    try:
                        payload = json.loads(bytes(body))
                    except (ValueError, UnicodeError):
                        return self._failure_outcome(
                            source_url,
                            FetchOutcomeCode.MALFORMED_UPSTREAM_RESPONSE,
                            started,
                        )
                finally:
                    await response.aclose()
            except httpx.TimeoutException:
                return self._failure_outcome(
                    source_url,
                    FetchOutcomeCode.UPSTREAM_TIMEOUT,
                    started,
                )
            except httpx.HTTPError:
                return self._failure_outcome(
                    source_url,
                    FetchOutcomeCode.UPSTREAM_FAILURE,
                    started,
                )
            return await self._outcome_from_payload(
                source_url,
                payload,
                capabilities,
                started,
            )
        finally:
            if host_acquired:
                host_semaphore.release()
            self._global_semaphore.release()

    async def _acquire(self, semaphore: asyncio.Semaphore) -> bool:
        if self.admission_wait_s == 0:
            if semaphore.locked():
                return False
            await semaphore.acquire()
            return True
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=self.admission_wait_s,
            )
            return True
        except TimeoutError:
            return False

    @staticmethod
    def _failure_outcome(
        source_url: str,
        code: FetchOutcomeCode,
        started: float,
        status_code: int | None = None,
    ) -> FetchStageOutcome:
        return FetchStageOutcome(
            requested_url=source_url,
            final_url=None,
            code=code,
            retrieval_method="crawl4ai_browser",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            status_code=status_code,
        )

    @staticmethod
    def _strict_result_items(payload: object) -> list[dict] | None:
        if not isinstance(payload, dict):
            return None
        if "results" in payload:
            results = payload["results"]
            if not isinstance(results, list) or not results:
                return None
            return results if all(isinstance(item, dict) for item in results) else None
        recognized = {
            "success",
            "url",
            "redirected_url",
            "markdown",
            "content",
            "fit_markdown",
            "raw_markdown",
            "html",
            "cleaned_html",
            "fit_html",
        }
        return [payload] if recognized & payload.keys() else None

    @staticmethod
    def _is_challenge(markdown: str, html: str | None) -> bool:
        sample = f"{markdown}\n{html or ''}"[:16384].casefold()
        return any(marker in sample for marker in CHALLENGE_MARKERS)

    @staticmethod
    def _is_empty_shell(html: str | None) -> bool:
        if not html or "<script" not in html.casefold():
            return False
        without_scripts = _SCRIPT_STYLE.sub(" ", html)
        visible = " ".join(_HTML_TAG.sub(" ", without_scripts).split())
        return len(visible) < 32

    @staticmethod
    def _structured_robots_denial(item: dict, metadata: dict) -> bool:
        return item.get("robots_denied") is True or metadata.get("robots_denied") is True

    @staticmethod
    def _unsupported_content_code(
        content_type: str | None,
        capabilities: frozenset[str],
    ) -> FetchOutcomeCode | None:
        if not content_type:
            return None
        media_type = content_type.split(";", 1)[0].strip().casefold()
        if media_type == "application/pdf":
            return None if "pdf" in capabilities else FetchOutcomeCode.UNSUPPORTED_CAPABILITY
        document_types = {
            "application/msword",
            "application/rtf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.oasis.opendocument.text",
        }
        if media_type in document_types:
            return None if "document" in capabilities else FetchOutcomeCode.UNSUPPORTED_CAPABILITY
        if media_type.startswith("text/") or media_type in {
            "application/xml",
            "application/rss+xml",
            "application/xhtml+xml",
            "application/json",
        }:
            return None
        return FetchOutcomeCode.UNSUPPORTED_CONTENT

    async def _outcome_from_payload(
        self,
        source_url: str,
        payload: object,
        capabilities: frozenset[str],
        started: float,
    ) -> FetchStageOutcome:
        items = self._strict_result_items(payload)
        if items is None:
            return self._failure_outcome(
                source_url,
                FetchOutcomeCode.MALFORMED_UPSTREAM_RESPONSE,
                started,
            )
        item = items[0]
        final_url = self._final_url(source_url, item)
        if not _is_utf8_within_limit(final_url, MAX_FINAL_URL_BYTES):
            return self._failure_outcome(
                source_url,
                FetchOutcomeCode.MALFORMED_UPSTREAM_RESPONSE,
                started,
            )
        status_code = self._status_code(item)
        raw_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        metadata = _bounded_json_mapping(raw_metadata, MAX_METADATA_ITEMS, MAX_METADATA_BYTES)
        links = _bounded_json_mapping(item.get("links"), MAX_LINK_ITEMS, MAX_LINK_BYTES)
        title = self._title_from_result(item, raw_metadata, source_url)
        (
            content_type,
            etag,
            last_modified,
            retry_after,
        ) = self._allowlisted_response_headers(item)
        markdown = self._markdown_from_result(item, payload if isinstance(payload, dict) else {})
        html = self._html_from_result(item, payload if isinstance(payload, dict) else {})
        base = {
            "requested_url": source_url,
            "final_url": final_url,
            "retrieval_method": "crawl4ai_browser",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "status_code": status_code,
            "content_type": content_type,
            "title": title,
            "links": links,
            "metadata": metadata,
            "etag": etag,
            "last_modified": last_modified,
            "retry_after": retry_after,
        }
        if await self._has_unsafe_final_url(source_url, final_url):
            return FetchStageOutcome(code=FetchOutcomeCode.UNSAFE_REDIRECT, **base)
        if status_code == 429:
            return FetchStageOutcome(code=FetchOutcomeCode.RATE_LIMITED, **base)
        if self._structured_robots_denial(item, raw_metadata):
            return FetchStageOutcome(code=FetchOutcomeCode.ROBOTS_REFUSED, **base)
        if self._is_challenge(markdown, html):
            return FetchStageOutcome(code=FetchOutcomeCode.CHALLENGE, **base)
        if item.get("success") is False:
            return FetchStageOutcome(code=FetchOutcomeCode.UPSTREAM_FAILURE, **base)
        unsupported = self._unsupported_content_code(content_type, capabilities)
        if unsupported is not None:
            return FetchStageOutcome(code=unsupported, **base)
        content_bytes = len(markdown.encode("utf-8", "replace"))
        if html:
            content_bytes += len(html.encode("utf-8", "replace"))
        if content_bytes > self.max_content_bytes:
            return FetchStageOutcome(code=FetchOutcomeCode.CONTENT_TOO_LARGE, **base)
        if not markdown:
            code = (
                FetchOutcomeCode.EMPTY_SHELL
                if self._is_empty_shell(html)
                else FetchOutcomeCode.EXTRACTION_EMPTY
            )
            return FetchStageOutcome(code=code, **base)
        page = Page(
            url=source_url,
            title=title,
            markdown=markdown,
            html=html,
            requested_url=source_url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
            retry_after=retry_after,
            metadata=metadata,
            links=links,
        )
        return FetchStageOutcome(
            code=FetchOutcomeCode.CONTENT,
            page=page,
            **base,
        )

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
    def _html_from_result(
        item: dict,
        payload: dict,
        content_type: str | None = None,
    ) -> str | None:
        media_type = (content_type or "").split(";", 1)[0].strip().casefold()
        if media_type in {"application/xml", "application/rss+xml", "text/xml"}:
            value = item.get("html") or payload.get("html")
            if isinstance(value, str) and value.strip():
                return value
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
            raw_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata = _bounded_json_mapping(raw_metadata, MAX_METADATA_ITEMS, MAX_METADATA_BYTES)
            links = _bounded_json_mapping(item.get("links"), MAX_LINK_ITEMS, MAX_LINK_BYTES)
            title = self._title_from_result(item, raw_metadata, source_url)
            (
                content_type,
                etag,
                last_modified,
                retry_after,
            ) = self._allowlisted_response_headers(item)
            html = self._html_from_result(item, payload, content_type)
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
                retry_after=retry_after,
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
    def _allowlisted_response_headers(
        item: dict,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        headers = item.get("response_headers")
        if not isinstance(headers, dict):
            return None, None, None, None
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
            if (
                name in {"content-type", "etag", "last-modified", "retry-after"}
                and name not in values
            ):
                values[name] = value
        return tuple(
            value if _is_utf8_within_limit(value, MAX_RESPONSE_HEADER_BYTES) else None
            for value in (
                values.get("content-type"),
                values.get("etag"),
                values.get("last-modified"),
                values.get("retry-after"),
            )
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
