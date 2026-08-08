"""Reranker client."""
import httpx

from ..observability import request_id_headers
from ..types import Chunk, RerankerTelemetry, RerankOutcome, ScoreComponent, ScoredChunk

RERANKER_STRATEGY = "external-reranker@1"
RERANKER_FLOOR_STRATEGY = "reranker-partial-floor@1"


class RerankerUnavailable(Exception):
    def __init__(self, message: str, telemetry: RerankerTelemetry | None = None):
        super().__init__(message)
        self.telemetry = telemetry or RerankerTelemetry()


class RerankerClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        path: str,
        batch_size: int,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.path = path if path.startswith("/") else f"/{path}"
        self.api_key = api_key
        self.batch_size = batch_size
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def rerank(self, query: str, chunks: list[Chunk]) -> RerankOutcome:
        if not chunks:
            return RerankOutcome([], RerankerTelemetry(), RERANKER_STRATEGY)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        headers = request_id_headers(headers)
        scored: list[ScoredChunk] = []
        scored_indexes: set[int] = set()
        scored_values: list[float] = []
        batches = 0
        batches_failed = 0

        for offset in range(0, len(chunks), self.batch_size):
            batch = chunks[offset : offset + self.batch_size]
            batches += 1
            try:
                response = await self._client.post(
                    f"{self.endpoint}{self.path}",
                    headers=headers,
                    json={"query": query, "documents": [c.text for c in batch], "model": self.model},
                )
                if response.status_code != 200:
                    raise ValueError(f"status {response.status_code}")
                batch_scored, batch_indexes, batch_scores = self._parse_payload(
                    response.json(),
                    chunks,
                    offset,
                    len(batch),
                )
                scored.extend(batch_scored)
                scored_indexes.update(batch_indexes)
                scored_values.extend(batch_scores)
            except Exception:
                batches_failed += 1

        if not scored:
            raise RerankerUnavailable(
                "no usable reranker results",
                RerankerTelemetry(
                    batches=batches,
                    batches_failed=batches_failed,
                ),
            )

        floor_filled = False
        if len(scored) < len(chunks):
            floor_score = min(scored_values) - 1.0
            scored.extend(
                ScoredChunk(
                    chunk,
                    floor_score,
                    (
                        ScoreComponent(
                            "reranker_floor",
                            floor_score,
                            RERANKER_FLOOR_STRATEGY,
                        ),
                    ),
                )
                for index, chunk in enumerate(chunks)
                if index not in scored_indexes
            )
            floor_filled = True
        return RerankOutcome(
            scored,
            RerankerTelemetry(
                batches=batches,
                batches_failed=batches_failed,
                floor_filled=floor_filled,
                scored_count=len(scored_indexes),
            ),
            RERANKER_STRATEGY,
        )

    def _parse_payload(
        self,
        payload: dict,
        chunks: list[Chunk],
        offset: int,
        batch_len: int,
    ) -> tuple[list[ScoredChunk], set[int], list[float]]:
        results = payload.get("results", payload.get("data", []))
        if not isinstance(results, list) or not results:
            raise ValueError("empty reranker results")

        scored: list[ScoredChunk] = []
        scored_indexes: set[int] = set()
        scored_values: list[float] = []
        for position, item in enumerate(results):
            if not isinstance(item, dict):
                raise ValueError("malformed reranker item")
            local_index = int(item.get("index", position))
            if not 0 <= local_index < batch_len:
                continue
            index = offset + local_index
            if index in scored_indexes:
                continue
            score = float(item.get("score", item.get("relevance_score", item.get("rank_score", 0.0))))
            scored.append(
                ScoredChunk(
                    chunks[index],
                    score,
                    (ScoreComponent("reranker", score, RERANKER_STRATEGY),),
                )
            )
            scored_indexes.add(index)
            scored_values.append(score)

        if not scored:
            raise ValueError("no usable reranker results")
        return scored, scored_indexes, scored_values
