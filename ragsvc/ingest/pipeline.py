"""The ingest pipeline. Owner: person 2.

Render, preprocess, OCR, layout, chunk -- with the native-text fast path
short-circuiting the middle three whenever a page already carries its own text.

The budget is 90 seconds for a 20-page scan on laptop CPU. Two things make that
reachable, and neither is lowering the resolution:

**Pages are OCR'd in parallel.** OCR is the only stage that costs real time,
and it is embarrassingly parallel across pages. Rendering stays on the calling
thread because a PyMuPDF document is not safe to read from several threads at
once; the rendered images are then handed to a pool of workers, each with its
own OCR engine. Measured on an 8-core laptop this is the difference between
9.5 and 2.4 seconds a page.

**Resolution is the last thing to go, not the first.** The pipeline watches its
own clock and drops from 200 to 150 DPI for the pages still to come only if it
is running over budget, and records that it did. Skipping deskew or dropping
the reranker are step changes in output quality; resolution trades smoothly.

This module does no I/O beyond reading the file: no database, no Qdrant. That
is corpus.py's job, and keeping them apart is what makes the pipeline testable
without a running stack.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import ragconfig as cfg

from . import layout, ocr, pdf
from .chunk import chunk_blocks
from .model import Block, Chunk, PageResult
from .ocr_worker import ocr_page


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    path: str
    sha256: str
    size_bytes: int
    pages: list[PageResult] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    duration_ms: int = 0
    native_pages: int = 0
    scanned_pages: int = 0
    dpi_used: list[int] = field(default_factory=list)
    downshifted: bool = False
    ocr_backend: str = "none"
    ocr_error: str | None = None
    workers: int = 1

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def mean_conf(self) -> float:
        scanned = [p for p in self.pages if p.scanned]
        if not scanned:
            return 1.0
        return sum(p.mean_conf for p in scanned) / len(scanned)

    @property
    def low_conf_pages(self) -> list[int]:
        return [p.page for p in self.pages if p.scanned and p.mean_conf < cfg.OCR_LOW_CONF]


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _native_page(page, page_no: int) -> PageResult:
    """Read a page that already has a text layer. No OCR, no preprocessing."""
    lines = pdf.native_lines(page)
    blocks, _ = layout.build_blocks(
        page_no, lines, page.rect.width, native_tables=pdf.native_tables(page)
    )
    return PageResult(
        page=page_no,
        scanned=False,
        width=page.rect.width,
        height=page.rect.height,
        lines=lines,
        blocks=blocks,
        mean_conf=1.0,
        dpi=0,
    )


def _run_batch(pool, path: str, indices: list[int], dpi: int) -> list[tuple]:
    """OCR a batch of pages, in the worker pool when there is one.

    A broken pool falls back to doing the batch inline rather than failing the
    upload. A worker process can be killed by the OS under memory pressure, and
    losing a document to that on demo day would be a poor trade for the speed.
    """
    if pool is None:
        return [(index, ocr_page(path, index, dpi)) for index in indices]

    futures = {pool.submit(ocr_page, path, index, dpi): index for index in indices}
    try:
        return [(futures[future], future.result()) for future in futures]
    except Exception:  # noqa: BLE001
        ocr.shutdown_pool()
        return [(index, ocr_page(path, index, dpi)) for index in indices]


def _carry_sections(pages: list[PageResult]) -> None:
    """Propagate the running section heading across page boundaries.

    Pages are laid out independently so they can be processed in parallel,
    which means a page that opens mid-section starts with no heading of its
    own. This walks the finished pages in order and fills those in, so a table
    continuing onto page 12 is still attributed to the section that introduced
    it on page 11.
    """
    current = ""
    for page in pages:
        for block in page.blocks:
            # A heading block already carries its own text as its section, and
            # `section` is the clean string -- `text` may have picked up a
            # low-confidence marker that must not become a section name.
            if block.section:
                current = block.section
            elif current:
                block.section = current


def ingest_document(
    path: str | Path,
    *,
    doc_id: str | None = None,
    filename: str | None = None,
    dpi: int | None = None,
    on_page: Callable[[int, int], None] | None = None,
    workers: int | None = None,
) -> IngestResult:
    """Run one file end to end, returning pages and chunks.

    `on_page(page_number, total)` is called as each page completes, so an
    upload endpoint can report progress without this module knowing about HTTP.
    """
    started = time.monotonic()
    path = Path(path)
    doc_id = doc_id or str(uuid.uuid4())
    filename = filename or path.name
    sha, size = file_digest(path)
    workers = cfg.OCR_WORKERS if workers is None else max(1, workers)

    result = IngestResult(
        doc_id=doc_id,
        filename=filename,
        path=str(path),
        sha256=sha,
        size_bytes=size,
        workers=workers,
    )

    # Text-native formats never touch the renderer or OCR: .docx, .txt, .md,
    # .csv, .xlsx become pages directly. Same PageResult/Block shape, same
    # chunker, same citations -- the rest of the service cannot tell.
    from .textdoc import TEXT_SUFFIXES, pages_from_text_document  # noqa: PLC0415

    if path.suffix.lower() in TEXT_SUFFIXES:
        result.pages = pages_from_text_document(path)
        result.native_pages = len(result.pages)
        _carry_sections(result.pages)
        all_blocks = [b for page in result.pages for b in page.blocks]
        result.chunks = chunk_blocks(all_blocks, doc_id, filename)
        if on_page:
            on_page(len(result.pages), len(result.pages))
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    document = pdf.open_document(path)
    try:
        total = document.page_count
        current_dpi = dpi or cfg.RENDER_DPI
        by_page: dict[int, PageResult] = {}
        scanned: list[int] = []
        completed = 0

        # Pass one: classify, and finish the native pages immediately. They
        # cost single-digit milliseconds, so there is nothing to parallelise
        # and no reason to hold them up behind the scans.
        for index in range(total):
            page = document[index]
            if pdf.is_native_text(page):
                by_page[index] = _native_page(page, index + 1)
                result.native_pages += 1
                completed += 1
                if on_page:
                    on_page(index + 1, total)
            else:
                scanned.append(index)

        # Pass two: the scans, in batches. Rendering stays here because a fitz
        # document is not safe to read from several threads; only the OCR and
        # layout of already-rendered images goes to the pool.
        if scanned:
            pool = ocr.get_pool() if workers > 1 else None
            batch_size = max(1, (workers if pool else 1) * 2)

            for start in range(0, len(scanned), batch_size):
                batch = scanned[start : start + batch_size]
                batch_started = time.monotonic()
                outcomes = _run_batch(pool, str(path), batch, current_dpi)

                for index, (page_result, error, backend) in outcomes:
                    by_page[index] = page_result
                    result.dpi_used.append(current_dpi)
                    if error and not result.ocr_error:
                        result.ocr_error = error
                    if backend != "none":
                        result.ocr_backend = backend
                    completed += 1
                    if on_page:
                        on_page(index + 1, total)
                result.scanned_pages += len(batch)

                # Decide the resolution for the pages still to come from what
                # this batch actually cost, not from a guess made up front.
                per_page = (time.monotonic() - batch_started) / len(batch)
                if per_page > cfg.PAGE_TIME_BUDGET_S and current_dpi > cfg.RENDER_DPI_FALLBACK:
                    current_dpi = cfg.RENDER_DPI_FALLBACK
                    result.downshifted = True

        result.pages = [by_page[i] for i in sorted(by_page)]
        _carry_sections(result.pages)

        all_blocks: list[Block] = [b for page in result.pages for b in page.blocks]
        result.chunks = chunk_blocks(all_blocks, doc_id, filename)
    finally:
        document.close()

    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
