"""Assemble scored chunks into passages and citations."""
from .normalize import normalize_url
from .types import AssembledCitation, AssembledPassage, ScoredChunk


class ResultAssemblerImpl:
    def assemble(
        self,
        scored: list[ScoredChunk],
        token_budget: int,
        max_passages: int | None,
    ) -> tuple[list[AssembledPassage], list[AssembledCitation]]:
        citation_ids: dict[str, int] = {}
        citations: list[AssembledCitation] = []
        passages: list[AssembledPassage] = []
        total_tokens = 0

        for item in sorted(scored, key=lambda s: -s.score):
            if max_passages is not None and len(passages) >= max_passages:
                break
            if total_tokens + item.chunk.token_count > token_budget:
                continue

            key = normalize_url(item.chunk.source_url)
            citation_id = citation_ids.get(key)
            if citation_id is None:
                citation_id = len(citations) + 1
                citation_ids[key] = citation_id
                citations.append(
                    AssembledCitation(
                        id=citation_id,
                        url=item.chunk.source_url,
                        title=item.chunk.title,
                        source_id=item.chunk.source_id,
                    )
                )

            passages.append(
                AssembledPassage(
                    text=item.chunk.text,
                    score=item.score,
                    token_count=item.chunk.token_count,
                    citation_id=citation_id,
                )
            )
            total_tokens += item.chunk.token_count

        return passages, citations
