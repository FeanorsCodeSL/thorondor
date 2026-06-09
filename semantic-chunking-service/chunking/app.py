"""FastAPI wrapper for the semantic chunking service."""
import logging

from fastapi import FastAPI, HTTPException

from .cluster_semantic import ClusterSemanticChunker
from .embedding_function import EmbeddingFunction
from .models import ChunkOut, ChunkRequest, ChunkResponse
from .strategies import DEFAULT_STRATEGY_VERSION, resolve_strategy
from .textprep import preclean

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chunking-service")

app = FastAPI(title="Semantic Chunking Service")

# One embedding client per process; it batches calls to the BYO endpoint.
_embedder = EmbeddingFunction()


def _length(text: str) -> int:
    # Default token proxy: word count. Swap for tiktoken / a HF tokenizer here
    # to align token budgets with a specific embedding/LLM model.
    return len(text.split())


@app.get("/healthz")
def healthz():
    return {"status": "ok", "embedding": _embedder.health_check()}


@app.post("/chunk", response_model=ChunkResponse)
def chunk(req: ChunkRequest) -> ChunkResponse:
    version = req.strategy_version or DEFAULT_STRATEGY_VERSION
    try:
        overrides = req.params.model_dump(exclude_none=True) if req.params else None
        params = resolve_strategy(version, overrides)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    text = preclean(req.text, req.source_type.value)

    chunker = ClusterSemanticChunker(
        embedding_function=_embedder,
        max_chunk_size=params.max_chunk_tokens,
        min_chunk_size=params.min_chunk_tokens,
        initial_segment_size=params.initial_segment_tokens,
        length_function=_length,
    )

    results = chunker.split_text_with_metadata(text)

    chunks = [
        ChunkOut(
            text=r.text,
            token_count=r.token_count,
            position=i,
            start_index=r.start_index,
            end_index=r.end_index,
            metadata={
                **req.metadata,
                "strategy_version": version,
                "chunk_strategy": r.chunk_strategy,
                "embedding_degraded": r.embedding_degraded,
            },
        )
        for i, r in enumerate(results)
    ]
    chunk_strategy = results[0].chunk_strategy if results else version
    embedding_degraded = any(r.embedding_degraded for r in results)
    return ChunkResponse(
        chunks=chunks,
        strategy_version=version,
        chunk_count=len(chunks),
        chunk_strategy=chunk_strategy,
        embedding_degraded=embedding_degraded,
    )
