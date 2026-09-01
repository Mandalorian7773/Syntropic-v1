"""Hybrid retrieval: dense + sparse, fused, reranked. Owner: person 2.

    dense top 30  ─┐
                   ├─ RRF(k=60) ─ top 30 ─ cross-encoder ─ top_k
    BM25 top 30   ─┘

Budget is 2 seconds on CPU for top_k=5, and the reranker owns most of it.

Every hit carries `filename` and `page`, always, with no second lookup. The
agent turns those into citation events and the frontend renders them; a hit
without provenance is a bug, so provenance is attached at the point the hit is
constructed and there is no code path that produces a Hit without it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import ragconfig as cfg

from . import bm25, fuse, qdrant_store
from .embed import get_embedder
from .rerank import get_reranker


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    filename: str
    page: int
    section: str
    text: str
    score: float
    sources: list[str] = field(default_factory=list)
    dense_rank: int | None = None
    sparse_rank: int | None = None
    reranked: bool = False

    def snippet(self, limit: int = 320) -> str:
        flat = " ".join(self.text.split())
        return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    reranked: bool = False

    @property
    def total_ms(self) -> int:
        return sum(self.timings_ms.values())


def _hydrate(ids: list[str], payloads: dict[str, dict]) -> dict[str, dict]:
    """Fill in chunk fields for ids the dense search did not return payloads for.

    A BM25-only hit has no Qdrant payload, so its text and provenance come from
    SQLite. This is the one place a hit could end up without a filename, so it
    is also the one place that has to be certain it does not.
    """
    import ragdb  # noqa: PLC0415

    missing = [i for i in ids if i not in payloads]
    if missing:
        rows = ragdb.get_chunks(missing)
        for chunk_id, row in rows.items():
            payloads[chunk_id] = {
                "doc_id": row["doc_id"],
                "filename": row["filename"],
                "page": row["page"],
                "chunk_index": row["chunk_index"],
                "text": row["text"],
                "section": row["section"] or "",
            }
    return payloads


def search(
    query: str,
    top_k: int = 5,
    *,
    mode: str = "hybrid",
    use_rerank: bool | None = None,
) -> SearchResult:
    """Run the hybrid pipeline. `mode` is "hybrid", "dense" or "sparse".

    The non-hybrid modes exist for the eval harness: "we added a reranker" has
    to become a number, and the only way to get that number is to be able to
    turn each stage off and re-measure.
    """
    result = SearchResult()
    query = (query or "").strip()
    if not query:
        return result

    use_rerank = cfg.RERANK_ENABLED if use_rerank is None else use_rerank
    ranked_lists: dict[str, list[str]] = {}
    payloads: dict[str, dict] = {}

    if mode in {"hybrid", "dense"}:
        started = time.perf_counter()
        vector = get_embedder().encode_one(query)
        result.timings_ms["embed"] = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        dense = qdrant_store.get_store().search(vector, cfg.DENSE_TOP)
        result.timings_ms["dense"] = int((time.perf_counter() - started) * 1000)
        ranked_lists["dense"] = [d["id"] for d in dense]
        payloads.update({d["id"]: d["payload"] for d in dense})

    if mode in {"hybrid", "sparse"}:
        started = time.perf_counter()
        sparse = bm25.get_index().search(query, cfg.SPARSE_TOP)
        result.timings_ms["sparse"] = int((time.perf_counter() - started) * 1000)
        ranked_lists["sparse"] = [chunk_id for chunk_id, _ in sparse]

    fused = fuse.reciprocal_rank_fusion(ranked_lists, limit=cfg.FUSE_KEEP)
    result.candidates = len(fused)
    if not fused:
        return result

    payloads = _hydrate([f.id for f in fused], payloads)
    # A candidate we cannot attribute to a file and a page is dropped rather
    # than returned unattributed. It can only happen when Qdrant and SQLite
    # have drifted, which a reindex fixes; shipping it would put an
    # uncitable claim in front of a judge.
    fused = [f for f in fused if f.id in payloads and payloads[f.id].get("filename")]

    order = fused
    scores = {f.id: f.score for f in fused}
    reranked = False

    if use_rerank and len(fused) > 1:
        started = time.perf_counter()
        try:
            texts = [payloads[f.id]["text"] for f in fused]
            logits = get_reranker().score(query, texts)
            pairs = sorted(zip(fused, logits), key=lambda pair: float(pair[1]), reverse=True)
            order = [f for f, _ in pairs]
            scores = {f.id: float(s) for f, s in pairs}
            reranked = True
        except FileNotFoundError:
            # Weights absent: fall back to the fused order rather than fail the
            # query. /health reports the missing model, loudly and separately.
            order = fused
        result.timings_ms["rerank"] = int((time.perf_counter() - started) * 1000)

    result.reranked = reranked
    for candidate in order[:top_k]:
        payload = payloads[candidate.id]
        result.hits.append(
            Hit(
                chunk_id=candidate.id,
                doc_id=payload["doc_id"],
                filename=payload["filename"],
                page=int(payload["page"]),
                section=payload.get("section", "") or "",
                text=payload["text"],
                score=float(scores[candidate.id]),
                sources=candidate.sources,
                dense_rank=candidate.ranks.get("dense"),
                sparse_rank=candidate.ranks.get("sparse"),
                reranked=reranked,
            )
        )
    return result
