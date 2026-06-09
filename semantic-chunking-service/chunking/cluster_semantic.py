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
            min_chunk_size: Soft minimum token target per chunk. Final and
                          degenerate chunks can be smaller.
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
                segment_indices=[0],
                chunk_strategy="cluster-semantic-single"
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
        diagonal_sum = float(np.trace(similarity_matrix))
        avg_similarity = (
            (similarity_matrix.sum() - diagonal_sum) / (n * (n - 1))
            if n > 1 else 0
        )
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
        # could grow to O(n^2) entries; LRU eviction keeps memory bounded. This
        # is intentionally per-DP-run because rewards depend on the current
        # similarity matrix; do not hoist it to instance or module scope.
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
        for result in results:
            result.chunk_strategy = "cluster-semantic-greedy-token"
            result.embedding_degraded = True
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
                segment_indices=[0],
                chunk_strategy="cluster-semantic-greedy-semantic"
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
        for result in results:
            result.chunk_strategy = "cluster-semantic-greedy-semantic"

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
