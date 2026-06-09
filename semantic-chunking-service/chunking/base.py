"""
Base classes and protocols for text chunking.

Provides the foundational types used by all chunker implementations.
"""
from dataclasses import dataclass, field
from typing import List, Protocol, runtime_checkable


@dataclass
class ChunkResult:
    """
    Result of a chunking operation containing text and metadata.

    Attributes:
        text: The chunk text content
        start_index: Character start index in the original text
        end_index: Character end index in the original text
        token_count: Number of tokens in the chunk
        segment_indices: Indices of original segments that make up this chunk
    """

    text: str
    start_index: int
    end_index: int
    token_count: int = 0
    segment_indices: List[int] = field(default_factory=list)

    def __len__(self) -> int:
        """Return the length of the chunk text."""
        return len(self.text)

    def __repr__(self) -> str:
        """Return string representation of the chunk."""
        preview = self.text[:50] + "..." if len(self.text) > 50 else self.text
        return (
            f"ChunkResult(text='{preview}', "
            f"start={self.start_index}, end={self.end_index}, "
            f"tokens={self.token_count})"
        )


@runtime_checkable
class BaseChunker(Protocol):
    """Protocol for text chunkers."""

    def split_text(self, text: str) -> List[str]:
        """Split text into chunks."""
        ...
