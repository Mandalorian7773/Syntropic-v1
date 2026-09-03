"""Pages from documents that are already text. Owner: person 3 (added).

The PDF/image path renders, OCRs and lays out. A .docx, .txt, .md, .csv or
.xlsx has no pixels to read -- its text is the document -- so it is turned
straight into the same PageResult/Block shape the chunker and read_document
already consume. Nothing downstream knows the difference: citations carry a
page number, tables stay atomic markdown blocks, sections carry forward.

"Page" for these formats is a size-bounded slice, not a print page: about
8000 characters, cut on a paragraph boundary, never inside a table. That is
what keeps read_document(pages=[n]) meaningful and inside the tool budget.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .model import Block, PageResult

TEXT_SUFFIXES = {".docx", ".txt", ".md", ".csv", ".xlsx"}
PAGE_CHARS = 8000
# A table block is atomic downstream (chunk.py never splits one), so a big
# sheet must be cut HERE, by rows, with the header repeated on every piece --
# otherwise the EIA refinery-capacity workbook (3,337 rows) became one block
# larger than the whole tool budget and unsearchable in practice. 40 rows of a
# wide sheet is a few hundred tokens: small enough to retrieve as one hit,
# big enough that a question about one refinery lands inside a single piece.
TABLE_ROWS_PER_BLOCK = 40


def _table_markdown(rows: list[list[str]]) -> str:
    """One markdown table. For a long one prefer table_markdown_blocks()."""
    return "\n\n".join(table_markdown_blocks(rows, rows_per_block=None))


def table_markdown_blocks(rows: list[list[str]], rows_per_block: int | None = TABLE_ROWS_PER_BLOCK) -> list[str]:
    rows = [[(c or "").strip().replace("|", "\\|").replace("\n", " ") for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    header = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    if not body:
        return ["\n".join(header)]
    step = rows_per_block or len(body)
    blocks = []
    for start in range(0, len(body), step):
        piece = body[start:start + step]
        label = f"(rows {start + 1}-{start + len(piece)} of {len(body)})" if step < len(body) else ""
        lines = ([label] if label else []) + header + ["| " + " | ".join(r) + " |" for r in piece]
        blocks.append("\n".join(lines))
    return blocks


def _blocks_docx(path: Path) -> list[Block]:
    import docx  # noqa: PLC0415  python-docx, already a ragsvc dependency
    from docx.table import Table  # noqa: PLC0415
    from docx.text.paragraph import Paragraph  # noqa: PLC0415

    document = docx.Document(str(path))
    blocks: list[Block] = []
    section = ""
    # Walk body elements in order so tables land where they sit in the text.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, document)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style is not None else ""
            if style.startswith("heading") or style == "title":
                section = text
                blocks.append(Block(kind="heading", text=text, page=0, section=section))
            else:
                blocks.append(Block(kind="paragraph", text=text, page=0, section=section))
        elif tag == "tbl":
            table = Table(child, document)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            md = _table_markdown(rows)
            if md:
                blocks.append(Block(kind="table", text=md, page=0, section=section))
    return blocks


def _blocks_plain(path: Path, markdown: bool) -> list[Block]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[Block] = []
    section = ""
    for para in text.replace("\r\n", "\n").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if markdown and para.startswith("#"):
            section = para.lstrip("#").strip()
            blocks.append(Block(kind="heading", text=section, page=0, section=section))
        elif markdown and para.lstrip().startswith("|"):
            blocks.append(Block(kind="table", text=para, page=0, section=section))
        else:
            blocks.append(Block(kind="paragraph", text=para, page=0, section=section))
    return blocks


def _blocks_csv(path: Path) -> list[Block]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
    return [Block(kind="table", text=md, page=0, section=path.stem)
            for md in table_markdown_blocks(rows)]


def _blocks_xlsx(path: Path) -> list[Block]:
    import openpyxl  # noqa: PLC0415  already a ragsvc dependency

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    blocks: list[Block] = []
    for ws in wb.worksheets:
        rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
        pieces = table_markdown_blocks(rows)
        if pieces:
            blocks.append(Block(kind="heading", text=ws.title, page=0, section=ws.title))
            blocks += [Block(kind="table", text=md, page=0, section=ws.title) for md in pieces]
    return blocks


def blocks_for(path: Path) -> list[Block]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _blocks_docx(path)
    if suffix == ".csv":
        return _blocks_csv(path)
    if suffix == ".xlsx":
        return _blocks_xlsx(path)
    return _blocks_plain(path, markdown=(suffix == ".md"))


def paginate(blocks: list[Block]) -> list[PageResult]:
    """Group blocks into ~PAGE_CHARS pages; a table is never split."""
    pages: list[PageResult] = []
    current: list[Block] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if not current:
            return
        number = len(pages) + 1
        for b in current:
            b.page = number
        pages.append(PageResult(page=number, scanned=False, width=0.0, height=0.0,
                                blocks=current, mean_conf=1.0, dpi=0))
        current, size = [], 0

    for block in blocks:
        if current and size + len(block.text) > PAGE_CHARS:
            flush()
        current.append(block)
        size += len(block.text)
    flush()
    return pages


def pages_from_text_document(path: str | Path) -> list[PageResult]:
    path = Path(path)
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"not a text document: {path.suffix!r}")
    return paginate(blocks_for(path))
