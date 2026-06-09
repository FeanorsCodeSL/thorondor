"""Semantic chunking service client."""
import httpx

from ..types import Chunk, Page


class ChunkerUnavailable(Exception):
    pass


class ChunkerClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def chunk(self, pages: list[Page]) -> list[Chunk]:
        chunks: list[Chunk] = []
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for page in pages:
                    response = await client.post(
                        f"{self.base_url}/chunk",
                        json={
                            "text": page.markdown,
                            "source_type": "WEB_MARKDOWN",
                            "metadata": {"source_url": page.url, "title": page.title},
                        },
                    )
                    if response.status_code != 200:
                        raise ChunkerUnavailable(f"status {response.status_code}")
                    payload = response.json()
                    for item in payload.get("chunks", []):
                        metadata = item.get("metadata", {})
                        chunks.append(
                            Chunk(
                                text=item["text"],
                                token_count=int(item["token_count"]),
                                source_url=metadata.get("source_url", page.url),
                                title=metadata.get("title", page.title),
                                position=int(item.get("position", 0)),
                            )
                        )
        except ChunkerUnavailable:
            raise
        except Exception as exc:
            raise ChunkerUnavailable(str(exc)) from exc
        return chunks
