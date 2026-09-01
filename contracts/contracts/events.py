"""Server-Sent Event contract for the SIH26117 workbench.

The eleven event types below are the ENTIRE vocabulary spoken between backend
and frontend. Adding, removing or renaming a field here is a breaking change
for both Person 1 and Person 3 -- see contracts/CHANGE-PROTOCOL.md.

Owner: shared (person1 + person2 + person3, all three must approve).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

TaskType = Literal["general", "code", "document", "vision", "data"]
StopReason = Literal["final_answer", "max_steps", "error", "cancelled"]


class SessionStart(BaseModel):
    type: Literal["session.start"] = "session.start"
    session_id: str
    ts: int


class RouterDecision(BaseModel):
    type: Literal["router.decision"] = "router.decision"
    model_id: str
    task_type: TaskType
    confidence: float
    reason: str
    alternatives: list[str] = Field(default_factory=list)


class ModelLoading(BaseModel):
    type: Literal["model.loading"] = "model.loading"
    model_id: str
    evicting: str | None = None
    eta_s: int


class ModelReady(BaseModel):
    type: Literal["model.ready"] = "model.ready"
    model_id: str
    load_ms: int
    vram_mb: int


class AgentStep(BaseModel):
    type: Literal["agent.step"] = "agent.step"
    step: int
    max_steps: int


class Token(BaseModel):
    type: Literal["token"] = "token"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool.call"] = "tool.call"
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    type: Literal["tool.result"] = "tool.result"
    call_id: str
    ok: bool
    summary: str
    duration_ms: int
    truncated: bool = False


class Citation(BaseModel):
    type: Literal["citation"] = "citation"
    doc_id: str
    filename: str
    page: int
    score: float
    snippet: str


class Artifact(BaseModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: str
    filename: str
    mime: str
    size_bytes: int
    url: str


class AgentError(BaseModel):
    # Named AgentError, not Error: the generated TypeScript would otherwise
    # shadow the DOM `Error` global in every file that imports it.
    type: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool


class Done(BaseModel):
    type: Literal["done"] = "done"
    stop_reason: StopReason
    steps_used: int
    tokens_in: int
    tokens_out: int
    latency_ms: int


Event = Annotated[
    Union[
        SessionStart,
        RouterDecision,
        ModelLoading,
        ModelReady,
        AgentStep,
        Token,
        ToolCall,
        ToolResultEvent,
        Citation,
        Artifact,
        AgentError,
        Done,
    ],
    Field(discriminator="type"),
]


class EventEnvelope(BaseModel):
    """Wrapper that exists only so the union gets a name in the JSON Schema.

    The frontend consumes `Event` from the generated events.ts; this envelope is
    what makes the discriminated union addressable by json-schema-to-typescript.
    """

    event: Event


def to_sse(event: BaseModel) -> str:
    """Serialize any contract event into a single valid SSE frame."""
    return f"event: {getattr(event, 'type', 'message')}\ndata: {event.model_dump_json()}\n\n"


__all__ = [
    "TaskType",
    "StopReason",
    "SessionStart",
    "RouterDecision",
    "ModelLoading",
    "ModelReady",
    "AgentStep",
    "Token",
    "ToolCall",
    "ToolResultEvent",
    "Citation",
    "Artifact",
    "AgentError",
    "Done",
    "Event",
    "EventEnvelope",
    "to_sse",
]
