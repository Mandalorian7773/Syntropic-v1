"""Corpus orchestration: ingest, persist, index, remove. Owner: person 2.

The seam in this service is here. `ingest/pipeline.py` turns a file into pages
and chunks and touches nothing else, which is what makes it testable without a
stack. This module is the part that knows about SQLite, Qdrant and the BM25
index, and it is the only part that writes to any of them.

Ordering matters and is deliberate: SQLite first, vectors second. SQLite is the
truth -- it holds the chunk text the BM25 index rebuilds from and that
`read_document` serves. If the embedding step fails halfway, the document is
still searchable lexically and a reindex fixes the rest. If it were the other
way round, a Qdrant hit could point at a chunk whose text nobody has.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import ragconfig as cfg
import ragdb
from index import bm25, qdrant_store
from index.embed import WeightsMissing, get_embedder
from ingest.pipeline import IngestResult, ingest_document

SUPPORTED_SUFFIXES = {
    # rendered + OCR'd via ingest/pipeline.py
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
    # text-native via ingest/textdoc.py: no renderer, no OCR
    ".docx", ".txt", ".md", ".csv", ".xlsx",
}


@dataclass
class IngestOutcome:
    """What happened, in the terms the API and the demo script care about."""

    doc_id: str
    filename: str
    pages: int
    chunks: int
    indexed: bool
    duration_ms: int
    scanned_pages: int
    native_pages: int
    ocr_backend: str
    mean_conf: float
    low_conf_pages: list[int]
    downshifted: bool
    warnings: list[str]


def startup() -> dict:
    """Bring the service's state up. Safe to call more than once."""
    cfg.ensure_dirs()
    if cfg.NETGUARD:
        import netguard  # noqa: PLC0415

        netguard.install()
    ragdb.init()

    report = {"qdrant": False, "bm25_chunks": 0, "collection": cfg.COLLECTION}
    try:
        store = qdrant_store.get_store()
        store.ensure_collection()
        report["qdrant"] = True
        report["mode"] = store.mode
    except Exception as exc:  # noqa: BLE001 - a dead Qdrant must not stop boot
        report["qdrant_error"] = str(exc)
    report["bm25_chunks"] = bm25.rebuild()
    return report


def save_upload(data: bytes, filename: str) -> Path:
    """Write an uploaded file into the workspace under a safe name.

    `Path(filename).name` is the whole path-traversal defence and is enough:
    it discards every directory component, so "../../etc/passwd" becomes
    "passwd" and lands in the documents directory like anything else.
    """
    safe = Path(filename).name or f"upload-{uuid.uuid4().hex}.pdf"
    cfg.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    target = cfg.DOCUMENTS_DIR / safe
    if target.exists():
        target = cfg.DOCUMENTS_DIR / f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}"
    target.write_bytes(data)
    return target


def copy_into_workspace(path: Path) -> Path:
    """Copy a file that already exists on disk into the documents directory."""
    cfg.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    target = cfg.DOCUMENTS_DIR / path.name
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    return target


