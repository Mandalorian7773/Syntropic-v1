"""Chunking. Owner: person 2.

600 tokens with 100 of overlap, and one rule that overrides both: **a table is
never split.** A half table retrieves as well as no table and answers worse
than nothing, because the model reads five of nine rows and reports a maximum
that is not the maximum. When a table alone exceeds the budget it becomes an
oversized chunk of its own; that is the intended outcome, not a bug.

Tokens are counted with the embedding model's own tokenizer, so "600 tokens"
means 600 tokens to the model that will encode it, not 600 words.

Every chunk carries `page` and `section`. Those two fields are the entire
reason a citation can name a real page without a second lookup, so they are
computed here and never inferred later.
"""

from __future__ import annotations

import re
import uuid

import ragconfig as cfg
from ragbudget import count_tokens, tail_to_tokens

from .model import Block, Chunk

SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def _tail_tokens(text: str, n_tokens: int) -> str:
    """Last ~n_tokens of text, cut on a sentence boundary where possible.

    This is the overlap. It exists so a fact that straddles a chunk boundary is
    retrievable from at least one side of it; starting the carry-over
    mid-sentence would defeat that.
    """
    if n_tokens <= 0 or not text:
        return ""
    sentences = SENTENCE_END.split(text)
    carried: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        cost = count_tokens(sentence)
        if total + cost > n_tokens:
            break
        carried.insert(0, sentence)
        total += cost
    if carried:
        return " ".join(carried).strip()

    # No whole sentence fits inside the overlap. That is the normal case for
    # OCR'd forms and table text, which often contain no sentence-ending
    # punctuation at all, so falling back to "carry it whole" would drag an
    # entire 600-token block into the next chunk and roughly double its size.
    return tail_to_tokens(text, n_tokens)


def _split_long_text(text: str, budget: int) -> list[str]:
    """Break a paragraph that is longer than a whole chunk, on sentences."""
    pieces: list[str] = []
    current: list[str] = []
    total = 0
    for sentence in SENTENCE_END.split(text):
        cost = count_tokens(sentence)
        if current and total + cost > budget:
            pieces.append(" ".join(current))
            current, total = [], 0
        current.append(sentence)
        total += cost
    if current:
        pieces.append(" ".join(current))
    return pieces


class _Accumulator:
    """Blocks gathered for the chunk currently being built."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.carry: str = ""
        self.carry_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.carry_tokens + sum(count_tokens(b.text) for b in self.blocks)

    @property
    def empty(self) -> bool:
        return not self.blocks


def chunk_blocks(
    blocks: list[Block],
    doc_id: str,
    filename: str,
    *,
    max_tokens: int | None = None,
    overlap: int | None = None,
    contextualise: bool = True,
) -> list[Chunk]:
    """Group ordered blocks into retrievable chunks.

    `contextualise` prepends the section heading to a chunk that does not
    already start with it. It measurably helps both retrievers -- dense,
    because the topic is in the vector; sparse, because "Relief Valve Testing"
    is exactly what someone types -- and it costs about eight tokens.
    """
    max_tokens = max_tokens or cfg.CHUNK_TOKENS
    overlap = cfg.CHUNK_OVERLAP if overlap is None else overlap

    chunks: list[Chunk] = []
    acc = _Accumulator()

    def flush() -> None:
        if acc.empty:
            return
        body_parts = ([acc.carry] if acc.carry else []) + [b.text for b in acc.blocks]
        body = "\n\n".join(part for part in body_parts if part.strip())
        pages = [b.page for b in acc.blocks]
        section = next((b.section for b in acc.blocks if b.section), "")

        text = body
        if contextualise and section and not body.lstrip().startswith(section):
            text = f"{section}\n\n{body}"

        chunks.append(
            Chunk(
                id=str(uuid.uuid4()),
                doc_id=doc_id,
                filename=filename,
                chunk_index=len(chunks),
                page=min(pages),
                page_end=max(pages),
                section=section,
                text=text,
                tokens=count_tokens(text),
                has_table=any(b.is_table for b in acc.blocks),
                low_conf=any(b.low_conf for b in acc.blocks),
            )
        )

        # Seed the next chunk with the tail of this one. Tables are excluded:
        # carrying half a table forward reintroduces exactly the split this
        # function exists to prevent.
        prose = "\n\n".join(b.text for b in acc.blocks if not b.is_table)
        acc.blocks = []
        acc.carry = _tail_tokens(prose, overlap)
        acc.carry_tokens = count_tokens(acc.carry)

    for block in blocks:
        block_tokens = count_tokens(block.text)

        if block.is_table:
            # Start a fresh chunk rather than push the table over the limit,
            # then close immediately if the table filled it.
            if not acc.empty and acc.tokens + block_tokens > max_tokens:
                flush()
            acc.blocks.append(block)
            if acc.tokens >= max_tokens:
                flush()
            continue

        if block_tokens > max_tokens:
            flush()
            for piece in _split_long_text(block.text, max_tokens):
                acc.blocks.append(
                    Block(
                        kind=block.kind,
                        text=piece,
                        page=block.page,
                        section=block.section,
                        conf=block.conf,
                        low_conf=block.low_conf,
                    )
                )
                flush()
            continue

        if not acc.empty and acc.tokens + block_tokens > max_tokens:
            flush()
        acc.blocks.append(block)

    flush()
    return chunks
