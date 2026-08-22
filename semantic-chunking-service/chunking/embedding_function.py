"""
Embedding Function for ClusterSemanticChunker.

This module provides an embedding function compatible with ClusterSemanticChunker
that uses the OpenAI-compatible embedding API (/v1/embeddings) to generate embeddings.
Works with any server that speaks /v1/embeddings (vLLM, text-embeddings-inference,
Infinity, Ollama, ...).
"""
import logging
import time
from typing import Callable, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from .observability import redact_url, request_id_headers

logger = logging.getLogger(__name__)


def _normalize_embedding_endpoint(endpoint: str) -> str:
    """Normalize an embedding endpoint while preserving scheme, host, and path."""
    raw = endpoint.strip().rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if not parsed.hostname:
        raise ValueError(f"EMBEDDING_ENDPOINT URL is missing hostname: {endpoint}")
    return urlunparse((
        parsed.scheme or "http",
        parsed.netloc,
        parsed.path.rstrip("/"),
        "",
        "",
        "",
    )).rstrip("/")


def _embedding_api_url(base_url: str) -> str:
    """Return the OpenAI-compatible embeddings URL for a normalized base URL."""
    base = base_url.rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if path.endswith("/embeddings"):
        return base
    if path.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


class EmbeddingFunction:
    """
    Embedding function using an OpenAI-compatible embedding API.

    Provides a callable interface compatible with ClusterSemanticChunker's
    embedding_function parameter, calling the /v1/embeddings endpoint.

    Usage:
        embed_fn = EmbeddingFunction(
            endpoint=settings.embedding_endpoint,
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            timeout_s=settings.embedding_timeout_s,
            api_key=settings.embedding_api_key,
        )
        embeddings = embed_fn(["text 1", "text 2", "text 3"])
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        batch_size: int,
        timeout_s: float,
        api_key: Optional[str] = None,
    ):
        self._model = model
        self._batch_size = batch_size
        self._progress_callback = None
        self._timeout = timeout_s
        self._api_key = api_key
        self._base_url = _normalize_embedding_endpoint(endpoint)
        parsed = urlparse(self._base_url)
        self._host = parsed.hostname or self._base_url
        self._embeddings_url = _embedding_api_url(self._base_url)

        logger.info(
            f"[Embedding] Initialized: model={self._model}, "
            f"endpoint={redact_url(self._embeddings_url)}, batch_size={self._batch_size}"
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def host(self) -> str:
        return self._host

    def set_progress_callback(
        self,
        callback: Optional[Callable[[int, int, str], None]]
    ) -> None:
        """Set a progress callback for heartbeat updates during embedding."""
        self._progress_callback = callback

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        if not input:
            return []

        use_batching = (
            self._progress_callback is not None and
            len(input) > self._batch_size
        )

        if use_batching:
            return self._embed_with_batching(input)

        num_batches = (len(input) + self._batch_size - 1) // self._batch_size
        logger.info(
            f"[Embedding] Processing {len(input)} texts in {num_batches} batches (batch_size={self._batch_size})"
        )

        start_time = time.perf_counter()
        try:
            all_embeddings: List[List[float]] = []

            for batch_idx in range(num_batches):
                batch_start = batch_idx * self._batch_size
                batch_end = min(batch_start + self._batch_size, len(input))
                batch = input[batch_start:batch_end]

                batch_embeddings = self._call_embedding_api(batch, batch_idx + 1, num_batches)
                all_embeddings.extend(batch_embeddings)

            elapsed = time.perf_counter() - start_time

            if len(all_embeddings) != len(input):
                logger.warning(
                    f"[Embedding] Count mismatch after {elapsed:.2f}s: "
                    f"got {len(all_embeddings)}, expected {len(input)}"
                )

            embed_dim = len(all_embeddings[0]) if all_embeddings and all_embeddings[0] else 0
            throughput = len(input) / elapsed if elapsed > 0 else 0
            logger.info(
                f"[Embedding] Success: {len(all_embeddings)} embeddings "
                f"({embed_dim} dims) in {elapsed:.2f}s ({throughput:.1f} texts/sec)"
            )
            return all_embeddings

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"[Embedding] FAILED after {elapsed:.2f}s: {e}")
            raise

    def _call_embedding_api(
        self,
        texts: List[str],
        batch_num: int,
        total_batches: int
    ) -> List[List[float]]:
        """Call the OpenAI-compatible /v1/embeddings API."""
        batch_start_time = time.perf_counter()

        with httpx.Client(timeout=self._timeout) as client:
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
            headers = request_id_headers(headers)
            response = client.post(
                self._embeddings_url,
                headers=headers,
                json={"model": self._model, "input": texts}
            )

            batch_elapsed = time.perf_counter() - batch_start_time

            if response.status_code != 200:
                logger.error(
                    f"[Embedding] Batch {batch_num} FAILED after {batch_elapsed:.2f}s: "
                    f"status={response.status_code} endpoint={redact_url(self._embeddings_url)}"
                )
                logger.debug("[Embedding] Upstream error body: %s", response.text)
                raise RuntimeError(f"embedding upstream status {response.status_code}")

            data = response.json()
            # OpenAI format: {"data": [{"embedding": [...], "index": 0}, ...]}
            embeddings = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]

            logger.debug(
                f"[Embedding] Batch {batch_num}/{total_batches}: "
                f"{len(texts)} texts in {batch_elapsed:.2f}s"
            )

            return embeddings

    def _embed_with_batching(self, input: List[str]) -> List[List[float]]:
        """Generate embeddings with batching and progress callbacks."""
        total = len(input)
        num_batches = (total + self._batch_size - 1) // self._batch_size
        all_embeddings: List[List[float]] = []

        logger.info(
            f"[Embedding] Processing {total} texts in {num_batches} batches "
            f"(batch_size={self._batch_size})"
        )

        start_time = time.perf_counter()

        for batch_idx in range(num_batches):
            batch_start = batch_idx * self._batch_size
            batch_end = min(batch_start + self._batch_size, total)
            batch = input[batch_start:batch_end]

            if self._progress_callback:
                self._progress_callback(
                    batch_start, total,
                    f"Embedding batch {batch_idx + 1}/{num_batches} ({batch_start}/{total})"
                )

            batch_embeddings = self._call_embedding_api(batch, batch_idx + 1, num_batches)

            if len(batch_embeddings) != len(batch):
                logger.warning(
                    f"[Embedding] Batch {batch_idx + 1} mismatch: "
                    f"got {len(batch_embeddings)}, expected {len(batch)}"
                )

            all_embeddings.extend(batch_embeddings)

        elapsed = time.perf_counter() - start_time

        if self._progress_callback:
            self._progress_callback(
                total, total, f"Embedding complete ({len(all_embeddings)} vectors)"
            )

        embed_dim = len(all_embeddings[0]) if all_embeddings and all_embeddings[0] else 0
        throughput = total / elapsed if elapsed > 0 else 0
        logger.info(
            f"[Embedding] Batched success: {len(all_embeddings)} embeddings "
            f"({embed_dim} dims) in {elapsed:.2f}s ({throughput:.1f} texts/sec)"
        )

        return all_embeddings

    def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        result = self([text])
        return result[0] if result else []
