"""SQLite persistence for documents, chunks, pages and artifacts. Owner: person 2.

This service shares one database file with the backend (`DB_PATH`), but not one
schema file. `backend/db/schema.sql` is Person 3's, and CODEOWNERS says so, so
ragsvc creates and migrates only its own four tables here, idempotently, on
every start. Two processes, one file, no coordination problem: SQLite in WAL
mode handles a writer and readers, and ragsvc is the only writer to these
tables.

Why chunk text lives here and not only in Qdrant: the BM25 index is in-process
and has to be rebuilt from something at startup, `read_document` needs page
text that was never chunked, and a corrupted Qdrant volume should cost a
reindex, not a re-OCR. Qdrant holds vectors; SQLite holds the truth.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import ragconfig as cfg

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    pages        INTEGER NOT NULL DEFAULT 0,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT,
    scanned      INTEGER NOT NULL DEFAULT 0,
    indexed      INTEGER NOT NULL DEFAULT 0,
    ingest_ms    INTEGER NOT NULL DEFAULT 0,
    ingested_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_pages (
    doc_id   TEXT NOT NULL,
    page     INTEGER NOT NULL,
    text     TEXT NOT NULL,
    scanned  INTEGER NOT NULL DEFAULT 0,
    mean_conf REAL,
    PRIMARY KEY (doc_id, page)
);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    filename    TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    has_table   INTEGER NOT NULL DEFAULT 0,
    low_conf    INTEGER NOT NULL DEFAULT 0,
    tokens      INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_by_doc ON chunks (doc_id, chunk_index);

-- rag_artifacts, not artifacts. backend/db/schema.sql creates its own
-- `artifacts` table in this same database file, with different columns and a
-- NOT NULL foreign key to sessions. Both use CREATE TABLE IF NOT EXISTS, so
-- whichever service starts first wins and the other writes against a shape it
-- does not expect -- silently, until an INSERT hits a missing column. Person 3
-- owns `artifacts` because it backs /api/artifacts; this table is ragsvc's own
-- record of what it generated, including the template and title that their
-- schema has no room for.
CREATE TABLE IF NOT EXISTS rag_artifacts (
    artifact_id TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL,
    mime        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    template    TEXT,
    title       TEXT,
    session_id  TEXT,
    created_at  INTEGER NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """One connection per thread. FastAPI runs sync endpoints on a threadpool."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cfg.DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init() -> None:
    """Create ragsvc's tables. Safe to call on every start."""
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


# --- documents --------------------------------------------------------------


def upsert_document(
    doc_id: str,
    filename: str,
    path: str,
    pages: int,
    chunk_count: int,
    size_bytes: int,
    sha256: str,
    scanned: bool,
    indexed: bool,
    ingest_ms: int,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO documents
                (id, filename, path, pages, chunk_count, size_bytes, sha256,
                 scanned, indexed, ingest_ms, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filename=excluded.filename, path=excluded.path,
                pages=excluded.pages, chunk_count=excluded.chunk_count,
                size_bytes=excluded.size_bytes, sha256=excluded.sha256,
                scanned=excluded.scanned, indexed=excluded.indexed,
                ingest_ms=excluded.ingest_ms, ingested_at=excluded.ingested_at
            """,
            (
                doc_id,
                filename,
                path,
                pages,
                chunk_count,
                size_bytes,
                sha256,
                int(scanned),
                int(indexed),
                ingest_ms,
                int(time.time()),
            ),
        )


def get_document(doc_id: str) -> dict[str, Any] | None:
    row = connect().execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def find_document_by_name(name: str) -> dict[str, Any] | None:
    """Resolve a filename to a document.

    The agent passes whatever string the model produced, and a 7B model will
    happily pass "inspection report" or the filename without its extension.
    Exact id, then exact filename, then a prefix match, then a contains match.
    """
    conn = connect()
    for sql, param in (
        ("SELECT * FROM documents WHERE id = ?", name),
        ("SELECT * FROM documents WHERE filename = ?", name),
        ("SELECT * FROM documents WHERE filename LIKE ? ORDER BY ingested_at DESC", f"{name}%"),
        ("SELECT * FROM documents WHERE filename LIKE ? ORDER BY ingested_at DESC", f"%{name}%"),
    ):
        row = conn.execute(sql, (param,)).fetchone()
        if row:
            return dict(row)
    return None


def list_documents() -> list[dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM documents ORDER BY ingested_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_pages WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# --- pages ------------------------------------------------------------------


def replace_pages(doc_id: str, pages: list[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM doc_pages WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO doc_pages (doc_id, page, text, scanned, mean_conf) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    doc_id,
                    p["page"],
                    p["text"],
                    int(p.get("scanned", False)),
                    p.get("mean_conf"),
                )
                for p in pages
            ],
        )


def get_pages(doc_id: str, pages: list[int] | None = None) -> list[dict[str, Any]]:
    conn = connect()
    if pages:
        marks = ",".join("?" for _ in pages)
        rows = conn.execute(
            f"SELECT * FROM doc_pages WHERE doc_id = ? AND page IN ({marks}) "
            f"ORDER BY page",
            (doc_id, *pages),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM doc_pages WHERE doc_id = ? ORDER BY page", (doc_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- chunks -----------------------------------------------------------------


def replace_chunks(doc_id: str, chunks: list[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """
            INSERT INTO chunks
                (id, doc_id, filename, chunk_index, page, page_end, section,
                 has_table, low_conf, tokens, text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["id"],
                    doc_id,
                    c["filename"],
                    c["chunk_index"],
                    c["page"],
                    c.get("page_end", c["page"]),
                    c.get("section", ""),
                    int(c.get("has_table", False)),
                    int(c.get("low_conf", False)),
                    c.get("tokens", 0),
                    c["text"],
                )
                for c in chunks
            ],
        )


def all_chunks() -> list[dict[str, Any]]:
    """Every chunk in the corpus, in a stable order. Feeds the BM25 rebuild."""
    rows = connect().execute(
        "SELECT * FROM chunks ORDER BY doc_id, chunk_index"
    ).fetchall()
    return [dict(r) for r in rows]


def get_chunks(ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = connect().execute(
        f"SELECT * FROM chunks WHERE id IN ({marks})", tuple(ids)
    ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def count_chunks() -> int:
    return int(connect().execute("SELECT COUNT(*) FROM chunks").fetchone()[0])


# --- artifacts --------------------------------------------------------------


def insert_artifact(
    artifact_id: str,
    filename: str,
    path: str,
    mime: str,
    size_bytes: int,
    template: str | None,
    title: str | None,
    session_id: str | None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO rag_artifacts
                (artifact_id, filename, path, mime, size_bytes, template, title,
                 session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                filename,
                path,
                mime,
                size_bytes,
                template,
                title,
                session_id,
                int(time.time()),
            ),
        )


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    row = connect().execute(
        "SELECT * FROM rag_artifacts WHERE artifact_id = ?", (artifact_id,)
    ).fetchone()
    return dict(row) if row else None


def list_artifacts() -> list[dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM rag_artifacts ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]
