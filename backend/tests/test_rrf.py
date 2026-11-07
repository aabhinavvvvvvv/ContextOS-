"""Reciprocal Rank Fusion: pure arithmetic on ranked lists, no database needed."""

import uuid

import pytest

from app.search.service import SearchHit, reciprocal_rank_fusion

IDS = {name: uuid.uuid4() for name in "ABCD"}


def hit(name: str, score: float = 0.0) -> SearchHit:
    return SearchHit(
        chunk_id=IDS[name],
        document_id=uuid.uuid4(),
        filename="doc.md",
        chunk_index=0,
        heading=name,
        page_number=None,
        text=name,
        score=score,  # the original method's score; RRF ignores it
    )


def names(hits: list[SearchHit]) -> list[str]:
    return [h.heading for h in hits]


def test_a_chunk_found_by_both_methods_beats_a_single_first_place() -> None:
    semantic = [hit("A", 0.9), hit("C", 0.8), hit("B", 0.7)]  # B is 3rd here...
    keyword = [hit("D", 5.0), hit("B", 4.0)]  # ...and 2nd here

    fused = reciprocal_rank_fusion(semantic, keyword, k=60)

    assert names(fused) == ["B", "A", "D", "C"]
    assert fused[0].score == pytest.approx(1 / 63 + 1 / 62)
    assert fused[1].score == pytest.approx(1 / 61)


def test_fused_hits_record_their_rank_in_each_method() -> None:
    fused = reciprocal_rank_fusion([hit("A"), hit("B")], [hit("B")])

    ranks = {h.heading: (h.semantic_rank, h.keyword_rank) for h in fused}
    assert ranks == {"A": (1, None), "B": (2, 1)}


def test_equal_scores_keep_semantic_order_first() -> None:
    # A is 1st in semantic only, D is 1st in keyword only: identical scores (1/61).
    fused = reciprocal_rank_fusion([hit("A")], [hit("D")])

    assert names(fused) == ["A", "D"]


def test_one_empty_list_keeps_the_other_ranking() -> None:
    assert names(reciprocal_rank_fusion([hit("A"), hit("B")], [])) == ["A", "B"]
    assert names(reciprocal_rank_fusion([], [hit("D"), hit("C")])) == ["D", "C"]
    assert reciprocal_rank_fusion([], []) == []
