"""Assemble scored chunks into passages and citations."""
import re
from dataclasses import replace

from .normalize import normalize_url
from .types import AssembledCitation, AssembledPassage, EvidenceSpan, ScoredChunk
from .url_identity import evidence_id_for


def _truncate_to_budget(item: ScoredChunk, token_budget: int) -> ScoredChunk | None:
    if item.chunk.token_count <= token_budget:
        return item
    if token_budget < 1:
        return None
    matches = list(re.finditer(r"\S+", item.chunk.text))
    if len(matches) != item.chunk.token_count or len(matches) < token_budget:
        return None
    relative_end = matches[token_budget - 1].end()
    preserves_identity = (
        item.chunk.verbatim
        and item.chunk.document_id is not None
        and item.chunk.final_url is not None
        and item.chunk.cleaned_markdown_sha256 is not None
        and item.chunk.start_index is not None
    )
    end_index = (
        item.chunk.start_index + relative_end
        if preserves_identity
        else None
    )
    evidence_id = (
        evidence_id_for(
            item.chunk.final_url,
            item.chunk.cleaned_markdown_sha256,
            item.chunk.start_index,
            end_index,
        )
        if preserves_identity and end_index is not None
        else None
    )
    chunk = replace(
        item.chunk,
        text=item.chunk.text[:relative_end],
        token_count=token_budget,
        start_index=item.chunk.start_index if preserves_identity else None,
        end_index=end_index,
        verbatim=preserves_identity,
        evidence_id=evidence_id,
    )
    return ScoredChunk(chunk=chunk, score=item.score)


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
            item = _truncate_to_budget(item, token_budget - total_tokens)
            if item is None:
                continue

            key = item.chunk.document_id or normalize_url(item.chunk.source_url)
            citation_id = citation_ids.get(key)
            if citation_id is None:
                citation_id = len(citations) + 1
                citation_ids[key] = citation_id
                metadata_title = (
                    item.chunk.evidence_metadata.get("title")
                    if item.chunk.evidence_metadata is not None
                    else None
                )
                citations.append(
                    AssembledCitation(
                        id=citation_id,
                        url=item.chunk.source_url,
                        title=(
                            metadata_title.value
                            if metadata_title is not None
                            else item.chunk.title
                        ),
                        source_id=item.chunk.source_id,
                        document_id=item.chunk.document_id,
                        evidence_metadata=item.chunk.evidence_metadata,
                    )
                )

            citation = citations[citation_id - 1]
            span = EvidenceSpan(
                start_index=item.chunk.start_index,
                end_index=item.chunk.end_index,
                verbatim=item.chunk.verbatim,
                evidence_id=item.chunk.evidence_id,
                section_heading=item.chunk.section_heading,
            )
            if span not in citation.evidence_spans:
                citation.evidence_spans.append(span)

            passages.append(
                AssembledPassage(
                    text=item.chunk.text,
                    score=item.score,
                    token_count=item.chunk.token_count,
                    citation_id=citation_id,
                    start_index=item.chunk.start_index,
                    end_index=item.chunk.end_index,
                    verbatim=item.chunk.verbatim,
                    document_id=item.chunk.document_id,
                    evidence_id=item.chunk.evidence_id,
                    section_heading=item.chunk.section_heading,
                )
            )
            total_tokens += item.chunk.token_count

        return passages, citations
