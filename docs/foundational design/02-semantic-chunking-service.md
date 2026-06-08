# 02 — Semantic Chunking Service

> **Thorondor** — Semantic Web Search Pipeline.

This is the heart of the project: a standalone microservice that turns raw text
or markdown into **globally-coherent passages**. It is reusable on its own — any
system that needs semantic chunks (a RAG ingester, a summarizer, another
agent's tool) can call it over HTTP without adopting the rest of the pipeline.

It implements the **ClusterSemanticChunker** algorithm (Chroma Research, July
2024): instead of cutting text into fixed-size windows, it finds the chunk
boundaries that **maximize semantic coherence within chunks** via dynamic
programming, subject to a token-size constraint.

The code below is the extracted, de-coupled version of a chunker that already
runs in production. The algorithm, the fallbacks, the memory discipline, and
the parameters are **preserved logic-for-logic**; only the host-application
couplings (a custom config module, a custom env helper, and a document-parser
dependency) were removed so the unit stands alone.

---

## 1. What it does (algorithm)

Given a string and a size budget, `split_text_with_metadata(text)` runs:

1. **Segment.** Split the text into small segments (~50 tokens) at natural
   boundaries (paragraph → line → sentence → clause → word → char) using a
   recursive separator hierarchy. These segments are the atoms the optimizer
   groups; they are never themselves split further.
2. **Embed.** Embed every segment via the configured embedding endpoint
   (batched, OpenAI `/v1/embeddings`).
3. **Similarity matrix.** Build the `N×N` cosine-similarity matrix between all
   segment embeddings. For unit-normalized vectors cosine similarity is just the
   dot product, so the whole matrix is one `emb @ emb.T`.
4. **Optimize (dynamic programming).** Find the segmentation into contiguous
   groups that **maximizes total semantic reward**, where a group's reward is
   the sum of pairwise similarities of the segments inside it, subject to: every
   group's token count ≤ `max_chunk_size`. `dp[i]` = best total reward for
   segments `[0, i)`; `parent[i]` = where the last chunk ending at `i` began.
   Backtracking `parent` yields the optimal boundaries.
5. **Build results.** Merge each group's segments back into chunk text, carry
   character offsets and token counts, and return `ChunkResult`s.

**Why globally optimal beats greedy.** A sliding-window-with-threshold splitter
makes a local decision at each boundary and can miss a grouping that is better
overall. The DP considers all admissible groupings and returns the true
optimum for the reward function. Chroma's published benchmarks: the
ClusterSemanticChunker at a 400-token budget reaches ~91% recall vs ~88% for a
plain recursive splitter, at comparable precision.

### Cost and the safety valves

The exact algorithm is `O(N²)` memory (the similarity matrix) and the DP inner
loop is `O(N·k)` where `k ≈ max_chunk_size / avg_segment_size`. Three fallbacks
keep it safe on adversarial inputs:

- **`MAX_SEGMENTS_FOR_DP` guard (OOM prevention).** Before building the matrix,
  if the segment count exceeds the configured cap, switch to a **greedy
  semantic** path: embed segments, compute only *adjacent-pair* similarities
  (`O(N)` memory), and break chunks where adjacent similarity drops below the
  25th-percentile threshold. Quality is slightly lower than full DP but still
  semantic, and memory is linear.
- **Embedding-failure fallback.** If the embedding call fails or returns the
  wrong count, fall back to **greedy token-based** chunking (group by token
  count, no semantics). The service still returns usable chunks.
- **DP-no-solution fallback.** If no admissible segmentation exists under the
  constraints, fall back to greedy token grouping.

Memory is released aggressively: the similarity matrix and embeddings (which can
be gigabytes for large inputs) are `del`-eted and `gc.collect()`-ed the instant
the DP finishes, before results are built. The DP reward cache is a **bounded
LRU** so it cannot grow without limit on huge inputs.

---

## 2. Service contract

A single logical operation, exposed as `POST /chunk`.

### Request

```jsonc
{
  "text": "raw text or markdown to chunk",
  "source_type": "WEB_MARKDOWN",      // or "DOCUMENT" (default). Controls pre-cleaning.
  "strategy_version": "cluster-semantic@1",  // optional; pins reproducible behavior
  "params": {                          // optional overrides; defaults per strategy_version
    "max_chunk_tokens": 400,
    "min_chunk_tokens": 50,
    "initial_segment_tokens": 50
  },
  "metadata": {                        // optional, passed through opaquely onto every chunk
    "source_url": "https://example.com/article",
    "title": "Example article"
  }
}
```

`metadata` is **passthrough provenance**. The service never interprets it — it
copies it onto every emitted chunk. This is what lets one contract serve both a
crawled web page (`source_url`, `title`) and a local document (`document_id`,
`file_name`) without the chunker knowing the difference.

### Response

```jsonc
{
  "chunks": [
    {
      "text": "merged passage text",
      "token_count": 372,
      "position": 0,            // 0-based order within this document
      "start_index": 0,         // char offset in the (pre-cleaned) input
      "end_index": 1840,
      "metadata": { "source_url": "...", "title": "...", "strategy_version": "cluster-semantic@1" }
    }
  ],
  "strategy_version": "cluster-semantic@1",
  "chunk_count": 1
}
```

### Strategy versioning

Every chunk and the response carry `strategy_version`. Pinning a version makes
chunking **reproducible**: a consumer that stored chunks produced by
`cluster-semantic@1` can detect when the strategy has moved on and decide to
re-chunk deliberately, instead of silently mixing incompatible chunkings. In
the web-search pipeline the content is ephemeral and always re-chunked at the
current version, so this matters most for any *persistent* consumer.

### Embedding dependency (statelessness discipline)

The chunker **does not load an embedding model**. It calls the configured
`EMBEDDING_ENDPOINT` (`/v1/embeddings`). This keeps the service CPU-bound, light,
stateless, and horizontally scalable, and it means the chunker and any other
consumer share one embedding model — so their chunk/embedding spaces stay
comparable. The chunker is, in effect, a CPU orchestration service that batches
embedding calls and runs numpy + DP.

---

## 3. Source layout

```
semantic-chunking-service/
├── chunking/
│   ├── __init__.py
│   ├── base.py                # ChunkResult dataclass + BaseChunker protocol
│   ├── recursive_splitter.py  # RecursiveCharacterTextSplitter (the segmenter)
│   ├── cluster_semantic.py    # ClusterSemanticChunker (the DP algorithm)
│   ├── embedding_function.py  # OpenAI-compatible /v1/embeddings client
│   ├── settings.py            # env-driven config (replaces host-app config)
│   ├── strategies.py          # strategy registry + version pinning
│   ├── textprep.py            # source_type pre-cleaning hook
│   ├── models.py              # Pydantic request/response models
│   └── app.py                 # FastAPI wrapper (/chunk, /healthz)
├── tests/
│   └── test_parity.py         # golden-file parity against the reference impl
├── requirements.txt
└── Dockerfile
```

The four files `base.py`, `recursive_splitter.py`, `cluster_semantic.py`, and
`embedding_function.py` are the extracted logic. The rest (`settings`,
`strategies`, `textprep`, `models`, `app`) is the thin service shell around it.

---

## 4. Extracted core — `chunking/base.py`

Foundational types. Reproduced verbatim — no host coupling.

```python
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
```

---

## 5. Extracted core — `chunking/recursive_splitter.py`

The segmenter. It produces the small (~50-token) atoms the optimizer groups,
respecting natural boundaries. Reproduced verbatim — no host coupling.

```python
"""
Recursive Character Text Splitter for initial segment creation.

This splitter recursively splits text using a hierarchy of separators,
ensuring that segments respect natural text boundaries like paragraphs,
sentences, and words.
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
        separator: str
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
```

---

## 6. Extracted core — `chunking/cluster_semantic.py`

The DP algorithm. **Logic preserved exactly.** The only changes from the
reference implementation:

- `MAX_SEGMENTS_FOR_DP` and the reward-cache size now come from `settings.py`
  (env-driven) instead of a host-application config module.
- Internal issue-tracker tags in comments were removed; the explanatory text is
  kept.

```python
"""
Cluster Semantic Chunker - Globally Optimal Semantic Chunking.

This module implements the ClusterSemanticChunker algorithm from Chroma Research
(July 2024) which uses dynamic programming to find globally optimal chunk
boundaries that maximize semantic coherence within chunks.

Algorithm Overview:
1. Split text into small segments (~50 tokens) using RecursiveCharacterTextSplitter
2. Embed each segment using the embedding model
3. Build NxN cosine similarity matrix between all segment pairs
4. Use dynamic programming to maximize "semantic reward":
   - Reward = sum of pairwise similarities within each chunk
   - Constraint: each chunk must not exceed max_chunk_size tokens
5. Return optimally grouped chunks

Performance Benchmarks (from Chroma Research):
- ClusterSemanticChunker (400 tokens): 91.3% Recall, 4.5% Precision
- ClusterSemanticChunker (200 tokens): 87.3% Recall, 8.0% Precision
- RecursiveCharacterTextSplitter: 88-89% Recall, 3.6-7.0% Precision

Recommendation: Use 400 tokens for best recall, 200 for best precision.

OOM Prevention:
- MAX_SEGMENTS_FOR_DP limits segments before falling back to greedy algorithm
- O(N^2) similarity matrix at 2000 segments = ~16MB (safe)
- O(N^2) at 18000 segments = ~1.3GB (OOM risk)
- DP inner loop is O(N*k) after reverse+break optimization (k ~ max_chunk_size / avg_segment_size)
- Greedy fallback is O(N) memory and O(N^2) worst-case time
"""
import gc
import logging
import time
from functools import lru_cache
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

from .base import ChunkResult
from .recursive_splitter import RecursiveCharacterTextSplitter
from .settings import CHUNKER_MAX_SEGMENTS_DP, REWARD_CACHE_MAX_SIZE

logger = logging.getLogger(__name__)

# OOM Prevention: Maximum segments before falling back to greedy algorithm
# Memory for similarity matrix: N^2 * 4 bytes (float32)
# - 2000 segments = 16 MB (safe)
# - 5000 segments = 100 MB (acceptable)
# - 10000 segments = 400 MB (safe within a typical container memory budget)
# - 18000 segments = 1.3 GB (OOM)
MAX_SEGMENTS_FOR_DP = CHUNKER_MAX_SEGMENTS_DP


class ClusterSemanticChunker:
    """
    Semantic chunker using dynamic programming for globally optimal boundaries.

    This chunker finds the optimal way to group text segments that maximizes
    semantic coherence (measured by cosine similarity) while respecting
    size constraints.

    The key insight is that locally greedy approaches (like sliding window
    with threshold) can miss globally optimal solutions. Dynamic programming
    considers all possible groupings to find the true optimum.
    """

    def __init__(
        self,
        embedding_function: Callable[[List[str]], List[List[float]]],
        max_chunk_size: int = 400,
        min_chunk_size: int = 50,
        initial_segment_size: int = 50,
        length_function: Optional[Callable[[str], int]] = None
    ):
        """
        Initialize the ClusterSemanticChunker.

        Args:
            embedding_function: Function that takes a list of strings and
                              returns a list of embedding vectors.
                              Signature: (List[str]) -> List[List[float]]
            max_chunk_size: Maximum number of tokens per final chunk.
                          Chroma recommends 400 for recall, 200 for precision.
            min_chunk_size: Minimum number of tokens per chunk.
            initial_segment_size: Token size for initial text splitting.
            length_function: Function to count tokens in text. Defaults to a
                           simple word count (split by spaces).
        """
        self._embedding_function = embedding_function
        self._max_chunk_size = max_chunk_size
        self._min_chunk_size = min_chunk_size
        self._initial_segment_size = initial_segment_size

        if length_function is None:
            self._length_function = lambda text: len(text.split())
        else:
            self._length_function = length_function

    @property
    def max_chunk_size(self) -> int:
        return self._max_chunk_size

    @property
    def min_chunk_size(self) -> int:
        return self._min_chunk_size

    def split_text(self, text: str) -> List[str]:
        """Split text into semantically coherent chunks (text only)."""
        results = self.split_text_with_metadata(text)
        return [r.text for r in results]

    def split_text_with_metadata(
        self,
        text: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> List[ChunkResult]:
        """
        Split text into chunks with full metadata.

        Args:
            text: The text to split
            progress_callback: Optional callback for progress updates during long
                             operations. Signature: (current_step, total_steps, message)

        Returns:
            List of ChunkResult with text, indices, and token counts
        """
        if not text or not text.strip():
            return []

        # Step 1: Split into initial segments
        segment_start_time = time.perf_counter()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._initial_segment_size,
            chunk_overlap=0,
            length_function=self._length_function
        )
        segments = splitter.split_text(text)

        segment_time = time.perf_counter() - segment_start_time

        if not segments:
            return []

        # Handle single segment case
        if len(segments) == 1:
            logger.info(
                f"[ClusterSemantic] Single segment document ({len(text):,} chars), "
                f"returning as single chunk"
            )
            return [ChunkResult(
                text=segments[0],
                start_index=0,
                end_index=len(segments[0]),
                token_count=self._length_function(segments[0]),
                segment_indices=[0]
            )]

        logger.info(
            f"[ClusterSemantic] Step 1/5 - Segmentation: "
            f"{len(text):,} chars -> {len(segments)} segments "
            f"(~{self._initial_segment_size} tokens each) in {segment_time:.2f}s"
        )

        if progress_callback:
            progress_callback(1, 5, f"Segmented into {len(segments)} segments")

        # OOM Prevention: Check segment count BEFORE expensive operations.
        # O(N^2) similarity matrix is infeasible for very large N.
        if len(segments) > MAX_SEGMENTS_FOR_DP:
            estimated_memory_gb = (len(segments) ** 2 * 4) / (1024 ** 3)
            logger.warning(
                f"[ClusterSemantic] OOM PREVENTION: {len(segments)} segments exceeds "
                f"MAX_SEGMENTS_FOR_DP={MAX_SEGMENTS_FOR_DP}. "
                f"Similarity matrix would require ~{estimated_memory_gb:.2f} GB. "
                f"Using greedy semantic fallback (O(N) memory) instead of DP (O(N^2) memory)."
            )
            segment_positions = self._calculate_segment_positions(text, segments)
            segment_lengths = [self._length_function(s) for s in segments]
            return self._greedy_semantic_chunking(
                segments, segment_positions, segment_lengths, progress_callback
            )

        # Step 2: Calculate segment positions in original text
        segment_positions = self._calculate_segment_positions(text, segments)

        # Step 3: Calculate token count for each segment
        segment_lengths = [self._length_function(s) for s in segments]
        total_tokens = sum(segment_lengths)
        avg_tokens = total_tokens / len(segment_lengths) if segment_lengths else 0
        logger.info(
            f"[ClusterSemantic] Step 2/5 - Token analysis: "
            f"{total_tokens:,} total tokens, avg {avg_tokens:.1f} tokens/segment, "
            f"range [{min(segment_lengths)}-{max(segment_lengths)}]"
        )

        if progress_callback:
            progress_callback(2, 5, f"Generating embeddings for {len(segments)} segments...")

        # Step 4: Generate embeddings for all segments
        logger.info(
            f"[ClusterSemantic] Step 3/5 - Generating embeddings for {len(segments)} segments..."
        )
        embeddings, embed_time = self._generate_embeddings_for_segments(segments, logger)
        if embeddings is None:
            return self._fallback_chunking(segments, segment_positions, segment_lengths)

        embed_dim = len(embeddings[0]) if embeddings[0] else 0
        logger.info(
            f"[ClusterSemantic] Step 3/5 - Embeddings complete: "
            f"{len(embeddings)} vectors x {embed_dim} dims in {embed_time:.2f}s "
            f"({len(segments)/embed_time:.1f} segments/sec)"
        )

        if progress_callback:
            progress_callback(3, 5, f"Embeddings complete ({len(embeddings)} vectors)")

        # Step 5: Compute similarity matrix
        estimated_memory_mb = (len(segments) ** 2 * 4) / (1024 ** 2)
        logger.info(
            f"[ClusterSemantic] Step 4/5 - Computing {len(segments)}x{len(segments)} similarity matrix "
            f"(~{estimated_memory_mb:.1f} MB)..."
        )
        sim_start_time = time.perf_counter()
        similarity_matrix = self._compute_similarity_matrix(embeddings)
        sim_time = time.perf_counter() - sim_start_time

        n = len(segments)
        avg_similarity = (similarity_matrix.sum() - n) / (n * (n - 1)) if n > 1 else 0
        logger.info(
            f"[ClusterSemantic] Step 4/5 - Similarity matrix complete: "
            f"avg similarity={avg_similarity:.3f}, computed in {sim_time:.2f}s"
        )

        if progress_callback:
            progress_callback(4, 5, f"Similarity matrix computed ({n}x{n})")

        # Step 6: Run dynamic programming to find optimal groupings
        logger.info(
            f"[ClusterSemantic] Step 5/5 - Running DP optimization "
            f"(max_chunk={self._max_chunk_size}, min_chunk={self._min_chunk_size})..."
        )
        dp_start_time = time.perf_counter()
        groupings = self._dynamic_programming_chunking(
            segments,
            similarity_matrix,
            segment_lengths
        )
        dp_time = time.perf_counter() - dp_start_time
        logger.info(
            f"[ClusterSemantic] Step 5/5 - DP optimization complete: "
            f"found {len(groupings)} optimal chunk boundaries in {dp_time:.2f}s"
        )

        # MEMORY OPTIMIZATION: release large arrays immediately after DP.
        # The similarity_matrix (O(n^2)) and embeddings are no longer needed
        # after DP optimization. Explicit deletion + gc.collect() releases this
        # memory before building results, reducing peak usage substantially.
        del similarity_matrix
        del embeddings
        gc.collect()
        logger.debug("[ClusterSemantic] Released similarity matrix and embeddings after DP")

        if progress_callback:
            progress_callback(5, 5, f"Optimization complete ({len(groupings)} chunks)")

        # Step 7: Build chunk results from groupings
        results = self._build_chunk_results(
            segments,
            groupings,
            segment_positions,
            segment_lengths
        )

        total_time = segment_time + embed_time + sim_time + dp_time
        logger.info(
            f"[ClusterSemantic] COMPLETE: {len(segments)} segments -> {len(results)} chunks "
            f"(total: {total_time:.2f}s)"
        )

        if results:
            chunk_tokens = [r.token_count for r in results]
            logger.info(
                f"[ClusterSemantic] Chunk stats: "
                f"tokens [{min(chunk_tokens)}-{max(chunk_tokens)}], "
                f"avg={sum(chunk_tokens)/len(chunk_tokens):.0f}, "
                f"segments/chunk avg={len(segments)/len(results):.1f}"
            )

        return results

    def _generate_embeddings_for_segments(self, segments: List[str], log: Any):
        """
        Call embedding function and return (embeddings, elapsed_seconds).

        Returns (None, elapsed) on error or count mismatch.
        """
        start = time.perf_counter()
        try:
            embeddings = self._embedding_function(segments)
            elapsed = time.perf_counter() - start
            if not embeddings or len(embeddings) != len(segments):
                log.error(
                    f"[ClusterSemantic] EMBEDDING MISMATCH: "
                    f"Got {len(embeddings) if embeddings else 0}, expected {len(segments)}. "
                    f"Falling back to non-semantic chunking."
                )
                return None, elapsed
            return embeddings, elapsed
        except Exception as e:
            elapsed = time.perf_counter() - start
            log.error(
                f"[ClusterSemantic] EMBEDDING FAILED after {elapsed:.2f}s: {e}. "
                f"Falling back to non-semantic chunking."
            )
            return None, elapsed

    def _calculate_segment_positions(
        self,
        text: str,
        segments: List[str]
    ) -> List[Tuple[int, int]]:
        """Calculate start and end positions of segments in original text."""
        positions = []
        current_pos = 0

        for segment in segments:
            segment_stripped = segment.strip()
            if not segment_stripped:
                positions.append((current_pos, current_pos))
                continue

            idx = text.find(segment_stripped, current_pos)
            if idx == -1:
                start = current_pos
                end = min(current_pos + len(segment), len(text))
            else:
                start = idx
                end = idx + len(segment_stripped)

            positions.append((start, end))
            current_pos = end

        return positions

    def _compute_similarity_matrix(
        self,
        embeddings: List[List[float]]
    ) -> np.ndarray:
        """
        Compute NxN cosine similarity matrix between all embedding pairs.

        MEMORY OPTIMIZATION:
        - Uses in-place normalization to avoid creating a second copy
        - Deletes intermediate arrays immediately after use
        - For unit vectors, cos_sim(a, b) = dot(a, b)
        """
        emb_matrix = np.array(embeddings, dtype=np.float32)

        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        emb_matrix /= norms
        del norms

        similarity_matrix = np.dot(emb_matrix, emb_matrix.T)
        del emb_matrix

        return similarity_matrix

    def _calculate_chunk_reward(
        self,
        similarity_matrix: np.ndarray,
        start: int,
        end: int
    ) -> float:
        """
        Calculate the semantic reward for grouping segments [start, end).

        The reward is the sum of all pairwise cosine similarities within
        the chunk. Higher reward = more semantically coherent grouping.
        This is the objective function maximized via dynamic programming.
        """
        if end <= start + 1:
            return 0.0

        sub_matrix = similarity_matrix[start:end, start:end]
        total = np.sum(sub_matrix) - np.trace(sub_matrix)
        return total / 2.0

    def _dynamic_programming_chunking(
        self,
        segments: List[str],
        similarity_matrix: np.ndarray,
        segment_lengths: List[int]
    ) -> List[Tuple[int, int]]:
        """
        Find optimal chunk boundaries using dynamic programming.

        DP State:
        - dp[i] = (max_reward, last_break_point) for segments [0, i)
        - We try all possible last chunk boundaries

        Time Complexity: O(N*k) where k ~ max_chunk_size / avg_segment_size
        Space Complexity: O(N^2) for the similarity matrix, O(N) for DP
        """
        n = len(segments)

        if n == 0:
            return []

        if n == 1:
            return [(0, 1)]

        cumsum = [0]
        for length in segment_lengths:
            cumsum.append(cumsum[-1] + length)

        def get_token_count(start: int, end: int) -> int:
            """Get total tokens in segments [start, end)."""
            return cumsum[end] - cumsum[start]

        # Bounded LRU cache for (start, end) -> reward. For large documents this
        # could grow to O(n^2) entries; LRU eviction keeps memory bounded.
        @lru_cache(maxsize=REWARD_CACHE_MAX_SIZE)
        def get_reward(start: int, end: int) -> float:
            """Get cached reward for segment range (LRU-bounded)."""
            return self._calculate_chunk_reward(similarity_matrix, start, end)

        dp = [-float('inf')] * (n + 1)
        parent = [-1] * (n + 1)
        dp[0] = 0.0

        self._fill_dp_table(dp, parent, n, get_token_count, get_reward)

        if dp[n] == -float('inf'):
            logger.warning(
                f"[ClusterSemantic] DP FALLBACK: No valid chunking found with constraints "
                f"(max={self._max_chunk_size}, min={self._min_chunk_size}). "
                f"Using greedy grouping (no semantic optimization)."
            )
            return self._greedy_fallback_chunking(segment_lengths)

        groupings = []
        current = n
        while current > 0:
            prev = parent[current]
            if prev == -1:
                prev = 0
            groupings.append((prev, current))
            current = prev

        groupings.reverse()

        logger.debug(
            f"DP found {len(groupings)} chunks with total reward {dp[n]:.4f}"
        )

        cache_info = get_reward.cache_info()
        get_reward.cache_clear()
        logger.debug(
            f"Cleared reward_cache LRU ({cache_info.currsize} entries, "
            f"hits={cache_info.hits}, misses={cache_info.misses})"
        )

        return groupings

    def _fill_dp_table(
        self,
        dp: List[float],
        parent: List[int],
        n: int,
        get_token_count: Any,
        get_reward: Any,
    ) -> None:
        """
        Fill the DP and parent arrays in-place.

        For each position i, try all valid last-chunk start positions j (reversed
        so we can break early once the chunk exceeds max_chunk_size).
        """
        for i in range(1, n + 1):
            for j in range(i - 1, -1, -1):
                should_break = self._evaluate_chunk_candidate(
                    dp, parent, i, j, n, get_token_count, get_reward
                )
                if should_break:
                    break

    def _evaluate_chunk_candidate(
        self,
        dp: List[float],
        parent: List[int],
        i: int,
        j: int,
        n: int,
        get_token_count: Any,
        get_reward: Any,
    ) -> bool:
        chunk_tokens = get_token_count(j, i)

        if chunk_tokens > self._max_chunk_size:
            return True

        if chunk_tokens < self._min_chunk_size and i < n:
            if dp[j] != -float('inf'):
                return False

        chunk_reward = get_reward(j, i)
        total_reward = dp[j] + chunk_reward
        if total_reward > dp[i]:
            dp[i] = total_reward
            parent[i] = j
        return False

    def _greedy_fallback_chunking(
        self,
        segment_lengths: List[int]
    ) -> List[Tuple[int, int]]:
        """Greedy fallback when DP finds no valid solution."""
        groupings = []
        current_start = 0
        current_tokens = 0

        for i, length in enumerate(segment_lengths):
            if current_tokens + length > self._max_chunk_size and current_start < i:
                groupings.append((current_start, i))
                current_start = i
                current_tokens = length
            else:
                current_tokens += length

        if current_start < len(segment_lengths):
            groupings.append((current_start, len(segment_lengths)))

        logger.info(
            f"[ClusterSemantic] Greedy fallback created {len(groupings)} chunks "
            f"from {len(segment_lengths)} segments"
        )

        return groupings

    def _build_chunk_results(
        self,
        segments: List[str],
        groupings: List[Tuple[int, int]],
        segment_positions: List[Tuple[int, int]],
        segment_lengths: List[int]
    ) -> List[ChunkResult]:
        """Build ChunkResult objects from segment groupings."""
        results = []

        for start, end in groupings:
            group_segments = segments[start:end]
            merged_text = " ".join(s.strip() for s in group_segments if s.strip())

            if not merged_text:
                continue

            char_start = segment_positions[start][0] if segment_positions else 0
            char_end = segment_positions[end - 1][1] if segment_positions else len(merged_text)

            total_tokens = sum(segment_lengths[start:end])

            results.append(ChunkResult(
                text=merged_text,
                start_index=char_start,
                end_index=char_end,
                token_count=total_tokens,
                segment_indices=list(range(start, end))
            ))

        return results

    def _greedy_merge_by_similarity(
        self,
        n: int,
        segment_lengths: List[int],
        adjacent_sims: Any,
        similarity_threshold: float,
    ) -> List[Tuple[int, int]]:
        groupings = []
        current_start = 0
        current_tokens = segment_lengths[0]

        for i in range(1, n):
            tokens_if_merged = current_tokens + segment_lengths[i]
            is_low_similarity = adjacent_sims[i - 1] < similarity_threshold
            exceeds_max = tokens_if_merged > self._max_chunk_size

            should_break = exceeds_max or (
                is_low_similarity and current_tokens >= self._min_chunk_size
            )

            if should_break and current_start < i:
                groupings.append((current_start, i))
                current_start = i
                current_tokens = segment_lengths[i]
            else:
                current_tokens = tokens_if_merged

        if current_start < n:
            groupings.append((current_start, n))

        return groupings

    def _fallback_chunking(
        self,
        segments: List[str],
        segment_positions: List[Tuple[int, int]],
        segment_lengths: List[int]
    ) -> List[ChunkResult]:
        """
        Simple fallback chunking when embedding fails.

        Groups segments greedily by token count without semantic analysis.
        """
        logger.warning(
            f"[ClusterSemantic] FALLBACK MODE: Using greedy token-based chunking "
            f"(embedding vectors NOT used). {len(segments)} segments will be grouped by token count only."
        )
        groupings = self._greedy_fallback_chunking(segment_lengths)
        results = self._build_chunk_results(
            segments, groupings, segment_positions, segment_lengths
        )
        logger.info(
            f"[ClusterSemantic] FALLBACK COMPLETE: Created {len(results)} chunks without semantic analysis"
        )
        return results

    def _greedy_semantic_chunking(
        self,
        segments: List[str],
        segment_positions: List[Tuple[int, int]],
        segment_lengths: List[int],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> List[ChunkResult]:
        """
        Greedy semantic chunking for large documents (OOM prevention).

        Instead of computing full NxN similarity matrix, this method:
        1. Embeds segments
        2. Computes only adjacent-pair similarities
        3. Merges segments greedily based on similarity threshold
        4. Uses O(N) memory instead of O(N^2)
        """
        n = len(segments)
        logger.info(
            f"[ClusterSemantic] GREEDY SEMANTIC MODE: Processing {n} segments "
            f"with O(N) memory algorithm"
        )

        if n == 0:
            return []

        if n == 1:
            return [ChunkResult(
                text=segments[0],
                start_index=segment_positions[0][0] if segment_positions else 0,
                end_index=segment_positions[0][1] if segment_positions else len(segments[0]),
                token_count=segment_lengths[0],
                segment_indices=[0]
            )]

        if progress_callback:
            progress_callback(2, 5, f"Generating embeddings for {n} segments...")

        embeddings, embed_time = self._generate_embeddings_for_segments(segments, logger)
        if embeddings is None:
            return self._fallback_chunking(segments, segment_positions, segment_lengths)

        logger.info(
            f"[ClusterSemantic] Greedy mode embeddings complete: "
            f"{n} vectors in {embed_time:.2f}s"
        )
        if progress_callback:
            progress_callback(3, 5, f"Embeddings complete ({n} vectors)")

        if progress_callback:
            progress_callback(4, 5, "Computing adjacent similarities...")

        emb_matrix = np.array(embeddings, dtype=np.float32)
        del embeddings

        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        emb_matrix /= norms
        del norms

        # Adjacent cosine similarities: sim[i] = similarity between segment i and i+1
        adjacent_sims = np.sum(emb_matrix[:-1] * emb_matrix[1:], axis=1)

        del emb_matrix
        gc.collect()
        logger.debug(f"Adjacent similarities computed: min={adjacent_sims.min():.3f}, "
                    f"max={adjacent_sims.max():.3f}, mean={adjacent_sims.mean():.3f}")

        similarity_threshold = np.percentile(adjacent_sims, 25)
        logger.debug(f"Similarity threshold (25th percentile): {similarity_threshold:.3f}")

        groupings = self._greedy_merge_by_similarity(
            n, segment_lengths, adjacent_sims, similarity_threshold
        )

        if progress_callback:
            progress_callback(5, 5, f"Optimization complete ({len(groupings)} chunks)")

        results = self._build_chunk_results(
            segments, groupings, segment_positions, segment_lengths
        )

        logger.info(
            f"[ClusterSemantic] GREEDY SEMANTIC COMPLETE: {n} segments -> {len(results)} chunks "
            f"(memory-safe algorithm)"
        )

        if results:
            chunk_tokens = [r.token_count for r in results]
            logger.info(
                f"[ClusterSemantic] Greedy chunk stats: "
                f"tokens [{min(chunk_tokens)}-{max(chunk_tokens)}], "
                f"avg={sum(chunk_tokens)/len(chunk_tokens):.0f}"
            )

        return results
```

---

## 7. Extracted core — `chunking/embedding_function.py`

OpenAI-compatible `/v1/embeddings` client. **Logic preserved exactly.** The only
change from the reference implementation: the two `get_required_env(...)` calls
now use a local `require_env` from `settings.py` instead of a host-application
helper, and the docstring no longer names a specific server.

```python
"""
Embedding Function for ClusterSemanticChunker.

This module provides an embedding function compatible with ClusterSemanticChunker
that uses the OpenAI-compatible embedding API (/v1/embeddings) to generate embeddings.
Works with any server that speaks /v1/embeddings (vLLM, text-embeddings-inference,
Infinity, Ollama, ...).
"""
import logging
import time
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .settings import require_env

logger = logging.getLogger(__name__)


def _get_embedding_endpoint() -> Tuple[str, str]:
    """Get (hostname, port) parsed from the EMBEDDING_ENDPOINT env var."""
    endpoint = require_env("EMBEDDING_ENDPOINT")
    parsed = urlparse(endpoint)
    if not parsed.hostname:
        raise ValueError(f"EMBEDDING_ENDPOINT URL is missing hostname: {endpoint}")
    host = parsed.hostname
    if not parsed.port:
        raise ValueError(f"EMBEDDING_ENDPOINT URL is missing port: {endpoint}")
    port = str(parsed.port)
    return host, port


def _get_embedding_model() -> str:
    return require_env("EMBEDDING_MODEL")


# Batch size for embedding with heartbeat callbacks
DEFAULT_EMBEDDING_BATCH_SIZE = 64


class EmbeddingFunction:
    """
    Embedding function using an OpenAI-compatible embedding API.

    Provides a callable interface compatible with ClusterSemanticChunker's
    embedding_function parameter, calling the /v1/embeddings endpoint.

    Usage:
        embed_fn = EmbeddingFunction()
        embeddings = embed_fn(["text 1", "text 2", "text 3"])
    """

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[str] = None,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        timeout: float = 60.0
    ):
        self._model = model or _get_embedding_model()
        self._batch_size = batch_size
        self._progress_callback = None
        self._timeout = timeout

        if host is None or port is None:
            env_host, env_port = _get_embedding_endpoint()
            host = host or env_host
            port = port or env_port

        self._host = host
        self._base_url = f"http://{host}:{port}"

        logger.info(
            f"[Embedding] Initialized: model={self._model}, endpoint={self._base_url}, batch_size={batch_size}"
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
            response = client.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": self._model, "input": texts}
            )

            batch_elapsed = time.perf_counter() - batch_start_time

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    f"[Embedding] Batch {batch_num} FAILED after {batch_elapsed:.2f}s: "
                    f"{error_text} (status code: {response.status_code})"
                )
                raise RuntimeError(f"{error_text} (status code: {response.status_code})")

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

    def health_check(self) -> bool:
        """Check if the embedding service is reachable and returns vectors."""
        try:
            result = self._call_embedding_api(["health check"], 1, 1)
            return bool(result and len(result[0]) > 0)
        except Exception as e:
            logger.warning(f"Embedding health check failed: {e}")
            return False
```

> **Known limitation carried over from the reference impl.** The client
> reconstructs the base URL as `http://{host}:{port}`, so it ignores any
> URL scheme (always `http`) and any path prefix in `EMBEDDING_ENDPOINT`. For
> most self-hosted servers (vLLM, TEI, Ollama on `host:port`) this is fine. If
> you need `https` or a path-prefixed endpoint, change `__init__` to keep and
> use the full endpoint URL directly. This is a one-line generalization and a
> good first enhancement, but it is **not** required to preserve the algorithm.

---

## 8. Service shell

### `chunking/settings.py`

```python
"""Environment-driven configuration for the semantic chunking service."""
import os


def require_env(name: str) -> str:
    """Return a required env var, raising if missing/blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


# OOM guard: above this segment count, fall back to the O(N) greedy semantic
# path instead of building the O(N^2) similarity matrix. See cluster_semantic.py.
CHUNKER_MAX_SEGMENTS_DP = _int_env("CHUNKER_MAX_SEGMENTS_DP", 10_000)

# Bound on the DP reward LRU cache to keep memory predictable on huge inputs.
REWARD_CACHE_MAX_SIZE = _int_env("REWARD_CACHE_MAX_SIZE", 100_000)
```

### `chunking/strategies.py`

```python
"""Chunking strategy registry and version pinning."""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class StrategyParams:
    max_chunk_tokens: int = 400
    min_chunk_tokens: int = 50
    initial_segment_tokens: int = 50


DEFAULT_STRATEGY_VERSION = "cluster-semantic@1"

_STRATEGIES: Dict[str, StrategyParams] = {
    "cluster-semantic@1": StrategyParams(
        max_chunk_tokens=400,
        min_chunk_tokens=50,
        initial_segment_tokens=50,
    ),
}


def resolve_strategy(
    version: Optional[str],
    overrides: Optional[Dict[str, int]] = None,
) -> StrategyParams:
    """Resolve a strategy version to its params, applying optional overrides."""
    base = _STRATEGIES.get(version or DEFAULT_STRATEGY_VERSION)
    if base is None:
        raise KeyError(f"Unknown strategy_version: {version!r}")
    if not overrides:
        return base
    return StrategyParams(
        max_chunk_tokens=overrides.get("max_chunk_tokens", base.max_chunk_tokens),
        min_chunk_tokens=overrides.get("min_chunk_tokens", base.min_chunk_tokens),
        initial_segment_tokens=overrides.get("initial_segment_tokens", base.initial_segment_tokens),
    )
```

### `chunking/textprep.py`

```python
"""Source-type-aware pre-cleaning. Extension point: add format-specific
normalization here without touching the chunker."""
import re

_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", re.MULTILINE)
_MULTI_BLANK = re.compile(r"\n{3,}")


def preclean(text: str, source_type: str) -> str:
    """Light, reversible normalization keyed by source_type."""
    if source_type == "WEB_MARKDOWN":
        text = _IMAGE_LINE.sub("", text)      # drop standalone image embeds
        text = _MULTI_BLANK.sub("\n\n", text)  # collapse runs of blank lines
    return text.strip()
```

### `chunking/models.py`

```python
"""Pydantic request/response models for the /chunk endpoint."""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    DOCUMENT = "DOCUMENT"
    WEB_MARKDOWN = "WEB_MARKDOWN"


class ChunkParams(BaseModel):
    max_chunk_tokens: Optional[int] = None
    min_chunk_tokens: Optional[int] = None
    initial_segment_tokens: Optional[int] = None


class ChunkRequest(BaseModel):
    text: str
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
```

### `chunking/app.py`

```python
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
    except KeyError as exc:
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
            metadata={**req.metadata, "strategy_version": version},
        )
        for i, r in enumerate(results)
    ]
    return ChunkResponse(chunks=chunks, strategy_version=version, chunk_count=len(chunks))
```

`/chunk` is a synchronous `def`, so FastAPI runs it in its threadpool — the
blocking `httpx` embedding calls and numpy/DP work do not stall the event loop.
For higher concurrency, scale replicas horizontally (the service is stateless).

### `requirements.txt`

```
fastapi
uvicorn[standard]
httpx
numpy
pydantic>=2
```

---

## 9. Testing & parity

The chunker is the one piece whose behavior must not drift. Two test layers:

1. **Determinism / parity (golden-file).** With a **stubbed embedding function**
   (deterministic vectors, e.g. hash-seeded) the chunker is fully deterministic.
   Capture `split_text_with_metadata(sample)` output as a golden file and assert
   byte-for-byte equality on every change. If you are extracting this from an
   existing codebase, run the *same* stub through both the original and the
   extracted code and assert identical chunks — this is the proof the
   extraction preserved behavior.
2. **Fallback coverage.** Unit tests that force each safety valve:
   - segment count `> CHUNKER_MAX_SEGMENTS_DP` → greedy-semantic path,
   - embedding function raises / returns wrong count → token-based fallback,
   - constraints with no admissible segmentation → DP-no-solution fallback.

```python
# tests/test_parity.py  (sketch)
import hashlib
import numpy as np
from chunking.cluster_semantic import ClusterSemanticChunker


def fake_embed(texts):
    # Deterministic pseudo-embeddings: stable across runs and machines.
    out = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:16]
        out.append((v / (np.linalg.norm(v) or 1)).tolist())
    return out


def test_chunking_is_deterministic():
    chunker = ClusterSemanticChunker(embedding_function=fake_embed, max_chunk_size=40)
    text = "Alpha beta gamma. " * 50
    a = [c.text for c in chunker.split_text_with_metadata(text)]
    b = [c.text for c in chunker.split_text_with_metadata(text)]
    assert a == b            # determinism
    assert len(a) >= 1
```

The chunking service ships as its own container; see
[`03-deployment.md`](03-deployment.md) for the Dockerfile and compose wiring.
