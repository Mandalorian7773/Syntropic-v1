"""Layout reconstruction: reading order, tables, sections. Owner: person 2.

This is the stage that decides whether retrieval looks broken. Refinery SOPs
and inspection reports are mostly tables, and a table flattened into a stream
of words -- "Pump P-101A 2024-03-11 Vibration 7.1 mm/s Alert" -- retrieves
badly and reads worse. So tables are detected, kept as markdown, and marked
atomic so chunk.py will never cut one in half.

Three signals produce a table:

1.  PyMuPDF's ruling-line finder, on native pages. Authoritative when it fires.
2.  Morphological line detection on the thresholded scan, for ruled tables that
    arrived as pixels.
3.  Column alignment across consecutive rows, for the borderless tables that
    make up most of the forms in a real document set.

Low-confidence OCR is flagged, never dropped and never silently emitted. A
human reading a citation needs to know the machine was unsure; a chunk that
quietly contains "P-1O1A" instead of "P-101A" is worse than one that says so.
"""

from __future__ import annotations

import re
import statistics

import cv2
import numpy as np

import ragconfig as cfg

from .model import Block, Line

# A heading is short, does not end like a sentence, and stands out somehow.
MAX_HEADING_CHARS = 90
NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z]|[IVXLC]+)[.)]?\s+\S")
SECTION_STOPWORDS = {"page", "continued", "contd"}

# Column clustering tolerance in points. Two cells whose left edges are within
# this are the same column; 6 pt is about one character width at 10 pt type.
COLUMN_TOL = 6.0
LOW_CONF_MARK = "[?]"


# --- generic helpers --------------------------------------------------------


def median_line_height(lines: list[Line]) -> float:
    heights = [ln.height for ln in lines if ln.height > 0]
    return statistics.median(heights) if heights else 10.0


def body_font_size(lines: list[Line]) -> float:
    """Modal font size, used as the baseline a heading has to beat."""
    sizes = [round(ln.size, 1) for ln in lines if ln.size > 0]
    if not sizes:
        return 0.0
    try:
        return statistics.mode(sizes)
    except statistics.StatisticsError:
        return statistics.median(sizes)


def group_rows(lines: list[Line], tol: float) -> list[list[Line]]:
    """Cluster lines into visual rows by vertical overlap.

    Two fragments belong to the same row when their vertical midpoints are
    within `tol`. Working from midpoints rather than tops survives the common
    case of a tall cell beside a short one.
    """
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda ln: (ln.mid_y, ln.x0)):
        if rows and abs(line.mid_y - rows[-1][0].mid_y) <= tol:
            rows[-1].append(line)
        else:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda ln: ln.x0)
    return rows


def detect_columns(lines: list[Line], page_width: float) -> list[list[Line]]:
    """Split a two-column page. Returns one list per column, in reading order.

    Looks for a vertical band in the middle of the page that no line crosses
    and that has text on both sides. Anything more elaborate (n-column, nested
    frames) is not worth the failure modes: refinery paperwork is single or
    double column, and a wrong split scrambles the text far worse than no split.
    """
    if len(lines) < 12:
        return [lines]

    band_start, band_end = page_width * 0.40, page_width * 0.60
    crossing = [ln for ln in lines if ln.x0 < band_start and ln.x1 > band_end]
    if crossing:
        return [lines]

    left = [ln for ln in lines if ln.x1 <= band_end]
    right = [ln for ln in lines if ln.x0 >= band_start]
    if len(left) < 5 or len(right) < 5:
        return [lines]
    # Both halves must carry real text, not a margin note against a body column.
    if min(len(left), len(right)) < 0.25 * len(lines):
        return [lines]
    return [left, right]


# --- table construction -----------------------------------------------------


def rows_to_markdown(rows: list[list[str]]) -> str:
    """Render extracted cells as a markdown table.

    The first row becomes the header. That is right nearly always for refinery
    tables, and when it is wrong the cost is a mislabelled header rather than
    lost content -- the cells are all still there and still aligned.
    """
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [list(r) + [""] * (width - len(r)) for r in rows]

    def cell(value: str) -> str:
        return (value or "").replace("|", r"\|").replace("\n", " ").strip()

    header = [cell(c) or f"Col {i + 1}" for i, c in enumerate(padded[0])]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for row in padded[1:]:
        out.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(out)


def _column_edges(rows: list[list[Line]]) -> list[float]:
    """Cluster the left edges of every cell into column positions."""
    edges = sorted(ln.x0 for row in rows for ln in row)
    if not edges:
        return []
    clusters: list[list[float]] = [[edges[0]]]
    for value in edges[1:]:
        if value - clusters[-1][-1] <= COLUMN_TOL:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    # A column that appears in only one row is a stray, not a column.
    return [statistics.mean(c) for c in clusters if len(c) >= 2]


