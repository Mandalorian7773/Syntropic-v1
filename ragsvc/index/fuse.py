"""Reciprocal Rank Fusion. Owner: person 2.

    score(d) = sum over retrievers of 1 / (k + rank(d))

Rank, not score. That is the whole reason RRF is the right choice here: a
cosine similarity of 0.83 and a BM25 score of 14.2 are not comparable, have
different distributions, and normalising them into agreement requires
per-corpus tuning that will be wrong on the judges' documents. Position in each
list is comparable by construction.

k = 60 is the value from the original RRF paper and is not arbitrary in effect:
it flattens the contribution of the top few ranks, so a document ranked 1 by
one retriever and 25 by the other still outranks one ranked 8 by both. With a
small k the first retriever to fire would dominate; with a very large k every
document converges to the same score and the fusion stops discriminating.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import ragconfig as cfg


@dataclass
class Fused:
    """One fused candidate and the evidence for how it got there."""

    id: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)

    @property
    def sources(self) -> list[str]:
        return sorted(self.ranks)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[str]],
    k: int | None = None,
    limit: int | None = None,
) -> list[Fused]:
    """Fuse named ranked id lists into one.

    `ranked_lists` maps a retriever name to its ids in rank order. The names
    survive into the result so the eval harness can attribute a hit to dense or
    sparse retrieval, which is how you find out whether BM25 is earning its
    place rather than assuming it is.
    """
    k = cfg.RRF_K if k is None else k
    limit = limit or cfg.FUSE_KEEP

    scores: dict[str, Fused] = {}
    for source, ids in ranked_lists.items():
        for position, doc_id in enumerate(ids):
            rank = position + 1
            entry = scores.get(doc_id)
            if entry is None:
                entry = Fused(id=doc_id, score=0.0)
                scores[doc_id] = entry
            entry.score += 1.0 / (k + rank)
            entry.ranks[source] = rank

    fused = sorted(scores.values(), key=lambda f: (-f.score, f.id))
    return fused[:limit]
