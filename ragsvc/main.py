"""RAG service. Owner: person 2.

Documents in, citations and deliverables out, on port 8001. The backend proxies
these paths under /api/*, so the SPA only ever talks to one origin and no CORS
configuration exists to get wrong on stage.

    GET    /health                     liveness, index state, missing weights
    POST   /documents/upload           multipart file -> full ingest pipeline
    GET    /documents                  what has been ingested
    GET    /documents/{id}             extracted text, optionally by page
    DELETE /documents/{id}             drop from SQLite, Qdrant and BM25
    POST   /documents/{id}/reindex     re-run ingest, keeping the id
    POST   /search                     hybrid retrieval, every hit cited
    POST   /artifacts/docx|xlsx        generate a deliverable
    GET    /artifacts                  list generated deliverables
    GET    /artifacts/{id}             download one
    GET    /tools                      tool schemas for Person 3's registry
    POST   /tools/{name}               run a tool over HTTP

`ragconfig` is imported first, on purpose: it pins the offline environment
variables before any model library has a chance to read them.
"""

from __future__ import annotations

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# ragsvc/ ahead of everything else on sys.path. backend/ installs a top-level
# package called `tools` as well, and whichever is found first wins -- which
# must be this one when the process is ragsvc.
_HERE = str(Path(__file__).resolve().parent)
if sys.path and sys.path[0] != _HERE:
    sys.path.insert(0, _HERE)

import ragconfig as cfg  # noqa: E402  - must precede onnxruntime/tokenizers

import netguard  # noqa: E402
import ragdb  # noqa: E402
from fastapi import FastAPI, File, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import corpus  # noqa: E402
import tools as ragtools  # noqa: E402
from contracts import (  # noqa: E402
    ArtifactInfo,
    DocumentInfo,
    ReindexResponse,
    RunContext,
    SearchHit,
    SearchRequest,
    SearchResponse,
    ToolResult,
    UploadResponse,
)
from docgen import Section, Sheet, UnknownTemplate, available_templates, create_docx, create_xlsx
from index.search import search as run_search

STARTED_AT = int(time.time())
_startup_report: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_report
    _startup_report = corpus.startup()
    yield


app = FastAPI(title="SIH26117 ragsvc", version="0.1.0", lifespan=lifespan)


def _document_info(row: dict) -> DocumentInfo:
    """Map a ragsvc document row onto the shared contract shape.

    The backend proxies /documents straight through to the SPA, so this is the
    object the Documents panel renders. `status` drives its badge: it polls
    while anything is not yet "indexed", so a document that failed to index has
    to say "failed" rather than sit at "indexed" with zero chunks.
    """
    return DocumentInfo(
        doc_id=row["id"],
        filename=row["filename"],
        pages=row["pages"],
        chunks=row["chunk_count"],
        ingested_at=row["ingested_at"],
        status="indexed" if row["indexed"] else "failed",
        size_bytes=row["size_bytes"],
    )


