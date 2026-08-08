from orchestrator.assembly import ResultAssemblerImpl
from orchestrator.types import Chunk, ScoredChunk
from orchestrator.url_identity import build_document_identity, evidence_id_for


def _sc(score, tokens, url, pos=0):
    return ScoredChunk(Chunk(text="x " * tokens, token_count=tokens, source_url=url, title="T", position=pos), score)


def test_budget_truncates_by_tokens_not_count():
    scored = [_sc(0.9, 300, "u1"), _sc(0.8, 300, "u2"), _sc(0.7, 300, "u3")]
    passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=650, max_passages=None)
    assert sum(p.token_count for p in passages) == 650
    assert len(passages) == 3
    assert passages[-1].token_count == 50


def test_citations_dedupe_and_align():
    scored = [_sc(0.9, 100, "https://a.test/x"), _sc(0.8, 100, "https://a.test/x")]
    passages, citations = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
    assert len({c.id for c in citations}) == len(citations) == 1
    assert all(p.citation_id == citations[0].id for p in passages)


def test_ordered_by_score_desc():
    scored = [_sc(0.2, 10, "u1"), _sc(0.9, 10, "u2")]
    passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
    assert [p.score for p in passages] == [0.9, 0.2]


def test_exact_budget_truncation_preserves_source_slice_and_recomputes_evidence_id():
    document = "one two three four five"
    identity = build_document_identity("https://example.test/final", document)
    original_evidence_id = evidence_id_for(
        identity.final_url,
        identity.cleaned_markdown_sha256,
        0,
        len(document),
    )
    chunk = Chunk(
        text=document,
        token_count=5,
        source_url=identity.final_url,
        title="Example",
        position=0,
        start_index=0,
        end_index=len(document),
        verbatim=True,
        document_id=identity.document_id,
        evidence_id=original_evidence_id,
        final_url=identity.final_url,
        cleaned_markdown_sha256=identity.cleaned_markdown_sha256,
        section_heading="Numbers",
    )

    passages, citations = ResultAssemblerImpl().assemble(
        [ScoredChunk(chunk, 1.0)],
        token_budget=3,
        max_passages=None,
    )

    passage = passages[0]
    assert passage.text == document[passage.start_index:passage.end_index] == "one two three"
    assert passage.evidence_id != original_evidence_id
    assert passage.document_id == identity.document_id
    assert passage.verbatim is True
    assert citations[0].evidence_spans[0].evidence_id == passage.evidence_id


def test_truncation_without_identity_inputs_degrades_to_non_verbatim():
    chunk = Chunk(
        text="one two three",
        token_count=3,
        source_url="https://example.test",
        title="Example",
        position=0,
        start_index=0,
        end_index=13,
        verbatim=True,
        document_id="a" * 64,
        evidence_id="b" * 64,
    )

    passages, citations = ResultAssemblerImpl().assemble(
        [ScoredChunk(chunk, 1.0)],
        token_budget=2,
        max_passages=None,
    )

    assert passages[0].text == "one two"
    assert passages[0].verbatim is False
    assert passages[0].start_index is None
    assert passages[0].end_index is None
    assert passages[0].evidence_id is None
    assert citations[0].evidence_spans[0].verbatim is False


def test_document_and_evidence_ids_do_not_depend_on_result_order():
    document = "Alpha evidence. Beta evidence."
    identity = build_document_identity("https://example.test/final", document)

    def chunk(start, end, position):
        return Chunk(
            text=document[start:end],
            token_count=len(document[start:end].split()),
            source_url=identity.final_url,
            title="Example",
            position=position,
            start_index=start,
            end_index=end,
            verbatim=True,
            document_id=identity.document_id,
            evidence_id=evidence_id_for(
                identity.final_url,
                identity.cleaned_markdown_sha256,
                start,
                end,
            ),
            final_url=identity.final_url,
            cleaned_markdown_sha256=identity.cleaned_markdown_sha256,
        )

    first = chunk(0, 15, 0)
    second = chunk(16, len(document), 1)
    forward, forward_citations = ResultAssemblerImpl().assemble(
        [ScoredChunk(first, 0.9), ScoredChunk(second, 0.8)],
        token_budget=20,
        max_passages=None,
    )
    reversed_order, reversed_citations = ResultAssemblerImpl().assemble(
        [ScoredChunk(first, 0.8), ScoredChunk(second, 0.9)],
        token_budget=20,
        max_passages=None,
    )

    assert {passage.text: passage.evidence_id for passage in forward} == {
        passage.text: passage.evidence_id for passage in reversed_order
    }
    assert forward_citations[0].document_id == reversed_citations[0].document_id == identity.document_id