def rows_to_grid(rows: list[list[Line]]) -> list[list[str]]:
    """Assign each line to a column and return a rectangular cell grid."""
    edges = _column_edges(rows)
    if len(edges) < 2:
        return [[" ".join(ln.text for ln in row)] for row in rows]

    grid: list[list[str]] = []
    for row in rows:
        cells = [""] * len(edges)
        for line in row:
            index = min(
                range(len(edges)), key=lambda i: abs(edges[i] - line.x0)
            )
            cells[index] = (cells[index] + " " + line.text).strip()
        grid.append(cells)
    return grid


def detect_ruled_tables(binary: np.ndarray, scale: float) -> list[tuple[float, float, float, float]]:
    """Find ruled table regions in a thresholded scan, in points.

    Morphological opening with a long horizontal kernel keeps only horizontal
    rules; the same with a tall vertical kernel keeps verticals. Where both
    survive in the same region, there is a grid.
    """
    if binary is None or binary.size == 0:
        return []
    height, width = binary.shape[:2]
    ink = cv2.bitwise_not(binary)  # rules are dark on a light page

    h_len = max(20, width // 25)
    v_len = max(20, height // 25)
    horizontal = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1)),
    )
    vertical = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len)),
    )
    grid = cv2.dilate(cv2.bitwise_or(horizontal, vertical),
                      cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))

    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    min_w, min_h = width * 0.25, height * 0.03
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < min_w or h < min_h:
            continue
        regions.append((x / scale, y / scale, (x + w) / scale, (y + h) / scale))
    return regions


def detect_aligned_tables(rows: list[list[Line]]) -> list[tuple[int, int]]:
    """Find borderless tables as runs of consistently-aligned rows.

    Returns inclusive (start, end) row-index spans. Most forms in a real
    document set have no ruling lines at all, so without this the detector
    finds nothing on exactly the pages that need it most.
    """
    tabular = [i for i, row in enumerate(rows) if len(row) >= 3]
    if not tabular:
        return []

    spans: list[tuple[int, int]] = []
    run_start = tabular[0]
    previous = tabular[0]
    for index in tabular[1:]:
        if index == previous + 1:
            previous = index
            continue
        if previous - run_start >= 1:
            spans.append((run_start, previous))
        run_start, previous = index, index
    if previous - run_start >= 1:
        spans.append((run_start, previous))

    # Require the run to share column positions, or a stack of ordinary
    # sentences that happen to contain wide gaps becomes a "table".
    confirmed = []
    for start, end in spans:
        block_rows = rows[start : end + 1]
        if len(_column_edges(block_rows)) >= 3:
            confirmed.append((start, end))
    return confirmed


# --- headings and sections --------------------------------------------------


def is_heading(line: Line, body_size: float, median_height: float) -> bool:
    text = line.text.strip()
    if not text or len(text) > MAX_HEADING_CHARS:
        return False
    if text.lower().split()[0] in SECTION_STOPWORDS:
        return False
    if text.endswith((".", ",", ";", ":")) and not NUMBERED_HEADING.match(text):
        return False

    bigger = body_size > 0 and line.size >= body_size * 1.15
    taller = body_size == 0 and line.height >= median_height * 1.25
    letters = [c for c in text if c.isalpha()]
    shouting = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85
    return bool(bigger or taller or line.bold or shouting or NUMBERED_HEADING.match(text))


# --- the entry point --------------------------------------------------------


