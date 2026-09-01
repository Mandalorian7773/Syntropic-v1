"""Agent loop against a scripted fake LLM. The four rules under test:
grammar-mode requests, loop detection, retry cap, persist-before-yield."""

import asyncio
import json

import pytest

from agent.loop import MAX_STEPS, MAX_TOOL_RETRIES, AgentLoop
from llm.client import ParsedToolCall, Response


class FakeLLM:
    """Plays back a script of Response objects; records what it was asked."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def generate(self, messages, **kwargs):
        self.requests.append({"messages": messages, **kwargs})
        return self.script.pop(0)


def tool_call(name, args):
    return Response(
        text="", raw=json.dumps({"tool": name, "args": args}),
        tool_calls=[ParsedToolCall(call_id=f"c{name}{len(args)}", name=name, args=args)],
        is_final=False,
    )


def final(text):
    return Response(text=text, raw=json.dumps({"final": text}), is_final=True)


def make_loop(llm, registry, store, audit, tmp_path):
    return AgentLoop(llm, registry, store, audit,
                     workspace_dir=str(tmp_path / "workspace"),
                     artifacts_dir=str(tmp_path / "artifacts"))


async def collect(loop, session="s1", message="do the thing"):
    events = []
    async for event in loop.run(session, message, "model-x", context_tokens=16384):
        events.append(event)
    return events


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "workspace").mkdir()
    return tmp_path


async def test_tool_then_final(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        tool_call("write_file", {"path": "out.txt", "content": "42"}),
        final("Wrote the answer."),
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    types = [e.type for e in events]
    assert types[0] == "agent.step"
    assert "tool.call" in types and "tool.result" in types
    assert types[-1] == "done"
    assert events[-1].stop_reason == "final_answer"
    assert (ws / "workspace" / "out.txt").read_text() == "42"
    # Every request went out in grammar mode.
    assert all(r["grammar"] for r in llm.requests)
    assert all(r["expect_json"] for r in llm.requests)


async def test_loop_detection_aborts(ws, registry, store, audit):
    store.ensure_session("s1")
    same = {"path": "x.txt"}
    llm = FakeLLM([
        tool_call("read_file", same),
        tool_call("read_file", same),   # identical, consecutively -> abort
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    errors = [e for e in events if e.type == "error"]
    assert errors and errors[0].code == "LOOP_DETECTED"
    assert events[-1].stop_reason == "error"


async def test_retry_cap_honest_stop(ws, registry, store, audit):
    store.ensure_session("s1")
    # Three DIFFERENT failing calls to the same tool: not a loop, but a streak.
    llm = FakeLLM([
        tool_call("read_file", {"path": f"missing-{i}.txt"})
        for i in range(MAX_TOOL_RETRIES)
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    errors = [e for e in events if e.type == "error"]
    assert errors and errors[-1].code == "TOOL_RETRIES_EXCEEDED"
    assert events[-1].stop_reason == "error"


async def test_max_steps_bound(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        tool_call("write_file", {"path": f"f{i}.txt", "content": str(i)})
        for i in range(MAX_STEPS)
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert events[-1].stop_reason == "max_steps"
    assert events[-1].steps_used == MAX_STEPS


async def test_malformed_gets_nudge_then_recovers(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        Response(text="chatter", raw="not json at all", is_final=True, malformed=True),
        final("Recovered."),
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert events[-1].stop_reason == "final_answer"
    # The nudge message reached the model on the second request.
    assert "not valid tool-call JSON" in llm.requests[1]["messages"][-1]["content"]
    # And the incident is in the audit trail.
    kinds = [r["kind"] for r in audit.trail("s1")]
    assert "llm.malformed" in kinds


async def test_cancellation_between_steps(ws, registry, store, audit):
    store.ensure_session("s1")
    cancel = asyncio.Event()
    cancel.set()
    loop = make_loop(FakeLLM([]), registry, store, audit, ws)
    events = []
    async for event in loop.run("s1", "hi", "model-x", 16384, cancel):
        events.append(event)
    assert len(events) == 1 and events[0].stop_reason == "cancelled"


async def test_every_event_audited_before_yield(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        tool_call("write_file", {"path": "a.txt", "content": "1"}),
        final("done"),
    ])
    loop = make_loop(llm, registry, store, audit, ws)
    async for event in loop.run("s1", "go", "model-x", 16384):
        # At the moment an event is yielded, it is already in the trail.
        kinds = [r["kind"] for r in audit.trail("s1")]
        assert event.type in kinds


async def test_tool_calls_persisted_with_results(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        tool_call("write_file", {"path": "a.txt", "content": "1"}),
        final("done"),
    ])
    await collect(make_loop(llm, registry, store, audit, ws))
    rows = store._read("SELECT * FROM tool_calls WHERE session_id='s1'")
    assert len(rows) == 1
    assert rows[0]["name"] == "write_file" and rows[0]["ok"] == 1
