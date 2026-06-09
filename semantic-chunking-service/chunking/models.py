"""Pydantic request/response models for the /chunk endpoint."""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

MAX_CHUNK_TEXT_CHARS = 200_000


class SourceType(str, Enum):
    DOCUMENT = "DOCUMENT"
    WEB_MARKDOWN = "WEB_MARKDOWN"


class ChunkParams(BaseModel):
    max_chunk_tokens: Optional[int] = None
    min_chunk_tokens: Optional[int] = None
    initial_segment_tokens: Optional[int] = None


class ChunkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHUNK_TEXT_CHARS)
    source_type: SourceType = SourceType.DOCUMENT
    strategy_version: Optional[str] = None
    params: Optional[ChunkParams] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChunkOut(BaseModel):
    text: str
    token_count: int
    position: int
    start_index: int
    end_index: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    chunks: List[ChunkOut]
    strategy_version: str
    chunk_count: int
    chunk_strategy: str
    embedding_degraded: bool = False