def build_blocks(
    page_no: int,
    lines: list[Line],
    page_width: float,
    *,
    native_tables: list[tuple[tuple[float, float, float, float], list[list[str]]]] | None = None,
    ruled_regions: list[tuple[float, float, float, float]] | None = None,
    section: str = "",
) -> tuple[list[Block], str]:
    """Turn positioned lines into ordered blocks. Returns (blocks, section).

    `section` comes in as the section carried over from the previous page and
    goes out as whatever the last heading on this page set it to, so a table
    that continues across a page break keeps its heading.
    """
    if not lines and not native_tables:
        return [], section

    native_tables = native_tables or []
    ruled_regions = ruled_regions or []
    median_height = median_line_height(lines)
    body_size = body_font_size(lines)
    row_tol = max(3.0, median_height * 0.6)

    # Lines inside a known table region are consumed by that table.
    table_boxes = [box for box, _ in native_tables] + list(ruled_regions)

    def inside_table(line: Line) -> bool:
        cx, cy = (line.x0 + line.x1) / 2.0, line.mid_y
        return any(
            box[0] - 2 <= cx <= box[2] + 2 and box[1] - 2 <= cy <= box[3] + 2
            for box in table_boxes
        )

    free_lines = [ln for ln in lines if not inside_table(ln)]

    columns = detect_columns(free_lines, page_width)
    # Left edge of each detected column, used to place tables into one of them.
    column_starts = [min((ln.x0 for ln in col), default=0.0) for col in columns]

    def column_of(x_centre: float) -> int:
        """Which column an item at this x belongs to."""
        if len(column_starts) < 2:
            return 0
        return max(
            (i for i, start in enumerate(column_starts) if x_centre >= start - 2),
            default=0,
        )

    # Items are (column, y, kind, payload). Sorting by column first and then by
    # y is what reading order *means* on a two-column page: sorting by y alone
    # would interleave the two columns line by line and produce text no human
    # or model could follow.
    items: list[tuple[int, float, str, object]] = []

    for box, cells in native_tables:
        items.append(
            (column_of((box[0] + box[2]) / 2), box[1], "table", (rows_to_markdown(cells), 1.0))
        )

    for box in ruled_regions:
        contained = [
            ln
            for ln in lines
            if box[0] - 2 <= (ln.x0 + ln.x1) / 2 <= box[2] + 2
            and box[1] - 2 <= ln.mid_y <= box[3] + 2
        ]
        if len(contained) < 4:
            continue
        rows = group_rows(contained, row_tol)
        conf = _weighted_conf(contained)
        items.append(
            (
                column_of((box[0] + box[2]) / 2),
                box[1],
                "table",
                (rows_to_markdown(rows_to_grid(rows)), conf),
            )
        )

    for ordinal, column in enumerate(columns):
        rows = group_rows(column, row_tol)
        aligned = detect_aligned_tables(rows)
        consumed: set[int] = set()
        for start, end in aligned:
            span = rows[start : end + 1]
            flat = [ln for row in span for ln in row]
            items.append(
                (
                    ordinal,
                    span[0][0].y0,
                    "table",
                    (rows_to_markdown(rows_to_grid(span)), _weighted_conf(flat)),
                )
            )
            consumed.update(range(start, end + 1))

        # Whatever is left is prose. Paragraph breaks come from vertical gaps.
        paragraph: list[Line] = []
        previous_bottom: float | None = None
        for index, row in enumerate(rows):
            if index in consumed:
                _flush_paragraph(items, paragraph, ordinal)
                paragraph = []
                previous_bottom = None
                continue
            row_text = " ".join(ln.text for ln in row)
            gap = None if previous_bottom is None else row[0].y0 - previous_bottom

            if len(row) == 1 and is_heading(row[0], body_size, median_height):
                _flush_paragraph(items, paragraph, ordinal)
                paragraph = []
                items.append((ordinal, row[0].y0, "heading", (row_text, row[0].conf)))
            else:
                if gap is not None and gap > median_height * 1.6:
                    _flush_paragraph(items, paragraph, ordinal)
                    paragraph = []
                paragraph.extend(row)
            previous_bottom = max(ln.y1 for ln in row)
        _flush_paragraph(items, paragraph, ordinal)

    items.sort(key=lambda item: (item[0], item[1]))

    blocks: list[Block] = []
    for _column, _y, kind, payload in items:
        text, conf = payload  # type: ignore[misc]
        if not str(text).strip():
            continue
        low = conf < cfg.OCR_LOW_CONF
        if kind == "heading":
            section = str(text).strip()
        blocks.append(
            Block(
                kind=kind,
                text=_mark_low_conf(str(text), low),
                page=page_no,
                section=section,
                conf=float(conf),
                low_conf=low,
            )
        )
    return blocks, section


def _weighted_conf(lines: list[Line]) -> float:
    chars = sum(len(ln.text) for ln in lines)
    if not chars:
        return 1.0
    return sum(ln.conf * len(ln.text) for ln in lines) / chars


def _flush_paragraph(items: list, paragraph: list[Line], column: int = 0) -> None:
    if not paragraph:
        return
    text = " ".join(ln.text for ln in paragraph).strip()
    if text:
        items.append(
            (column, paragraph[0].y0, "paragraph", (text, _weighted_conf(paragraph)))
        )


def _mark_low_conf(text: str, low: bool) -> str:
    """Flag doubtful text in-band so a human reading the citation can see it.

    Deliberately visible rather than a metadata-only flag: the chunk text is
    what reaches the model and what the frontend renders in a citation, and
    both need to know.
    """
    if not low:
        return text
    return f"{LOW_CONF_MARK} low-confidence OCR, verify against the source page:\n{text}"
