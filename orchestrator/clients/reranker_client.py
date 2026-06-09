"""Reranker client."""
import httpx

from ..types import Chunk, ScoredChunk


class RerankerUnavailable(Exception):
    pass


class RerankerClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        path: str = "/rerank",
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.path = path if path.startswith("/") else f"/{path}"
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]:
        if not chunks:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            response = await self._client.post(
                f"{self.endpoint}{self.path}",
                headers=headers,
                json={"query": query, "documents": [c.text for c in chunks], "model": self.model},
            )
        except Exception as exc:
            raise RerankerUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise RerankerUnavailable(f"status {response.status_code}")
        try:
            payload = response.json()
            results = payload.get("results", payload.get("data", []))
            if not isinstance(results, list) or not results:
                raise ValueError("empty reranker results")

            scored: list[ScoredChunk] = []
            scored_indexes: set[int] = set()
            scored_values: list[float] = []
            for position, item in enumerate(results):
                if not isinstance(item, dict):
                    raise ValueError("malformed reranker item")
                index = int(item.get("index", position))
                if not 0 <= index < len(chunks) or index in scored_indexes:
                    continue
                score = float(item.get("score", item.get("relevance_score", item.get("rank_score", 0.0))))
                scored.append(ScoredChunk(chunks[index], score))
                scored_indexes.add(index)
                scored_values.append(score)

            if not scored:
                raise ValueError("no usable reranker results")
            if len(scored) < len(chunks):
                floor_score = min(scored_values) - 1.0
                scored.extend(
                    ScoredChunk(chunk, floor_score)
                    for index, chunk in enumerate(chunks)
                    if index not in scored_indexes
                )
            return scored
        except Exception as exc:
            raise RerankerUnavailable(str(exc)) from exc
