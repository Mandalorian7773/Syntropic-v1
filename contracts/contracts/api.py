"""REST request/response models for every endpoint the workbench exposes.

One table, three consumers: backend serves it, ragsvc serves a subset, the
frontend generates its client types from it. Nobody redefines these locally.

Owner: shared (person1 + person2 + person3, all three must approve).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .events import TaskType

# --- POST /api/chat  (returns text/event-stream, not JSON) --------------------


class Attachment(BaseModel):
    filename: str
    mime: str
    size_bytes: int
    path: str | None = None  # server-side path once uploaded


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    attachments: list[Attachment] = Field(default_factory=list)


# --- POST /api/chat/cancel ----------------------------------------------------


class CancelRequest(BaseModel):
    session_id: str


class CancelResponse(BaseModel):
    cancelled: bool


# --- GET /api/models ----------------------------------------------------------


class ModelInfo(BaseModel):
    id: str
    capabilities: list[str] = Field(default_factory=list)
    context: int
    vram_mb: int
    loaded: bool


class ModelsResponse(BaseModel):
    models: list[ModelInfo] = Field(default_factory=list)


# --- GET /api/sessions and /api/sessions/{id} ---------------------------------


class SessionSummary(BaseModel):
    session_id: str
    title: str
    created_ts: int
    updated_ts: int
    message_count: int


class SessionsResponse(BaseModel):
    sessions: list[SessionSummary] = Field(default_factory=list)


class Message(BaseModel):
    role: str  # "user" | "assistant" | "tool"
    content: str
    ts: int


class SessionDetail(BaseModel):
    session_id: str
    title: str
    created_ts: int
    updated_ts: int
    task_type: TaskType | None = None
    messages: list[Message] = Field(default_factory=list)


# --- Documents ----------------------------------------------------------------


class DocumentInfo(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    size_bytes: int
    indexed: bool
    ingested_ts: int


class UploadResponse(BaseModel):
    document: DocumentInfo


class DocumentsResponse(BaseModel):
    documents: list[DocumentInfo] = Field(default_factory=list)


class ReindexResponse(BaseModel):
    id: str
    queued: bool


# --- POST /api/search ---------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SearchHit(BaseModel):
    doc_id: str
    filename: str
    page: int
    score: float
    snippet: str


class SearchResponse(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)


# --- GET /api/artifacts/{id}  (file download; metadata shape for listings) -----


class ArtifactInfo(BaseModel):
    artifact_id: str
    filename: str
    mime: str
    size_bytes: int
    url: str


# --- GET /api/network/status --------------------------------------------------


class NetworkStatus(BaseModel):
    external_packets: int
    dns_queries: int
    since: int  # unix ts the counters were reset
    rules_active: bool


# --- GET /api/health ----------------------------------------------------------


class HealthResponse(BaseModel):
    ok: bool
    model_loaded: str | None = None
    qdrant: bool
    vram_free_mb: int


__all__ = [
    "Attachment",
    "ChatRequest",
    "CancelRequest",
    "CancelResponse",
    "ModelInfo",
    "ModelsResponse",
    "SessionSummary",
    "SessionsResponse",
    "Message",
    "SessionDetail",
    "DocumentInfo",
    "UploadResponse",
    "DocumentsResponse",
    "ReindexResponse",
    "SearchRequest",
    "SearchHit",
    "SearchResponse",
    "ArtifactInfo",
    "NetworkStatus",
    "HealthResponse",
]
