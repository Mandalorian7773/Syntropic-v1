"""Shared ingest data types. Owner: person 2.

One vocabulary for the whole pipeline: pdf.py and ocr.py both produce `Line`,
layout.py turns lines into `Block`s, chunk.py turns blocks into `Chunk`s. Every
stage after OCR is identical for a scanned page and a native one, which is the
point -- the only place the two paths differ is where the lines came from.

Coordinates are always PDF points (1/72"), never pixels, even when the line
came out of a 200 DPI render. Converting once at the OCR boundary means layout
analysis never has to know what DPI produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Line:
    """One line of text with its position on the page."""

    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in points
    conf: float = 1.0  # 1.0 for native text; the OCR score for scanned
    size: float = 0.0  # font size in points, 0 when unknown (OCR)
    bold: bool = False

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def mid_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0


@dataclass
class Block:
    """A run of text with one role on the page.

    `kind` is "heading", "paragraph" or "table". Tables carry markdown in
    `text` and are atomic from here on: chunk.py will start a new chunk rather
    than split one, because half a table retrieves as well as no table.
    """

    kind: str
    text: str
    page: int
    section: str = ""
    conf: float = 1.0
    low_conf: bool = False

    @property
    def is_table(self) -> bool:
        return self.kind == "table"


@dataclass
class PageResult:
    """Everything ingest knows about one page."""

    page: int  # 1-based, matches what a citation shows a human
    scanned: bool
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    mean_conf: float = 1.0
    dpi: int = 0  # 0 for native pages, the render DPI for scanned ones

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    @property
    def low_conf(self) -> bool:
        return any(b.low_conf for b in self.blocks)


@dataclass
class Chunk:
    """A retrievable unit. Every field here ends up in a citation."""

    id: str
    doc_id: str
    filename: str
    chunk_index: int
    page: int
    page_end: int
    section: str
    text: str
    tokens: int
    has_table: bool = False
    low_conf: bool = False

    def to_payload(self) -> dict:
        """The Qdrant payload. Matches the collection spec in the build brief."""
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "page": self.page,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "section": self.section,
        }