# --- health -----------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    """Liveness plus everything that could be quietly wrong before a demo.

    Missing weights are reported here rather than discovered on the first
    query. A model that is not on disk is a five-minute fix an hour before the
    demo and an unrecoverable failure during it.
    """
    stats = corpus.stats()
    missing = cfg.missing_weights()
    return {
        "ok": True,
        "qdrant": stats["qdrant"],
        "documents": stats["documents"],
        "chunks": stats["chunks"],
        "vectors": stats["vectors"],
        "bm25_chunks": stats["bm25_chunks"],
        "collection": stats["collection"],
        "qdrant_mode": stats["qdrant_mode"],
        "missing_weights": missing,
        "degraded": bool(missing) or not stats["qdrant"],
        "egress": netguard.status(),
        "uptime_s": int(time.time()) - STARTED_AT,
        "startup": _startup_report,
    }


# --- documents --------------------------------------------------------------


@app.post("/documents/upload", response_model=UploadResponse)
def upload(file: UploadFile = File(...)) -> UploadResponse:
    """Accept a file and run the whole ingest pipeline synchronously.

    Synchronous on purpose: the frontend shows a spinner and the operator
    watches the page count climb. A background job would need a status endpoint,
    a polling loop and a failure path, for a corpus that is loaded once before
    the demo.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in corpus.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix or '(none)'}; "
            f"accepted: {', '.join(sorted(corpus.SUPPORTED_SUFFIXES))}",
        )
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")

    path = corpus.save_upload(data, file.filename or "upload.pdf")
    try:
        outcome = corpus.ingest_file(path, filename=path.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc

    return UploadResponse(
        file_id=outcome.doc_id,
        filename=outcome.filename,
        pages=outcome.pages,
        status="indexed" if outcome.indexed else "failed",
    )


@app.get("/documents", response_model=list[DocumentInfo])
def list_documents() -> list[DocumentInfo]:
    # A bare array, not an envelope. The contract is explicit about this and
    # the SPA's rest.ts types it as DocumentInfo[].
    return [_document_info(r) for r in ragdb.list_documents()]


@app.get("/documents/{doc_id}")
def get_document(
    doc_id: str, pages: str | None = Query(default=None, description="e.g. 1,3,5")
) -> dict:
    wanted = None
    if pages:
        try:
            wanted = [int(p) for p in pages.replace(" ", "").split(",") if p]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="pages must be integers") from exc
    try:
        document, page_rows = corpus.read_document(doc_id, wanted)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no document {doc_id!r}") from exc
    return {
        "document": _document_info(document).model_dump(),
        "pages": [
            {
                "page": row["page"],
                "text": row["text"],
                "scanned": bool(row["scanned"]),
                "mean_conf": row["mean_conf"],
            }
            for row in page_rows
        ],
    }


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict:
    if not corpus.delete_document(doc_id):
        raise HTTPException(status_code=404, detail=f"no document {doc_id!r}")
    return {"deleted": True, "id": doc_id}


@app.post("/documents/{doc_id}/reindex", response_model=ReindexResponse)
def reindex(doc_id: str) -> ReindexResponse:
    try:
        outcome = corpus.reindex(doc_id)
    except FileNotFoundError as exc:
        # The row is real, the file behind it is not. 410 rather than 404: the
        # difference matters to whoever is debugging, and an unhandled
        # FileNotFoundError here is a 500 with a traceback for what is an
        # ordinary, recoverable state.
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail=f"no document {doc_id!r}")
    # queued=False because reindexing is synchronous here: it has already
    # happened by the time this returns.
    return ReindexResponse(doc_id=outcome.doc_id, queued=False)


# --- search -----------------------------------------------------------------


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256)


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dim: int


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    """Dense BGE-M3 vectors for the gateway's router.

    The router's primary classifier is LogisticRegression over these vectors
    (architecture B4); it POSTs here at startup to train and per prompt to
    classify, and falls back to TF-IDF when this endpoint is missing. It WAS
    missing, so every routing decision was the fallback -- document questions
    landed at ~0.4 confidence, under the 0.6 threshold, and the router panel
    read "falling back to default model" through the whole document demo.
    Same embedder as indexing, so nothing new loads.
    """
    from index.embed import get_embedder  # noqa: PLC0415

    embedder = get_embedder()
    vectors = embedder.encode(req.texts)
    return EmbedResponse(vectors=[[float(x) for x in row] for row in vectors],
                         dim=int(embedder.dim))


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    result = run_search(req.query, top_k=req.top_k)
    return SearchResponse(
        hits=[
            SearchHit(
                doc_id=hit.doc_id,
                filename=hit.filename,
                page=hit.page,
                score=hit.score,
                # The same window the agent gets, not the head of the chunk.
                snippet=ragtools.focused_snippet(hit.text, req.query) or hit.snippet(),
            )
            for hit in result.hits
        ]
    )


# --- artifacts --------------------------------------------------------------


