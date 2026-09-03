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


def _table_markdown(rows: list[list[str]]) -> str:
    rows = [[(c or "").strip().replace("|", "\\|").replace("\n", " ") for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


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
    md = _table_markdown(rows)
    return [Block(kind="table", text=md, page=0, section=path.stem)] if md else []


def _blocks_xlsx(path: Path) -> list[Block]:
    import openpyxl  # noqa: PLC0415  already a ragsvc dependency

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    blocks: list[Block] = []
    for ws in wb.worksheets:
        rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
        md = _table_markdown(rows)
        if md:
            blocks.append(Block(kind="heading", text=ws.title, page=0, section=ws.title))
            blocks.append(Block(kind="table", text=md, page=0, section=ws.title))
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
