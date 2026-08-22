"""FastAPI wrapper for the semantic chunking service."""
import logging

from fastapi import FastAPI, HTTPException, Request

from .settings import load_settings
from .cluster_semantic import ClusterSemanticChunker
from .embedding_function import EmbeddingFunction
from .models import ChunkOut, ChunkRequest, ChunkResponse, SourceType
from .observability import new_request_id, reset_request_id, set_request_id
from .strategies import resolve_strategy
from .textprep import preclean

settings = load_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("chunking-service")

app = FastAPI(title="Semantic Chunking Service")

# One embedding client per process; it batches calls to the BYO endpoint.
_embedder = EmbeddingFunction(
    endpoint=settings.embedding_endpoint,
    model=settings.embedding_model,
    batch_size=settings.embedding_batch_size,
    timeout_s=settings.embedding_timeout_s,
    api_key=settings.embedding_api_key,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = set_request_id(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)


def _length(text: str) -> int:
    # Default token proxy: word count. Swap for tiktoken / a HF tokenizer here
    # to align token budgets with a specific embedding/LLM model.
    return len(text.split())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chunk", responses={400: {"description": "Unknown or invalid chunking strategy"}})
def chunk(req: ChunkRequest) -> ChunkResponse:
    version = req.strategy_version or settings.default_strategy_version
    try:
        overrides = req.params.model_dump(exclude_none=True) if req.params else None
        params = resolve_strategy(version, overrides)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    preserve_offsets = req.source_type == SourceType.ORCHESTRATOR_MARKDOWN
    text = req.text if preserve_offsets else preclean(req.text, req.source_type.value)

    chunker = ClusterSemanticChunker(
        embedding_function=_embedder,
        max_chunk_size=params.max_chunk_tokens,
        min_chunk_size=params.min_chunk_tokens,
        initial_segment_size=params.initial_segment_tokens,
        length_function=_length,
    )

    results = chunker.split_text_with_metadata(text, preserve_offsets=preserve_offsets)

    chunks = [
        ChunkOut(
            text=r.text,
            token_count=r.token_count,
            position=i,
            start_index=r.start_index,
            end_index=r.end_index,
            verbatim=r.verbatim,
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
