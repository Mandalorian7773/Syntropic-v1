"""Layout reconstruction and the egress guard. Owner: person 2.

Layout is where retrieval quality is won or lost on a table-heavy corpus, and
it is pure geometry, so it can be tested exactly with synthetic line boxes --
no OCR, no weights, no fixtures.

The egress guard is tested here too because it is a *claim* the project makes
to judges, and an untested claim is a hope.
"""

from __future__ import annotations

import socket

import pytest

from ingest.layout import (
    build_blocks,
    detect_aligned_tables,
    detect_columns,
    group_rows,
    is_heading,
    rows_to_grid,
    rows_to_markdown,
)
from ingest.model import Line


def line(text: str, x: float, y: float, width: float = 60, height: float = 10, **kwargs) -> Line:
    return Line(text=text, bbox=(x, y, x + width, y + height), **kwargs)


def grid_lines(rows: list[list[str]], top: float = 100.0) -> list[Line]:
    """Lay cells out on a real coordinate grid, one row per 20 points."""
    out = []
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            out.append(line(cell, 50 + column_index * 120, top + row_index * 20))
    return out


# --- rows and columns -------------------------------------------------------


def test_group_rows_clusters_by_vertical_midpoint():
    lines = [line("a", 50, 100), line("b", 200, 102), line("c", 50, 140)]
    rows = group_rows(lines, tol=6)
    assert len(rows) == 2
    assert [ln.text for ln in rows[0]] == ["a", "b"]


def test_group_rows_orders_cells_left_to_right():
    rows = group_rows([line("right", 300, 100), line("left", 50, 100)], tol=6)
    assert [ln.text for ln in rows[0]] == ["left", "right"]


def test_two_column_pages_are_read_column_by_column():
    left = [line(f"L{i}", 50, 100 + i * 15, width=150) for i in range(8)]
    right = [line(f"R{i}", 320, 100 + i * 15, width=150) for i in range(8)]
    columns = detect_columns(left + right, page_width=595)
    assert len(columns) == 2
    assert all(ln.text.startswith("L") for ln in columns[0])


def test_a_full_width_line_prevents_a_column_split():
    """A heading spanning the page means it is not a two-column layout."""
    left = [line(f"L{i}", 50, 100 + i * 15, width=150) for i in range(8)]
    right = [line(f"R{i}", 320, 100 + i * 15, width=150) for i in range(8)]
    spanning = [line("A HEADING ACROSS THE WHOLE PAGE", 50, 80, width=480)]
    assert len(detect_columns(left + right + spanning, page_width=595)) == 1


# --- tables -----------------------------------------------------------------


def test_markdown_table_has_a_header_and_a_rule():
    markdown = rows_to_markdown([["CML", "Measured"], ["CML-12", "7.80"]])
    lines = markdown.splitlines()
    assert lines[0] == "| CML | Measured |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| CML-12 | 7.80 |"


def test_pipes_inside_cells_are_escaped():
    markdown = rows_to_markdown([["a"], ["x | y"]])
    assert r"x \| y" in markdown


def test_ragged_rows_are_padded_to_a_rectangle():
    markdown = rows_to_markdown([["a", "b", "c"], ["1"]])
    assert markdown.splitlines()[2].count("|") == 4


def test_aligned_rows_are_detected_as_a_borderless_table():
    rows = group_rows(
        grid_lines(
            [
                ["CML", "Nominal", "Measured", "Rate"],
                ["CML-01", "8.18", "7.91", "0.04"],
                ["CML-02", "8.18", "7.50", "0.12"],
                ["CML-03", "8.18", "7.26", "0.15"],
            ]
        ),
        tol=6,
    )
    spans = detect_aligned_tables(rows)
    assert spans == [(0, 3)]


def test_ordinary_prose_is_not_mistaken_for_a_table():
    rows = group_rows(
        [line("This is an ordinary sentence of prose text.", 50, 100 + i * 20, width=400)
         for i in range(5)],
        tol=6,
    )
    assert detect_aligned_tables(rows) == []


def test_cells_are_assigned_to_the_column_they_line_up_under():
    rows = group_rows(
        grid_lines([["CML", "Measured"], ["CML-12", "7.80"], ["CML-19", "5.62"]]), tol=6
    )
    grid = rows_to_grid(rows)
    assert grid[1] == ["CML-12", "7.80"]
    assert grid[2] == ["CML-19", "5.62"]


