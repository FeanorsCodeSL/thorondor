from orchestrator.assembly import ResultAssemblerImpl
from orchestrator.types import Chunk, ScoredChunk


def _sc(score, tokens, url, pos=0):
    return ScoredChunk(Chunk(text="x " * tokens, token_count=tokens, source_url=url, title="T", position=pos), score)


def test_budget_truncates_by_tokens_not_count():
    scored = [_sc(0.9, 300, "u1"), _sc(0.8, 300, "u2"), _sc(0.7, 300, "u3")]
    passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=650, max_passages=None)
    assert sum(p.token_count for p in passages) <= 650 and len(passages) == 2


def test_citations_dedupe_and_align():
    scored = [_sc(0.9, 100, "https://a.test/x"), _sc(0.8, 100, "https://a.test/x")]
    passages, citations = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
    assert len({c.id for c in citations}) == len(citations) == 1
    assert all(p.citation_id == citations[0].id for p in passages)


def test_ordered_by_score_desc():
    scored = [_sc(0.2, 10, "u1"), _sc(0.9, 10, "u2")]
    passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
    assert [p.score for p in passages] == [0.9, 0.2]
