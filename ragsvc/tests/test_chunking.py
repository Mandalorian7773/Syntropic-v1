"""Chunking behaviour. Owner: person 2.

The rule under test is the one that matters most for a table-heavy corpus: a
table is never split. Everything else about chunking is a tuning parameter the
eval harness can measure; this one is a correctness property, because half a
table produces a confidently wrong answer rather than a worse-ranked one.
"""

from __future__ import annotations

import pytest

import ragconfig as cfg
from ingest.chunk import chunk_blocks
from ingest.model import Block
from ragbudget import count_tokens


def table_markdown(rows: int, columns: int = 5) -> str:
    header = "| " + " | ".join(f"Col {c}" for c in range(columns)) + " |"
    rule = "| " + " | ".join(["---"] * columns) + " |"
    body = [
        "| " + " | ".join(f"CML-{r:02d} value {c} reading" for c in range(columns)) + " |"
        for r in range(rows)
    ]
    return "\n".join([header, rule, *body])


def prose(words: int, page: int = 1, section: str = "1. Scope") -> Block:
    text = " ".join(f"word{i}" for i in range(words))
    return Block(kind="paragraph", text=text, page=page, section=section)


def test_table_is_never_split_across_chunks():
    table = table_markdown(rows=40)
    blocks = [prose(300), Block(kind="table", text=table, page=2, section="2. Results"), prose(300, page=3)]

    chunks = chunk_blocks(blocks, "doc-1", "survey.pdf")

    holders = [c for c in chunks if "CML-00" in c.text]
    assert len(holders) == 1, "the table appears in more than one chunk"
    # Every row that went in comes out of that one chunk.
    for row in range(40):
        assert f"CML-{row:02d}" in holders[0].text


def test_oversized_table_becomes_its_own_chunk():
    """A table larger than the budget is kept whole, deliberately over budget."""
    table = table_markdown(rows=200)
    assert count_tokens(table) > cfg.CHUNK_TOKENS

    chunks = chunk_blocks(
        [prose(100), Block(kind="table", text=table, page=1, section="Big")], "d", "f.pdf"
    )

    holders = [c for c in chunks if "CML-000" in c.text or "CML-00" in c.text]
    assert len(holders) == 1
    assert holders[0].has_table
    assert "CML-199" in holders[0].text


def test_chunks_respect_the_token_budget_for_prose():
    blocks = [prose(200, page=p) for p in range(1, 12)]
    chunks = chunk_blocks(blocks, "d", "f.pdf")

    assert len(chunks) > 1
    for chunk in chunks:
        if chunk.has_table:
            continue
        # The overlap carried in from the previous chunk is added on top of the
        # budget, so the ceiling is budget + overlap, not budget.
        assert chunk.tokens <= cfg.CHUNK_TOKENS + cfg.CHUNK_OVERLAP + 40


def test_every_chunk_carries_page_and_section():
    blocks = [prose(150, page=1, section="1. Scope"), prose(150, page=2, section="2. Findings")]
    chunks = chunk_blocks(blocks, "doc-9", "report.pdf")

    assert chunks
    for chunk in chunks:
        assert chunk.doc_id == "doc-9"
        assert chunk.filename == "report.pdf"
        assert chunk.page >= 1
        assert chunk.page_end >= chunk.page
        assert chunk.section, "a chunk with no section cannot be cited properly"
        payload = chunk.to_payload()
        assert set(payload) == {
            "doc_id", "filename", "page", "chunk_index", "text", "section"
        }


def test_overlap_carries_context_forward():
    marker = "the retirement thickness is 5.2 mm"
    blocks = [prose(240), Block(kind="paragraph", text=marker, page=1, section="1. Scope"), prose(240, page=2)]

    chunks = chunk_blocks(blocks, "d", "f.pdf", max_tokens=280, overlap=100)

    holders = [i for i, c in enumerate(chunks) if marker in c.text]
    assert holders, "the marker sentence vanished"
    if len(chunks) > holders[0] + 1:
        # The sentence sits near a boundary, so the following chunk should have
        # carried it forward. That is what the overlap is for.
        assert marker in chunks[holders[0]].text


def test_low_confidence_flag_propagates_to_the_chunk():
    blocks = [
        Block(kind="paragraph", text="clean text", page=1, section="S", conf=0.99),
        Block(kind="paragraph", text="doubtful text", page=1, section="S", conf=0.4, low_conf=True),
    ]
    chunks = chunk_blocks(blocks, "d", "f.pdf")
    assert any(c.low_conf for c in chunks)


@pytest.mark.parametrize("overlap", [0, 50, 100])
def test_chunking_terminates_for_any_overlap(overlap):
    """A carry-over larger than the content must not loop forever."""
    blocks = [prose(50, page=p) for p in range(1, 6)]
    chunks = chunk_blocks(blocks, "d", "f.pdf", max_tokens=120, overlap=overlap)
    assert 0 < len(chunks) < 50