class DocxRequest(BaseModel):
    template: str = "approval_note"
    title: str
    sections: list[Section] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    session_id: str | None = None


class XlsxRequest(BaseModel):
    sheets: list[Sheet]
    title: str | None = None
    session_id: str | None = None


def _artifact_info(record) -> ArtifactInfo:
    return ArtifactInfo(
        artifact_id=record.artifact_id,
        filename=record.filename,
        mime=record.mime,
        size_bytes=record.size_bytes,
        url=f"/artifacts/{record.artifact_id}",
    )


@app.get("/artifacts/templates")
def artifact_templates() -> dict:
    """The templates create_docx accepts, with the slots each one exposes."""
    from docgen import load_template  # noqa: PLC0415

    out = []
    for name in available_templates():
        template = load_template(name)
        out.append(
            {
                "id": template.id,
                "title": template.title,
                "document_type": template.document_type,
                "sections": [
                    {
                        "key": section.key,
                        "heading": section.heading,
                        "required": section.required,
                        "kind": section.kind,
                        "columns": section.columns,
                        "hint": section.hint,
                    }
                    for section in template.sections
                ],
            }
        )
    return {"templates": out}


@app.post("/artifacts/docx", response_model=ArtifactInfo)
def artifact_docx(req: DocxRequest) -> ArtifactInfo:
    try:
        record = create_docx(
            req.template, req.title, req.sections, meta=req.meta, session_id=req.session_id
        )
    except UnknownTemplate as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _artifact_info(record)


@app.post("/artifacts/xlsx", response_model=ArtifactInfo)
def artifact_xlsx(req: XlsxRequest) -> ArtifactInfo:
    if not req.sheets:
        raise HTTPException(status_code=400, detail="at least one sheet is required")
    record = create_xlsx(req.sheets, title=req.title, session_id=req.session_id)
    return _artifact_info(record)


@app.get("/artifacts")
def list_artifacts() -> dict:
    return {
        "artifacts": [
            {
                "artifact_id": row["artifact_id"],
                "filename": row["filename"],
                "mime": row["mime"],
                "size_bytes": row["size_bytes"],
                "template": row["template"],
                "title": row["title"],
                "created_at": row["created_at"],
                "url": f"/artifacts/{row['artifact_id']}",
            }
            for row in ragdb.list_artifacts()
        ]
    }


@app.get("/artifacts/{artifact_id}")
def download_artifact(artifact_id: str) -> FileResponse:
    row = ragdb.get_artifact(artifact_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"no artifact {artifact_id!r}")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="artifact file is missing from disk")
    return FileResponse(path=str(path), media_type=row["mime"], filename=row["filename"])


# --- tools over HTTP --------------------------------------------------------


class ToolCallRequest(BaseModel):
    """What Person 3's registry posts to run one of these tools remotely.

    It sends `{"args": {...}, "session_id": ...}`. Both spellings are accepted
    because the alternative is a contract PR and a coordinated deploy to fix a
    single key name, and because getting this wrong fails quietly: an unknown
    field is dropped, the tool runs with no arguments, and the agent sees a
    validation error three layers from the cause.
    """

    args: dict = Field(default_factory=dict)
    arguments: dict = Field(default_factory=dict)
    session_id: str = "http"
    workspace_dir: str | None = None
    artifacts_dir: str | None = None

    @property
    def tool_args(self) -> dict:
        return self.args or self.arguments


@app.get("/tools")
def tool_schemas() -> dict:
    """Tool schemas, in the shape the model's tool list expects."""
    return {"tools": ragtools.schemas()}


@app.post("/tools/{name}", response_model=ToolResult)
def call_tool(name: str, req: ToolCallRequest) -> ToolResult:
    context = RunContext(
        session_id=req.session_id,
        workspace_dir=req.workspace_dir or str(cfg.WORKSPACE_DIR),
        artifacts_dir=req.artifacts_dir or str(cfg.ARTIFACTS_DIR),
    )
    return ragtools.run_tool(name, req.tool_args, context)
