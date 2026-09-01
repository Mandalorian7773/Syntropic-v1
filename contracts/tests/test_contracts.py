"""The one check the contracts package earns.

Everything here guards something that fails silently otherwise: an event shape
drifting from the documented frame, or a tool description long enough to make a
7B model pick the wrong tool.
"""

import pytest
from pydantic import BaseModel, TypeAdapter

from contracts import Event, RunContext, Tool, ToolResult, to_sse

# The exact frames from the build spec. If one of these stops validating, the
# frontend and backend have stopped agreeing about the wire format.
FRAMES = [
    {"type": "session.start", "session_id": "uuid", "ts": 1234567890},
    {"type": "router.decision", "model_id": "qwen2.5-vl-7b", "task_type": "document",
     "confidence": 0.91, "reason": "vision input present",
     "alternatives": ["qwen3-coder-8b"]},
    {"type": "model.loading", "model_id": "qwen3-coder-8b",
     "evicting": "qwen2.5-vl-7b", "eta_s": 9},
    {"type": "model.ready", "model_id": "qwen3-coder-8b", "load_ms": 8400,
     "vram_mb": 5100},
    {"type": "agent.step", "step": 2, "max_steps": 10},
    {"type": "token", "text": "partial output chunk"},
    {"type": "tool.call", "call_id": "c1", "name": "execute_python",
     "args": {"code": "print(1+1)"}},
    {"type": "tool.result", "call_id": "c1", "ok": True,
     "summary": "exit 0, stdout: 2", "duration_ms": 1240, "truncated": False},
    {"type": "citation", "doc_id": "d7", "filename": "SOP-014.pdf", "page": 4,
     "score": 0.87, "snippet": "max permissible wall loss is ..."},
    {"type": "artifact", "artifact_id": "a3", "filename": "approval-note.docx",
     "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     "size_bytes": 18422, "url": "/api/artifacts/a3"},
    {"type": "error", "code": "TOOL_TIMEOUT",
     "message": "execute_python exceeded 30s", "recoverable": True},
    {"type": "done", "stop_reason": "final_answer", "steps_used": 4,
     "tokens_in": 3120, "tokens_out": 880, "latency_ms": 18400},
]

adapter = TypeAdapter(Event)


@pytest.mark.parametrize("frame", FRAMES, ids=[f["type"] for f in FRAMES])
def test_documented_frame_roundtrips(frame):
    event = adapter.validate_python(frame)
    assert event.model_dump() == frame


def test_union_is_exactly_the_documented_types():
    # The build spec's prose says "eleven" but lists twelve frames; the frames
    # are the authority and all twelve are implemented. Adding a thirteenth is
    # a contract change: see CHANGE-PROTOCOL.md.
    assert len({f["type"] for f in FRAMES}) == 12


def test_unknown_event_type_is_rejected():
    with pytest.raises(Exception):
        adapter.validate_python({"type": "token.stream", "text": "x"})


def test_sse_frame_is_wellformed():
    frame = to_sse(adapter.validate_python(FRAMES[5]))
    assert frame.startswith("event: token\ndata: {")
    assert frame.endswith("\n\n")


class Args(BaseModel):
    code: str


def test_tool_schema_and_limits():
    class Good(Tool):
        name = "execute_python"
        description = "Run Python in the sandbox and return stdout."
        args_model = Args

        def run(self, args, ctx):
            return ToolResult(ok=True, content="", duration_ms=0)

    schema = Good().schema()
    assert schema["name"] == "execute_python"
    assert "code" in schema["parameters"]["properties"]

    with pytest.raises(ValueError, match="name must be"):
        class LongName(Tool):
            name = "x" * 25
            description = "fine"
            args_model = Args

            def run(self, args, ctx):  # pragma: no cover
                ...

    with pytest.raises(ValueError, match="description must be"):
        class LongDesc(Tool):
            name = "ok_tool"
            description = "d" * 121
            args_model = Args

            def run(self, args, ctx):  # pragma: no cover
                ...


def test_run_context_shape():
    ctx = RunContext(session_id="s", workspace_dir="/w", artifacts_dir="/a")
    assert ctx.model_dump() == {
        "session_id": "s", "workspace_dir": "/w", "artifacts_dir": "/a"
    }