def test_two_columns_are_emitted_one_after_the_other_not_interleaved():
    """Reading order on a two-column page is column by column, not line by line.

    Sorting purely by vertical position would produce L0 R0 L1 R1 ..., which is
    text that neither a human nor a model can follow.
    """
    left = [line(f"L{i}", 50, 100 + i * 15, width=150) for i in range(8)]
    right = [line(f"R{i}", 320, 100 + i * 15, width=150) for i in range(8)]

    blocks, _ = build_blocks(1, left + right, page_width=595)
    text = " ".join(b.text for b in blocks)

    assert text.index("L7") < text.index("R0"), f"columns interleaved: {text}"
    for i in range(7):
        assert text.index(f"L{i}") < text.index(f"L{i + 1}")


def test_a_table_becomes_one_atomic_block():
    lines = grid_lines(
        [["CML", "Measured", "Rate"], ["CML-12", "7.80", "0.12"], ["CML-19", "5.62", "0.24"]]
    )
    blocks, _ = build_blocks(1, lines, page_width=595)
    tables = [b for b in blocks if b.is_table]
    assert len(tables) == 1
    assert "CML-19" in tables[0].text and "CML-12" in tables[0].text


# --- headings and sections --------------------------------------------------


def test_a_larger_bold_line_is_a_heading():
    assert is_heading(line("2. Observations", 50, 100, size=13, bold=True), body_size=10, median_height=10)


def test_a_full_sentence_is_not_a_heading():
    candidate = line(
        "The vessel is fit for continued service at the current conditions.",
        50, 100, size=10,
    )
    assert not is_heading(candidate, body_size=10, median_height=10)


def test_a_heading_sets_the_section_for_the_blocks_that_follow():
    lines = [
        line("2. OBSERVATIONS AND FINDINGS", 50, 100, width=300, size=12, bold=True),
        line("Corrosion under insulation was confirmed at the N1 nozzle.", 50, 130, width=400, size=10),
    ]
    blocks, section = build_blocks(4, lines, page_width=595)
    assert section == "2. OBSERVATIONS AND FINDINGS"
    assert blocks[-1].section == "2. OBSERVATIONS AND FINDINGS"
    assert all(b.page == 4 for b in blocks)


def test_a_section_carries_across_a_page_break():
    blocks, section = build_blocks(
        5, [line("continued text on the next page", 50, 100, width=300)],
        page_width=595, section="3. Thickness Measurement Results",
    )
    assert blocks[0].section == "3. Thickness Measurement Results"
    assert section == "3. Thickness Measurement Results"


# --- low confidence ---------------------------------------------------------


def test_low_confidence_text_is_flagged_in_band_not_dropped():
    lines = [line("PSV-21O3 set pressure 12.5 barg", 50, 100, width=300, conf=0.35)]
    blocks, _ = build_blocks(2, lines, page_width=595)
    assert blocks[0].low_conf
    assert "low-confidence" in blocks[0].text
    assert "PSV-21O3" in blocks[0].text, "flagged text must still be present"


def test_confident_text_is_not_flagged():
    blocks, _ = build_blocks(
        2, [line("PSV-2103 set pressure 12.5 barg", 50, 100, width=300, conf=0.98)],
        page_width=595,
    )
    assert not blocks[0].low_conf
    assert "low-confidence" not in blocks[0].text


# --- egress guard -----------------------------------------------------------


def test_the_guard_blocks_a_public_address():
    import netguard

    netguard.install()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(netguard.EgressBlocked):
            sock.connect(("1.1.1.1", 443))
    finally:
        sock.close()


def test_the_guard_allows_loopback_and_private_addresses():
    import netguard

    netguard.install()
    # Qdrant and llama-server live on loopback or a private compose network;
    # blocking those would break the service rather than protect it.
    assert netguard._is_local("127.0.0.1")
    assert netguard._is_local("172.18.0.4")
    assert netguard._is_local("192.168.1.11")
    assert netguard._is_local("localhost")
    assert not netguard._is_local("142.250.183.14")


def test_blocked_attempts_are_counted_for_health_reporting():
    import netguard

    netguard.install()
    before = netguard.status()["blocked"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(netguard.EgressBlocked):
            sock.connect(("8.8.8.8", 53))
    finally:
        sock.close()
    assert netguard.status()["blocked"] == before + 1
