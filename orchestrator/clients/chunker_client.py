"""Semantic chunking service client."""
from bisect import bisect_left
import re

import httpx

from ..observability import request_id_headers
from ..types import Chunk, Page
from ..url_identity import build_document_identity, evidence_id_for

_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|$)")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
MAX_SECTION_HEADING_BYTES = 512


class ChunkerUnavailable(Exception):
    pass


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


def _section_headings(document: str) -> list[tuple[int, str]]:
    headings = []
    fence = None
    offset = 0
    for line in document.splitlines(keepends=True):
        fence_match = _FENCE.match(line)
        if fence is None and fence_match:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            offset += len(line)
            continue
        if fence is not None:
            if fence_match:
                marker = fence_match.group(1)
                remainder = line[fence_match.end():].strip()
                if marker[0] == fence[0] and len(marker) >= fence[1] and not remainder:
                    fence = None
            offset += len(line)
            continue
        heading_match = _HEADING.match(line)
        if heading_match:
            heading = " ".join(heading_match.group(1).split())
            heading = heading.encode("utf-8")[:MAX_SECTION_HEADING_BYTES].decode(
                "utf-8",
                "ignore",
            )
            if heading:
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
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        chunks: list[Chunk] = []
        rejected_statuses = []
        successful_responses = 0
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        headers = request_id_headers(headers)
        try:
            for page in pages:
                final_url = page.final_url or page.url
                document_identity = build_document_identity(final_url, page.markdown)
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
                        rejected_statuses.append(response.status_code)
                        continue
                    raise ChunkerUnavailable(f"status {response.status_code}")
                successful_responses += 1
                payload = response.json()
                chunk_strategy = payload.get("chunk_strategy") or payload.get("strategy_version")
                embedding_degraded = bool(payload.get("embedding_degraded", False))
                section_headings = _section_headings(page.markdown)
                for item in payload.get("chunks", []):
                    metadata = item.get("metadata", {})
                    item_strategy = metadata.get("chunk_strategy", chunk_strategy)
                    item_degraded = bool(metadata.get("embedding_degraded", embedding_degraded))
                    span = _exact_span(item, page.markdown)
                    start_index, end_index = span if span is not None else (None, None)
                    evidence_id = (
                        evidence_id_for(
                            document_identity.final_url,
                            document_identity.cleaned_markdown_sha256,
                            start_index,
                            end_index,
                        )
                        if start_index is not None and end_index is not None
                        else None
                    )
                    chunks.append(
                        Chunk(
                            text=item["text"],
                            token_count=int(item["token_count"]),
                            source_url=final_url,
                            title=page.title,
                            position=int(item.get("position", 0)),
                            source_id=page.source_id,
                            chunk_strategy=item_strategy,
                            embedding_degraded=item_degraded,
                            start_index=start_index,
                            end_index=end_index,
                            verbatim=span is not None,
                            document_id=document_identity.document_id,
                            evidence_id=evidence_id,
                            final_url=document_identity.final_url,
                            cleaned_markdown_sha256=document_identity.cleaned_markdown_sha256,
                            section_heading=(
                                _nearest_section_heading(section_headings, start_index)
                                if start_index is not None
                                else None
                            ),
                            evidence_metadata=page.evidence_metadata,
                        )
                    )
            if pages and successful_responses == 0 and rejected_statuses:
                statuses = ",".join(str(status) for status in sorted(set(rejected_statuses)))
                raise ChunkerUnavailable(f"all pages rejected with status {statuses}")
        except ChunkerUnavailable:
            raise
        except Exception as exc:
            raise ChunkerUnavailable(str(exc)) from exc
        return chunks
