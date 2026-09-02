"""BM25 lexical index. Owner: person 2.

Dense retrieval is bad at exactly what refinery staff type. "PSV-2103",
"drawing 4102-PID-006", "API 510" -- these are near-arbitrary strings whose
embedding carries almost no signal, and the nearest neighbour of a tag number
is a different tag number. BM25 finds them by matching them.

The tokenizer is the interesting part. A naive `\\w+` split turns `PSV-2103`
into `psv` and `2103`, which retrieves every relief valve in the corpus. So
compound identifiers survive whole *and* are additionally emitted in pieces,
letting a query match on either the full tag or a fragment of it.

The index is in-process and rebuilt from SQLite at startup. rank_bm25 keeps the
whole corpus in memory; at roughly 400 bytes a chunk that is 40 MB for a
100,000-chunk corpus, which is well inside the budget and much simpler than a
second server.
"""

from __future__ import annotations

import re
import threading

import ragconfig as cfg

# A token is a run of alphanumerics, optionally joined by - _ / . into a
# compound identifier: PSV-2103, 4102-PID-006, 12.5, API_510.
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-_/.][A-Za-z0-9]+)*")
SPLIT_RE = re.compile(r"[-_/.]")

_index = None
_lock = threading.Lock()


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, with compound identifiers kept whole and split."""
    tokens: list[str] = []
    for match in TOKEN_RE.findall(text.lower()):
        tokens.append(match)
        if SPLIT_RE.search(match):
            # Emit the parts too, so "2103" still finds "psv-2103". Single
            # characters are dropped: they match everything and rank nothing.
            tokens.extend(part for part in SPLIT_RE.split(match) if len(part) > 1)
    return tokens


class Bm25Index:
    """A frozen BM25 index over the corpus, plus the ids to map results back."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.model = None

    def build(self, chunks: list[dict]) -> int:
        """Build from chunk rows. Empty corpus leaves the index unusable, not broken."""
        from rank_bm25 import BM25Okapi  # noqa: PLC0415

        self.ids = [c["id"] for c in chunks]
        corpus = [tokenize(c["text"]) for c in chunks]
        # BM25Okapi divides by the average document length and cannot be built
        # from nothing. An empty corpus is a normal state before the first
        # upload, so it is represented as "no model" rather than an exception.
        self.model = BM25Okapi(corpus) if corpus else None
        return len(self.ids)

    def search(self, query: str, limit: int | None = None) -> list[tuple[str, float]]:
        """Top matches as [(chunk_id, score)], best first."""
        limit = limit or cfg.SPARSE_TOP
        if self.model is None or not self.ids:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.model.get_scores(tokens)
        ranked = sorted(
            ((self.ids[i], float(score)) for i, score in enumerate(scores)),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [pair for pair in ranked[:limit] if pair[1] > 0.0]

    @property
    def size(self) -> int:
        return len(self.ids)


def get_index() -> Bm25Index:
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is None:
            _index = Bm25Index()
    return _index


def rebuild(chunks: list[dict] | None = None) -> int:
    """Rebuild the index from SQLite, or from the rows given.

    Called at startup and after every ingest. A full rebuild is chosen over
    incremental updates on purpose: rank_bm25 has no incremental API, IDF
    depends on the whole corpus anyway, and rebuilding 10,000 chunks takes
    under a second.
    """
    import ragdb  # noqa: PLC0415

    index = get_index()
    return index.build(chunks if chunks is not None else ragdb.all_chunks())


def reset() -> None:
    global _index
    _index = None
