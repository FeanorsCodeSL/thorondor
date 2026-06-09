"""Reranker client."""
import httpx

from ..types import Chunk, ScoredChunk


class RerankerUnavailable(Exception):
    pass


class RerankerClient:
    def __init__(self, endpoint: str, model: str, path: str = "/rerank"):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.path = path if path.startswith("/") else f"/{path}"

    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.endpoint}{self.path}",
                    json={"query": query, "documents": [c.text for c in chunks], "model": self.model},
                )
        except Exception as exc:
            raise RerankerUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise RerankerUnavailable(f"status {response.status_code}")
        payload = response.json()
        results = payload.get("results", payload.get("data", []))
        scored: list[ScoredChunk] = []
        for item in results:
            index = int(item["index"])
            if 0 <= index < len(chunks):
                score = item.get("score", item.get("relevance_score", item.get("rank_score", 0.0)))
                scored.append(ScoredChunk(chunks[index], float(score)))
        return scored
