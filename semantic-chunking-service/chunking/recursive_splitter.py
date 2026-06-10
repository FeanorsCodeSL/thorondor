"""
Recursive Character Text Splitter for initial segment creation.

This splitter recursively splits text using a hierarchy of separators,
ensuring that segments respect natural text boundaries like paragraphs,
sentences, and words.

The separator hierarchy is LangChain-inspired, but this is a first-party
implementation and does not vendor LangChain source.
"""
import logging
import re
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RecursiveCharacterTextSplitter:
    """
    Recursively splits text using a hierarchy of separators.

    The splitter tries separators in order, falling back to the next
    separator when chunks are still too large. This ensures that text
    is split at natural boundaries when possible.
    """

    DEFAULT_SEPARATORS = [
        "\n\n",     # Paragraph break
        "\n",       # Line break
        ". ",       # Sentence ending
        "! ",       # Exclamation
        "? ",       # Question
        "; ",       # Semicolon
        ", ",       # Comma
        " ",        # Word boundary
        ""          # Character level (last resort)
    ]

    def __init__(
        self,
        chunk_size: int = 50,
        chunk_overlap: int = 0,
        separators: Optional[List[str]] = None,
        length_function: Optional[Callable[[str], int]] = None
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or self.DEFAULT_SEPARATORS.copy()

        # Default length function: simple word count
        if length_function is None:
            self.length_function = lambda text: len(text.split())
        else:
            self.length_function = length_function

    @property
    def _keep_separator(self) -> bool:
        """Whether to keep separators in the output."""
        return True

    def split_text(self, text: str) -> List[str]:
        """Split text into chunks using recursive character splitting."""
        if not text or not text.strip():
            return []
        return self._split_text_recursive(text, self.separators)

    def _split_text_recursive(
        self,
        text: str,
        separators: List[str]
    ) -> List[str]:
        """Recursively split text using the separator hierarchy."""
        final_chunks: List[str] = []
        separator, new_separators = self._find_best_separator(text, separators)

        splits = self._split_with_separator(text, separator) if separator else list(text)

        good_splits: List[str] = []

        for split in splits:
            if not split:
                continue

            if self.length_function(split) <= self.chunk_size:
                good_splits.append(split)
            else:
                if good_splits:
                    final_chunks.extend(self._merge_splits(good_splits, separator))
                    good_splits = []

                if new_separators:
                    final_chunks.extend(self._split_text_recursive(split, new_separators))
                else:
                    final_chunks.extend(self._force_split_by_size(split))

        if good_splits:
            final_chunks.extend(self._merge_splits(good_splits, separator))

        return final_chunks

    def _find_best_separator(
        self,
        text: str,
        separators: List[str]
    ) -> Tuple[str, List[str]]:
        """Select the most specific separator that appears in text."""
        default = separators[-1] if separators else ""
        for i, sep in enumerate(separators):
            if sep == "":
                return sep, []
            if sep in text:
                return sep, separators[i + 1:]
        return default, []

    def _split_with_separator(
        self,
        text: str,
        separator: str
    ) -> List[str]:
        """Split text using a separator, optionally keeping the separator."""
        if not separator:
            return [text]

        if self._keep_separator:
            # Keep separator at end of each piece
            pattern = f"({re.escape(separator)})"
            parts = re.split(pattern, text)

            result = []
            for i in range(0, len(parts), 2):
                piece = parts[i]
                if i + 1 < len(parts):
                    piece += parts[i + 1]
                if piece:
                    result.append(piece)
            return result
        else:
            return text.split(separator)

    def _merge_splits(
        self,
        splits: List[str],
        _separator: str
    ) -> List[str]:
        """Merge small splits into chunks up to chunk_size."""
        if not splits:
            return []

        merged_chunks: List[str] = []
        current_chunk: List[str] = []
        current_length = 0

        for split in splits:
            split_length = self.length_function(split)

            if current_length + split_length > self.chunk_size and current_chunk:
                merged_text = "".join(current_chunk)
                merged_chunks.append(merged_text)

                if self.chunk_overlap > 0:
                    overlap_chunks = self._get_overlap(current_chunk)
                    current_chunk = overlap_chunks
                    current_length = sum(
                        self.length_function(c) for c in current_chunk
                    )
                else:
                    current_chunk = []
                    current_length = 0

            current_chunk.append(split)
            current_length += split_length

        if current_chunk:
            merged_text = "".join(current_chunk)
            merged_chunks.append(merged_text)

        return merged_chunks

    def _get_overlap(self, chunks: List[str]) -> List[str]:
        """Get the overlap portion from the end of chunks."""
        if not chunks or self.chunk_overlap <= 0:
            return []

        overlap_pieces: List[str] = []
        overlap_length = 0

        for chunk in reversed(chunks):
            chunk_length = self.length_function(chunk)
            if overlap_length + chunk_length <= self.chunk_overlap:
                overlap_pieces.insert(0, chunk)
                overlap_length += chunk_length
            else:
                break

        return overlap_pieces

    def _force_split_by_size(self, text: str) -> List[str]:
        """Force split text by character count when no separators work."""
        chunks: List[str] = []

        chars_per_token = max(1, len(text) // max(1, self.length_function(text)))
        target_chars = self.chunk_size * chars_per_token

        start = 0
        while start < len(text):
            end = min(start + target_chars, len(text))

            if end < len(text):
                space_idx = text.rfind(' ', start, end)
                if space_idx > start:
                    end = space_idx + 1

            chunks.append(text[start:end])
            start = end

        return chunks
