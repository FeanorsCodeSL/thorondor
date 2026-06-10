"""Pure candidate prefilter before expensive reranking."""
import re

from .types import Chunk, PrefilteredChunks

PREFILTER_STRATEGY = "lexical-source-preserving@1"


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _score(query_terms: set[str], chunk: Chunk) -> float:
    if not query_terms:
        return 1.0 / (chunk.position + 1)
    text_terms = _tokens(chunk.text)
    title_terms = _tokens(chunk.title)
    text_overlap = len(query_terms & text_terms) / len(query_terms)
    title_overlap = len(query_terms & title_terms) / len(query_terms)
    position_boost = 1.0 / ((chunk.position + 1) * 100.0)
    return text_overlap + (title_overlap * 0.35) + position_boost


def _source_key(chunk: Chunk) -> str:
    if chunk.source_id is not None:
        return f"id:{chunk.source_id}"
    return f"url:{chunk.source_url}"


class CandidatePrefilterImpl:
    def __init__(self, max_candidates: int = 50):
        self.max_candidates = max(1, max_candidates)

    def filter(self, query: str, chunks: list[Chunk]) -> PrefilteredChunks:
        if len(chunks) <= self.max_candidates:
            return PrefilteredChunks(chunks, 0, len(chunks), PREFILTER_STRATEGY)

        query_terms = _tokens(query)
        scored = [
            (_score(query_terms, chunk), index, chunk)
            for index, chunk in enumerate(chunks)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))

        best_by_source: dict[str, tuple[float, int, Chunk]] = {}
        for score, index, chunk in scored:
            key = _source_key(chunk)
            if key not in best_by_source:
                best_by_source[key] = (score, index, chunk)

        selected_indexes: set[int] = set()
        selected: list[tuple[float, int, Chunk]] = []
        for item in sorted(best_by_source.values(), key=lambda value: (-value[0], value[1])):
            if len(selected) >= self.max_candidates:
                break
            selected.append(item)
            selected_indexes.add(item[1])

        for item in scored:
            if len(selected) >= self.max_candidates:
                break
            if item[1] in selected_indexes:
                continue
            selected.append(item)
            selected_indexes.add(item[1])

        selected.sort(key=lambda item: (-item[0], item[1]))
        filtered = [chunk for _score_value, _index, chunk in selected]
        return PrefilteredChunks(
            chunks=filtered,
            chunks_prefiltered=len(chunks) - len(filtered),
            chunks_sent_to_reranker=len(filtered),
            prefilter_strategy=PREFILTER_STRATEGY,
        )
