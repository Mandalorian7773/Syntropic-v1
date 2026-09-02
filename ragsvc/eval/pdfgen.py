"""A small PDF writer for building the evaluation corpus. Owner: person 2.

Not part of the service. This exists so `make_corpus.py` can lay out documents
that look like refinery paperwork *and report which page every fact landed on*,
which is the only way to get an evaluation set whose ground truth is not a
guess. Recall@5 is meaningless if the "correct page" was assigned by eye.

Text is wrapped and flowed manually rather than through `insert_textbox`
because the page a block lands on is the thing being measured, and that means
the writer has to own pagination rather than hand it to PyMuPDF.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

A4 = (595.0, 842.0)
MARGIN_X = 56.0
MARGIN_TOP = 64.0
MARGIN_BOTTOM = 64.0

FONT_BODY = "helv"
FONT_BOLD = "hebo"
FONT_ITALIC = "heit"
FONT_MONO = "cour"


def wrap(text: str, width: float, fontname: str, fontsize: float) -> list[str]:
    """Greedy word wrap using the real glyph metrics."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if fitz.get_text_length(candidate, fontname=fontname, fontsize=fontsize) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


@dataclass
class PdfWriter:
    """Flows blocks onto A4 pages and records where each tagged block landed."""

    title: str
    org: str = "MANGALORE REFINERY AND PETROCHEMICALS LIMITED"
    unit: str = "Inspection and Reliability Department"
    doc_no: str = ""
    fact_pages: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.document = fitz.open()
        self.page = None
        self.y = 0.0
        self._new_page()

    # --- page management ----------------------------------------------------

    @property
    def content_width(self) -> float:
        return A4[0] - 2 * MARGIN_X

    def _new_page(self) -> None:
        self.page = self.document.new_page(width=A4[0], height=A4[1])
        self.y = MARGIN_TOP
        self._page_furniture()

    def _page_furniture(self) -> None:
        number = self.document.page_count
        self.page.insert_text(
            (MARGIN_X, 36), self.org, fontname=FONT_BOLD, fontsize=8.5, color=(0.12, 0.22, 0.39)
        )
        self.page.insert_text(
            (MARGIN_X, 47), self.unit, fontname=FONT_BODY, fontsize=7.5, color=(0.35, 0.35, 0.35)
        )
        if self.doc_no:
            width = fitz.get_text_length(self.doc_no, fontname=FONT_BODY, fontsize=7.5)
            self.page.insert_text(
                (A4[0] - MARGIN_X - width, 36), self.doc_no,
                fontname=FONT_BODY, fontsize=7.5, color=(0.35, 0.35, 0.35),
            )
        self.page.draw_line(
            fitz.Point(MARGIN_X, 52), fitz.Point(A4[0] - MARGIN_X, 52),
            color=(0.12, 0.22, 0.39), width=0.8,
        )
        footer = f"Page {number} | {self.doc_no or self.title}"
        self.page.insert_text(
            (MARGIN_X, A4[1] - 40), footer,
            fontname=FONT_BODY, fontsize=7, color=(0.45, 0.45, 0.45),
        )
        self.page.draw_line(
            fitz.Point(MARGIN_X, A4[1] - 50), fitz.Point(A4[0] - MARGIN_X, A4[1] - 50),
            color=(0.7, 0.7, 0.7), width=0.5,
        )

    def _ensure(self, height: float) -> None:
        if self.y + height > A4[1] - MARGIN_BOTTOM:
            self._new_page()

    def _tag(self, fact: str | None) -> None:
        if fact:
            self.fact_pages[fact] = self.document.page_count

    # --- blocks -------------------------------------------------------------

    def add_title(self, text: str, subtitle: str = "") -> None:
        self._ensure(48)
        width = fitz.get_text_length(text, fontname=FONT_BOLD, fontsize=14)
        self.page.insert_text(
            ((A4[0] - width) / 2, self.y + 12), text, fontname=FONT_BOLD, fontsize=14
        )
        self.y += 22
        if subtitle:
            width = fitz.get_text_length(subtitle, fontname=FONT_BODY, fontsize=9)
            self.page.insert_text(
                ((A4[0] - width) / 2, self.y + 6), subtitle,
                fontname=FONT_BODY, fontsize=9, color=(0.3, 0.3, 0.3),
            )
            self.y += 16
        self.page.draw_line(
            fitz.Point(MARGIN_X, self.y), fitz.Point(A4[0] - MARGIN_X, self.y),
            color=(0.12, 0.22, 0.39), width=1.2,
        )
        self.y += 14

    def add_heading(self, text: str, fact: str | None = None) -> None:
        self._ensure(30)
        self.y += 8
        self.page.insert_text(
            (MARGIN_X, self.y + 9), text, fontname=FONT_BOLD, fontsize=10.5,
            color=(0.12, 0.22, 0.39),
        )
        self._tag(fact)
        self.y += 18

    def add_paragraph(self, text: str, fact: str | None = None, size: float = 9.5) -> None:
        lines = wrap(text, self.content_width, FONT_BODY, size)
        leading = size * 1.45
        self._ensure(leading * min(len(lines), 3))
        self._tag(fact)
        for line in lines:
            self._ensure(leading)
            self.page.insert_text(
                (MARGIN_X, self.y + size), line, fontname=FONT_BODY, fontsize=size
            )
            self.y += leading
        self.y += 4

    def add_key_values(self, pairs: list[tuple[str, str]], fact: str | None = None) -> None:
        """Two label/value columns, as every refinery form header is laid out."""
        self._tag(fact)
        column = self.content_width / 2
        for index in range(0, len(pairs), 2):
            self._ensure(16)
            for offset, (label, value) in enumerate(pairs[index : index + 2]):
                x = MARGIN_X + offset * column
                self.page.insert_text(
                    (x, self.y + 9), f"{label}:", fontname=FONT_BOLD, fontsize=8.5
                )
                label_width = fitz.get_text_length(
                    f"{label}: ", fontname=FONT_BOLD, fontsize=8.5
                )
                self.page.insert_text(
                    (x + label_width + 2, self.y + 9), value,
                    fontname=FONT_BODY, fontsize=8.5,
                )
            self.y += 15
        self.y += 4

    def add_table(
        self,
        columns: list[str],
        rows: list[list[str]],
        fact: str | None = None,
        row_facts: dict[int, str] | None = None,
        widths: list[float] | None = None,
    ) -> None:
        """A ruled table with a shaded header, repeated if it spans pages."""
        size = 8.0
        row_height = 16.0
        count = len(columns)
        widths = widths or [1.0 / count] * count
        pixel_widths = [w * self.content_width for w in widths]

        def draw_header() -> None:
            self.page.draw_rect(
                fitz.Rect(MARGIN_X, self.y, A4[0] - MARGIN_X, self.y + row_height),
                color=(0.5, 0.5, 0.5), fill=(0.85, 0.85, 0.85), width=0.6,
            )
            x = MARGIN_X
            for index, name in enumerate(columns):
                self.page.insert_text(
                    (x + 3, self.y + 11), name, fontname=FONT_BOLD, fontsize=size
                )
                x += pixel_widths[index]
                if index < count - 1:
                    self.page.draw_line(
                        fitz.Point(x, self.y), fitz.Point(x, self.y + row_height),
                        color=(0.5, 0.5, 0.5), width=0.6,
                    )
            self.y += row_height

        self._ensure(row_height * 3)
        self._tag(fact)
        draw_header()

        for row_index, row in enumerate(rows):
            if self.y + row_height > A4[1] - MARGIN_BOTTOM:
                self._new_page()
                draw_header()
            if row_facts and row_index in row_facts:
                self.fact_pages[row_facts[row_index]] = self.document.page_count

            self.page.draw_rect(
                fitz.Rect(MARGIN_X, self.y, A4[0] - MARGIN_X, self.y + row_height),
                color=(0.6, 0.6, 0.6), width=0.5,
            )
            x = MARGIN_X
            for index in range(count):
                value = str(row[index]) if index < len(row) else ""
                available = pixel_widths[index] - 6
                while (
                    fitz.get_text_length(value, fontname=FONT_BODY, fontsize=size) > available
                    and len(value) > 4
                ):
                    value = value[:-2]
                self.page.insert_text(
                    (x + 3, self.y + 11), value, fontname=FONT_BODY, fontsize=size
                )
                x += pixel_widths[index]
                if index < count - 1:
                    self.page.draw_line(
                        fitz.Point(x, self.y), fitz.Point(x, self.y + row_height),
                        color=(0.6, 0.6, 0.6), width=0.5,
                    )
            self.y += row_height
        self.y += 8

    def add_signature_block(self, roles: list[str]) -> None:
        self._ensure(70)
        self.y += 18
        column = self.content_width / len(roles)
        for index, role in enumerate(roles):
            x = MARGIN_X + index * column
            self.page.draw_line(
                fitz.Point(x, self.y + 26), fitz.Point(x + column - 20, self.y + 26),
                color=(0.3, 0.3, 0.3), width=0.6,
            )
            self.page.insert_text(
                (x, self.y + 38), role, fontname=FONT_BOLD, fontsize=8
            )
        self.y += 52

    def add_spacer(self, height: float = 10.0) -> None:
        self._ensure(height)
        self.y += height

    # --- output -------------------------------------------------------------

    def to_bytes(self) -> bytes:
        return self.document.tobytes()

    def save(self, path: Path) -> dict[str, int]:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.document.save(str(path))
        return dict(self.fact_pages)


def scan_effect(pdf_bytes: bytes, seed: int = 0, dpi: int = 200) -> bytes:
    """Turn a digital PDF into one that looks photocopied.

    Rotation, blur, sensor noise and JPEG artefacts, in that order, because
    that is the order a real scan acquires them. The result is an image-only
    PDF with no text layer at all, so the ingest pipeline has no choice but to
    OCR it -- which is the point. A corpus of clean digital PDFs would let a
    broken OCR path pass every test in this repo.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    source = fitz.open("pdf", pdf_bytes)
    output = fitz.open()

    for page in source:
        pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, 3
        )[:, :, ::-1].copy()

        angle = float(rng.uniform(-0.9, 0.9))
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        image = cv2.warpAffine(
            image, matrix, (width, height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )

        image = cv2.GaussianBlur(image, (3, 3), 0.6)
        noise = rng.normal(0, 6.5, image.shape)
        # A photocopier lifts the black point and drops the white point.
        image = np.clip(image.astype(np.float32) * 0.94 + 8 + noise, 0, 255).astype(np.uint8)

        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if not ok:
            raise RuntimeError("failed to encode scanned page")

        new_page = output.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=io.BytesIO(encoded.tobytes()))

    data = output.tobytes()
    source.close()
    output.close()
    return data
