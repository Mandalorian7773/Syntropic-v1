"""Out-of-process page OCR. Owner: person 2.

This module exists because of a measurement, not a preference.

OCR is 88% of ingest time, and almost all of that is recognition. Recognition
runs at the same speed on one onnxruntime thread as on eight -- the CRNN's LSTM
kernel is single-threaded -- which reads like an invitation to parallelise
across pages. It is not, in threads: measured on six pages, a six-thread pool
was **slower** than doing them one at a time (0.49x "speedup"). The recogniser
spends its time in Python holding the GIL, so threads serialise and add
contention on top.

Processes have no such problem. Each worker renders, cleans, OCRs and lays out
one page and returns a finished `PageResult`, which is a few kilobytes of
dataclasses. The page image never crosses the process boundary -- the worker is
given a path, a page index and a DPI, and renders it itself, because rendering
costs 74 ms and pickling an 11 MB array costs more than that.

The models are small (about 16 MB of ONNX for all three), so N workers is not
N times the memory of a language model; it is N times a rounding error.
"""

from __future__ import annotations

from pathlib import Path

from .model import PageResult

# One open document per worker process, so a 200-page file is not reopened 200
# times. Keyed by path: a worker only ever sees one document at a time in
# practice, but a dict costs nothing and makes that not matter.
_documents: dict[str, object] = {}


def init_worker() -> None:
    """Run once in each worker process, before the first page.

    Pinning OpenCV to a single thread is not a courtesy to the other workers,
    it is faster outright: measured 7.6 s/page against 10.6 s/page with
    OpenCV's default pool, even with no other work on the machine. Its internal
    threading on the small images OCR crops is pure overhead.
    """
    import cv2  # noqa: PLC0415

    cv2.setNumThreads(1)

    from . import ocr  # noqa: PLC0415

    try:
        ocr.get_engine()  # pay the model load here, not on the first page
    except Exception:  # noqa: BLE001 - reported per page instead
        pass


def _document(path: str):
    document = _documents.get(path)
    if document is None:
        from . import pdf  # noqa: PLC0415

        document = pdf.open_document(Path(path))
        _documents[path] = document
    return document


def ocr_page(path: str, page_index: int, dpi: int) -> tuple[PageResult, str | None, str]:
    """Render, clean, OCR and lay out one page.

    Returns (result, error, backend). The backend name is returned rather than
    read from the parent because the parent process never loads an OCR engine
    when the pool is doing the work.
    """
    from . import layout, ocr, pdf, preprocess  # noqa: PLC0415

    document = _document(path)
    page = document[page_index]
    width, height = page.rect.width, page.rect.height

    image = pdf.render_page(page, dpi)
    scale = pdf.scale_for(page, dpi)
    ocr_input, report = preprocess.prepare(image)

    error = None
    try:
        lines = ocr.read_page(ocr_input, scale)
    except ocr.OcrUnavailable as exc:
        # A page nobody can read is recorded as unread, not silently empty.
        # Failing the whole upload would lose the pages that did read.
        error = str(exc)
        lines = []

    ruled = layout.detect_ruled_tables(report.get("binary"), scale)
    blocks, _ = layout.build_blocks(page_index + 1, lines, width, ruled_regions=ruled)

    return (
        PageResult(
            page=page_index + 1,
            scanned=True,
            width=width,
            height=height,
            lines=lines,
            blocks=blocks,
            mean_conf=ocr.mean_confidence(lines),
            dpi=dpi,
        ),
        error,
        ocr.backend_name(),
    )
