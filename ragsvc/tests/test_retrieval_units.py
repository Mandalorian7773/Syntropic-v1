"""Fusion and lexical-index behaviour. Owner: person 2.

These are the two pieces of retrieval that can be tested without loading 600 MB
of weights, and they are also the two most likely to be quietly wrong: RRF is
four lines that are easy to write with an off-by-one, and the BM25 tokenizer is
the whole reason hybrid retrieval beats dense retrieval on tag numbers.
"""

from __future__ import annotations

from index.bm25 import Bm25Index, tokenize
from index.fuse import reciprocal_rank_fusion


# --- tokenizer --------------------------------------------------------------


def test_tag_numbers_survive_tokenisation_whole():
    tokens = tokenize("Relief valve PSV-2103 on line 6-P-2104")
    assert "psv-2103" in tokens, "a split tag number matches every relief valve"
    assert "6-p-2104" in tokens


def test_tag_numbers_are_also_emitted_in_parts():
    """So that a query of "2103" still finds "PSV-2103"."""
    tokens = tokenize("PSV-2103")
    assert "psv-2103" in tokens
    assert "2103" in tokens
    assert "ps" not in tokens


def test_single_characters_are_dropped_from_compound_parts():
    tokens = tokenize("8-P-1104")
    assert "8-p-1104" in tokens
    assert "p" not in tokens, "a one-character token matches everything and ranks nothing"


def test_reference_numbers_with_slashes_survive():
    tokens = tokenize("Our quotation QTN/BHE/2024/0871 refers")
    assert "qtn/bhe/2024/0871" in tokens
    assert "0871" in tokens


# --- BM25 -------------------------------------------------------------------


def test_bm25_finds_an_exact_tag_over_a_semantic_neighbour():
    index = Bm25Index()
    index.build(
        [
            {"id": "a", "text": "PSV-2103 has a set pressure of 12.5 barg."},
            {"id": "b", "text": "PSV-2104 has a set pressure of 18.0 barg."},
            {"id": "c", "text": "Relief valves protect equipment from overpressure."},
        ]
    )
    results = index.search("PSV-2103 set pressure", limit=3)
    assert results, "BM25 returned nothing for an exact tag query"
    assert results[0][0] == "a"


def test_empty_corpus_is_a_state_not_an_error():
    index = Bm25Index()
    index.build([])
    assert index.search("anything") == []
    assert index.size == 0


def test_query_with_no_usable_tokens_returns_nothing():
    index = Bm25Index()
    index.build([{"id": "a", "text": "some text"}])
    assert index.search("!!! ???") == []


# --- reciprocal rank fusion -------------------------------------------------


def test_rrf_scores_match_the_formula():
    fused = reciprocal_rank_fusion({"dense": ["a", "b"], "sparse": ["b", "a"]}, k=60)
    scores = {f.id: f.score for f in fused}
    expected = 1 / 61 + 1 / 62
    assert abs(scores["a"] - expected) < 1e-9
    assert abs(scores["b"] - expected) < 1e-9


def test_agreement_between_retrievers_outranks_a_single_first_place():
    """A document both retrievers like beats one only dense ranks first."""
    fused = reciprocal_rank_fusion(
        {"dense": ["solo", "agreed"], "sparse": ["agreed", "other"]}, k=60
    )
    assert fused[0].id == "agreed"


def test_fusion_records_which_retriever_found_what():
    fused = reciprocal_rank_fusion({"dense": ["a"], "sparse": ["b"]})
    by_id = {f.id: f for f in fused}
    assert by_id["a"].sources == ["dense"]
    assert by_id["b"].sources == ["sparse"]
    assert by_id["a"].ranks["dense"] == 1


def test_fusion_respects_the_candidate_limit():
    ids = [f"d{i}" for i in range(50)]
    fused = reciprocal_rank_fusion({"dense": ids}, limit=30)
    assert len(fused) == 30


def test_a_single_retriever_still_fuses():
    fused = reciprocal_rank_fusion({"sparse": ["x", "y"]})
    assert [f.id for f in fused] == ["x", "y"]
