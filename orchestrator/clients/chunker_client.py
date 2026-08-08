"""Semantic chunking service client."""
from bisect import bisect_left
import asyncio
import re
from dataclasses import dataclass

import httpx

from ..observability import request_id_headers
from ..types import Chunk, Page
from ..url_identity import build_document_identity, evidence_id_for

_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+)(?:\r?\n|$)")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
MAX_SECTION_HEADING_BYTES = 512


@dataclass(frozen=True)
class _ChunkContext:
    final_url: str
    document_id: str
    cleaned_markdown_sha256: str
    chunk_strategy: str | None
    embedding_degraded: bool
    section_headings: list[tuple[int, str]]


class ChunkerUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _exact_span(item: dict, document: str) -> tuple[int, int] | None:
    start = item.get("start_index")
    end = item.get("end_index")
    text = item.get("text")
    if item.get("verbatim") is not True:
        return None
    if type(start) is not int or type(end) is not int or not isinstance(text, str):
        return None
    if start < 0 or end <= start or end > len(document):
        return None
    return (start, end) if document[start:end] == text else None


def _fence_state(
    line: str,
    fence: tuple[str, int] | None,
) -> tuple[tuple[str, int] | None, bool]:
    match = _FENCE.match(line)
    if fence is None:
        if match is None:
            return None, False
        marker = match.group(1)
        return (marker[0], len(marker)), True
    if match is not None:
        marker = match.group(1)
        remainder = line[match.end():].strip()
        if marker[0] == fence[0] and len(marker) >= fence[1] and not remainder:
            return None, True
    return fence, True


def _heading(line: str) -> str | None:
    match = _HEADING.match(line)
    if match is None:
        return None
    heading = " ".join(match.group(1).rstrip(" \t#").split())
    heading = heading.encode("utf-8")[:MAX_SECTION_HEADING_BYTES].decode("utf-8", "ignore")
    return heading or None


def _section_headings(document: str) -> list[tuple[int, str]]:
    headings = []
    fence = None
    offset = 0
    for line in document.splitlines(keepends=True):
        fence, inside_fence = _fence_state(line, fence)
        if not inside_fence:
            heading = _heading(line)
            if heading is not None:
                headings.append((offset, heading))
        offset += len(line)
    return headings


def _nearest_section_heading(
    headings: list[tuple[int, str]],
    start_index: int,
) -> str | None:
    index = bisect_left(headings, (start_index + 1, "")) - 1
    if index < 0:
        return None
    return headings[index][1]


class ChunkerClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        concurrency: int = 4,
        timeout_s: float = 45.0,
    ):
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            limits=httpx.Limits(
                max_connections=concurrency,
                max_keepalive_connections=concurrency,
            ),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        headers = request_id_headers(headers)
        try:
            async with asyncio.timeout(self.timeout_s):
                results = await asyncio.gather(
                    *(self._chunk_page(page, headers) for page in pages)
                )
            chunks = [chunk for page_chunks, _status in results for chunk in page_chunks]
            rejected_statuses = [status for _chunks, status in results if status is not None]
            successful_responses = sum(status is None for _chunks, status in results)
            if pages and successful_responses == 0 and rejected_statuses:
                statuses = ",".join(str(status) for status in sorted(set(rejected_statuses)))
                raise ChunkerUnavailable(f"all pages rejected with status {statuses}")
            return chunks
        except TimeoutError as exc:
            raise ChunkerUnavailable("timeout") from exc
        except ChunkerUnavailable:
            raise
        except Exception as exc:
            raise ChunkerUnavailable(str(exc)) from exc

    async def _chunk_page(
        self,
        page: Page,
        headers: dict[str, str] | None,
    ) -> tuple[list[Chunk], int | None]:
        final_url = page.final_url or page.url
        document_identity = build_document_identity(final_url, page.markdown)
        async with self._semaphore:
            response = await self._client.post(
                f"{self.base_url}/chunk",
                headers=headers,
                json={
                    "text": page.markdown,
                    "source_type": "ORCHESTRATOR_MARKDOWN",
                    "metadata": {
                        "source_url": final_url,
                        "title": page.title,
                        "source_id": page.source_id,
                        "document_id": document_identity.document_id,
                        "cleaned_markdown_sha256": document_identity.cleaned_markdown_sha256,
                    },
                },
            )
        if response.status_code != 200:
            if 400 <= response.status_code < 500:
                return [], response.status_code
            raise ChunkerUnavailable(f"status {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("chunks", []), list):
            raise ChunkerUnavailable("malformed_response")
        context = _ChunkContext(
            final_url=final_url,
            document_id=document_identity.document_id,
            cleaned_markdown_sha256=document_identity.cleaned_markdown_sha256,
            chunk_strategy=payload.get("chunk_strategy") or payload.get("strategy_version"),
            embedding_degraded=bool(payload.get("embedding_degraded", False)),
            section_headings=_section_headings(page.markdown),
        )
        chunks = []
        for item in payload.get("chunks", []):
            chunks.append(self._chunk_from_item(page, item, context))
        return chunks, None

    @staticmethod
    def _chunk_from_item(page: Page, item: object, context: _ChunkContext) -> Chunk:
        if not isinstance(item, dict):
            raise ChunkerUnavailable("malformed_response")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        span = _exact_span(item, page.markdown)
        start_index, end_index = span if span is not None else (None, None)
        evidence_id = None
        section_heading = None
        if start_index is not None and end_index is not None:
            evidence_id = evidence_id_for(
                context.final_url,
                context.cleaned_markdown_sha256,
                start_index,
                end_index,
            )
            section_heading = _nearest_section_heading(context.section_headings, start_index)
        return Chunk(
            text=item["text"],
            token_count=int(item["token_count"]),
            source_url=context.final_url,
            title=page.title,
            position=int(item.get("position", 0)),
            source_id=page.source_id,
            chunk_strategy=metadata.get("chunk_strategy", context.chunk_strategy),
            embedding_degraded=bool(
                metadata.get("embedding_degraded", context.embedding_degraded)
            ),
            start_index=start_index,
            end_index=end_index,
            verbatim=span is not None,
            document_id=context.document_id,
            evidence_id=evidence_id,
            final_url=context.final_url,
            cleaned_markdown_sha256=context.cleaned_markdown_sha256,
            section_heading=section_heading,
            evidence_metadata=page.evidence_metadata,
        )
