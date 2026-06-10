"""Semantic chunking service client."""
import httpx

from ..observability import request_id_headers
from ..types import Chunk, Page


class ChunkerUnavailable(Exception):
    pass


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
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        headers = request_id_headers(headers)
        try:
            for page in pages:
                response = await self._client.post(
                    f"{self.base_url}/chunk",
                    headers=headers,
                    json={
                        "text": page.markdown,
                        "source_type": "WEB_MARKDOWN",
                        "metadata": {"source_url": page.url, "title": page.title, "source_id": page.source_id},
                    },
                )
                if response.status_code != 200:
                    if 400 <= response.status_code < 500:
                        continue
                    raise ChunkerUnavailable(f"status {response.status_code}")
                payload = response.json()
                chunk_strategy = payload.get("chunk_strategy") or payload.get("strategy_version")
                embedding_degraded = bool(payload.get("embedding_degraded", False))
                for item in payload.get("chunks", []):
                    metadata = item.get("metadata", {})
                    raw_source_id = metadata.get("source_id", page.source_id)
                    item_strategy = metadata.get("chunk_strategy", chunk_strategy)
                    item_degraded = bool(metadata.get("embedding_degraded", embedding_degraded))
                    chunks.append(
                        Chunk(
                            text=item["text"],
                            token_count=int(item["token_count"]),
                            source_url=metadata.get("source_url", page.url),
                            title=metadata.get("title", page.title),
                            position=int(item.get("position", 0)),
                            source_id=int(raw_source_id) if raw_source_id is not None else None,
                            chunk_strategy=item_strategy,
                            embedding_degraded=item_degraded,
                        )
                    )
        except ChunkerUnavailable:
            raise
        except Exception as exc:
            raise ChunkerUnavailable(str(exc)) from exc
        return chunks
