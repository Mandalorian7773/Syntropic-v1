"""PDF page rendering and native-text extraction. Owner: person 2.

Two jobs, and the first one is the decision that makes the 90-second budget
reachable: **is this page already text?** Running OCR over a digitally
generated PDF is slower than reading it and worse at reading it -- the text
layer is exact, the OCR is a guess at a rasterisation of the exact thing. Most
refinery document sets are a mix, and the mix is where the time is won.

Scanned pages are rendered at 200 DPI, which is the floor for reliable
recognition of the 8-9 pt text in an equipment table. The caller may ask for
150 when the clock is tight; ingest/pipeline.py does exactly that.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # the fitz alias is the documented API name; the module moved
import numpy as np

import ragconfig as cfg

from .model import Line

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def open_document(path: str | Path) -> fitz.Document:
    """Open a PDF or a single image as a fitz document.

    fitz reads images natively, so a photographed page and a scanned PDF take
    the same path through the rest of the pipeline.
    """
    path = Path(path)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        # Wrap the image in a one-page PDF so page geometry exists downstream.
        image_doc = fitz.open(str(path))
        pdf_bytes = image_doc.convert_to_pdf()
        image_doc.close()
        return fitz.open("pdf", pdf_bytes)
    return fitz.open(str(path))


def is_native_text(page: fitz.Page) -> bool:
    """True when the page carries a usable text layer.

    The threshold is characters, not the presence of any text at all: a scanned
    page often carries a stray header stamp or a page number in real text, and
    treating that as "native" would skip OCR on a page that is 99% image.
    """
    return len(page.get_text("text").strip()) >= cfg.NATIVE_TEXT_MIN_CHARS


def native_lines(page: fitz.Page) -> list[Line]:
    """Extract text lines with position and font metrics from the text layer.

    Font size and boldness survive here and nowhere else -- they are the most
    reliable heading signal available, and layout.py uses them when present.
    """
    lines: list[Line] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 = text, 1 = image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            size = max((span.get("size", 0.0) for span in spans), default=0.0)
            # PyMuPDF packs style into a bitfield; bit 4 (16) is bold.
            bold = any(int(span.get("flags", 0)) & 2 ** 4 for span in spans)
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                Line(text=text, bbox=(x0, y0, x1, y1), conf=1.0, size=size, bold=bold)
            )
    lines.sort(key=lambda ln: (round(ln.y0, 1), ln.x0))
    return lines


def native_tables(page: fitz.Page) -> list[tuple[tuple[float, float, float, float], list[list[str]]]]:
    """Tables found by PyMuPDF's ruling-line detector, as (bbox, rows).

    Only available for native pages -- it works off vector line art, which a
    raster scan does not have. layout.py falls back to its own grid detection
    for scanned pages.
    """
    try:
        finder = page.find_tables()
    except Exception:  # noqa: BLE001 - table finding is best-effort by nature
        return []
    found = []
    for table in getattr(finder, "tables", []):
        try:
            rows = table.extract()
        except Exception:  # noqa: BLE001
            continue
        cleaned = [
            [(cell or "").replace("\n", " ").strip() for cell in row] for row in rows
        ]
        # A one-row or one-column "table" is almost always a misfire on a form
        # field or a page border. Dropping them costs nothing and avoids
        # emitting markdown pipes around ordinary paragraphs.
        if len(cleaned) < 2 or max((len(r) for r in cleaned), default=0) < 2:
            continue
        found.append((tuple(table.bbox), cleaned))
    return found


def render_page(page: fitz.Page, dpi: int) -> np.ndarray:
    """Rasterise a page to a BGR array ready for OpenCV."""
    pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, 3
    )
    return image[:, :, ::-1].copy()  # RGB -> BGR


def scale_for(page: fitz.Page, dpi: int) -> float:
    """Pixels per point at `dpi`. OCR boxes divide by this to become points."""
    _ = page
    return dpi / 72.0