def ingest_file(
    path: str | Path,
    *,
    doc_id: str | None = None,
    filename: str | None = None,
    rebuild_sparse: bool = True,
) -> IngestOutcome:
    """Ingest one file and index it. Returns what the caller needs to report."""
    path = Path(path)
    warnings: list[str] = []

    result: IngestResult = ingest_document(path, doc_id=doc_id, filename=filename)
    if result.ocr_error:
        warnings.append(result.ocr_error)
    if result.downshifted:
        warnings.append(
            f"render dropped to {cfg.RENDER_DPI_FALLBACK} DPI part-way through to "
            f"stay inside the ingest time budget"
        )
    if result.low_conf_pages:
        warnings.append(
            "low OCR confidence on page(s) "
            + ", ".join(str(p) for p in result.low_conf_pages)
        )

    ragdb.replace_pages(
        result.doc_id,
        [
            {
                "page": page.page,
                "text": page.text,
                "scanned": page.scanned,
                "mean_conf": page.mean_conf,
            }
            for page in result.pages
        ],
    )
    ragdb.replace_chunks(
        result.doc_id,
        [
            {
                "id": chunk.id,
                "filename": chunk.filename,
                "chunk_index": chunk.chunk_index,
                "page": chunk.page,
                "page_end": chunk.page_end,
                "section": chunk.section,
                "has_table": chunk.has_table,
                "low_conf": chunk.low_conf,
                "tokens": chunk.tokens,
                "text": chunk.text,
            }
            for chunk in result.chunks
        ],
    )

    indexed = False
    if result.chunks:
        try:
            embedder = get_embedder()
            vectors = embedder.encode(
                [c.text for c in result.chunks], batch_size=cfg.EMBED_BATCH
            )
            store = qdrant_store.get_store()
            store.delete_document(result.doc_id)
            store.upsert(result.chunks, vectors)
            indexed = True
        except WeightsMissing as exc:
            warnings.append(f"dense index skipped: {exc}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"dense index failed: {exc}")

    ragdb.upsert_document(
        doc_id=result.doc_id,
        filename=result.filename,
        path=str(path),
        pages=result.page_count,
        chunk_count=len(result.chunks),
        size_bytes=result.size_bytes,
        sha256=result.sha256,
        scanned=result.scanned_pages > 0,
        indexed=indexed,
        ingest_ms=result.duration_ms,
    )

    if rebuild_sparse:
        bm25.rebuild()

    return IngestOutcome(
        doc_id=result.doc_id,
        filename=result.filename,
        pages=result.page_count,
        chunks=len(result.chunks),
        indexed=indexed,
        duration_ms=result.duration_ms,
        scanned_pages=result.scanned_pages,
        native_pages=result.native_pages,
        ocr_backend=result.ocr_backend,
        mean_conf=result.mean_conf,
        low_conf_pages=result.low_conf_pages,
        downshifted=result.downshifted,
        warnings=warnings,
    )


def ingest_directory(directory: str | Path, rebuild_sparse: bool = True) -> list[IngestOutcome]:
    """Ingest every supported file in a directory. Used to load the demo corpus."""
    directory = Path(directory)
    outcomes = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            outcomes.append(ingest_file(path, rebuild_sparse=False))
    if rebuild_sparse:
        bm25.rebuild()
    return outcomes


def delete_document(doc_id: str) -> bool:
    row = ragdb.get_document(doc_id)
    if not row:
        return False
    try:
        qdrant_store.get_store().delete_document(doc_id)
    except Exception:  # noqa: BLE001 - the SQLite delete still has to happen
        pass
    ragdb.delete_document(doc_id)
    bm25.rebuild()
    return True


def reindex(doc_id: str) -> IngestOutcome | None:
    """Re-run ingest for a document already on disk, keeping its id.

    Returns None when the id is unknown; raises FileNotFoundError when the row
    exists but the file behind it has gone. Those are different failures and
    the caller reports them differently -- a stale row after someone cleared
    the workspace is not the same as a typo in a document id.
    """
    row = ragdb.get_document(doc_id)
    if not row:
        return None
    source = Path(row["path"])
    if not source.exists():
        raise FileNotFoundError(
            f"{row['filename']} is still registered but its source file is gone "
            f"from {source}. Re-upload it, or delete the document."
        )
    return ingest_file(source, doc_id=doc_id, filename=row["filename"])


def read_document(doc_id: str, pages: list[int] | None = None) -> tuple[dict, list[dict]]:
    """Return (document row, page rows). Raises KeyError when unknown."""
    row = ragdb.get_document(doc_id) or ragdb.find_document_by_name(doc_id)
    if not row:
        raise KeyError(doc_id)
    return row, ragdb.get_pages(row["id"], pages)


def stats() -> dict:
    documents = ragdb.list_documents()
    try:
        store = qdrant_store.get_store()
        vectors = store.count()
        healthy = store.healthy()
        mode = store.mode
    except Exception:  # noqa: BLE001
        vectors, healthy, mode = 0, False, "unavailable"
    return {
        "documents": len(documents),
        "pages": sum(d["pages"] for d in documents),
        "chunks": ragdb.count_chunks(),
        "vectors": vectors,
        "bm25_chunks": bm25.get_index().size,
        "qdrant": healthy,
        "qdrant_mode": mode,
        "collection": cfg.COLLECTION,
        "updated": int(time.time()),
    }
