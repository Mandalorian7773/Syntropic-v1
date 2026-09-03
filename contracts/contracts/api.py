"""REST request/response models for every endpoint the workbench exposes.

One table, three consumers: backend serves it, ragsvc serves a subset, the
frontend generates its client types from it. Nobody redefines these locally.

Owner: shared (person1 + person2 + person3, all three must approve).
"""

from __future__ import annotations

from typing import Any

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
    model_id: str | None = Field(
        default=None,
        description=(
            "Run this turn on a specific model, overriding the router. None "
            "means route as before, so an existing client is unaffected."
        ),
    )


# --- POST /api/chat/cancel ----------------------------------------------------


class CancelRequest(BaseModel):
    session_id: str


class CancelResponse(BaseModel):
    ok: bool


# --- GET /api/models ----------------------------------------------------------
# Returns a BARE ARRAY of ModelInfo, not an envelope. Same for /api/sessions and
# /api/documents below -- the endpoint table in the build prompts is explicit
# about this, and FastAPI serves it with response_model=list[ModelInfo].


class ModelInfo(BaseModel):
    id: str
    capabilities: list[str] = Field(default_factory=list)
    context: int
    vram_mb: int
    loaded: bool
    # A picker needs something a human can read. `id` is a filename-shaped
    # slug; nobody choosing a model wants to compare "qwen2.5-vl-7b" against
    # "qwen2.5-coder-7b" on a stage. Both default to "" so a producer that has
    # not been updated still validates.
    display_name: str = Field(
        default="", description="Human-readable name for the picker."
    )
    description: str = Field(
        default="", description="One line on what this model is good for."
    )


# --- GET /api/sessions and /api/sessions/{id} ---------------------------------


class SessionSummary(BaseModel):
    """One row of GET /api/sessions (bare array of these)."""

    id: str
    title: str
    created_at: int
    message_count: int


class Message(BaseModel):
    role: str  # "user" | "assistant" | "tool"
    content: str
    ts: int


class SessionStep(BaseModel):
    """A replayed agent step, for rehydrating the trace panel on session load."""

    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    summary: str
    duration_ms: int


class SessionDetail(BaseModel):
    """GET /api/sessions/{id}."""

    id: str
    messages: list[Message] = Field(default_factory=list)
    steps: list[SessionStep] = Field(default_factory=list)
    model_id: str | None = Field(
        default=None,
        description=(
            "The model this session is pinned to, set when a user picked one. "
            "None means the router chooses per turn, which is the default."
        ),
    )


# --- Documents ----------------------------------------------------------------


class UploadResponse(BaseModel):
    """POST /api/documents/upload (multipart)."""

    file_id: str
    filename: str
    pages: int
    status: str  # "queued" | "ingesting" | "indexed" | "failed"


class DocumentInfo(BaseModel):
    """One row of GET /api/documents (bare array of these)."""

    doc_id: str
    filename: str
    pages: int
    chunks: int
    ingested_at: int
    status: str = "indexed"
    size_bytes: int = 0


class ReindexResponse(BaseModel):
    doc_id: str
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
    "SessionSummary",
    "Message",
    "SessionStep",
    "SessionDetail",
    "UploadResponse",
    "DocumentInfo",
    "ReindexResponse",
    "SearchRequest",
    "SearchHit",
    "SearchResponse",
    "ArtifactInfo",
    "NetworkStatus",
    "HealthResponse",
]
