"""Agent loop against a scripted fake LLM. The four rules under test:
grammar-mode requests, loop detection, retry cap, persist-before-yield."""

import asyncio
import json

import pytest

from agent.loop import MAX_STEPS, MAX_TOOL_RETRIES, AgentLoop
from contracts import ToolResult
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
    """Repeating a call that SUCCEEDED is a loop: the answer is already there.

    list_files, not read_file, because a repeat of a *failed* call is the
    documented retry path and is exempt -- see the ok=False test below.
    """
    store.ensure_session("s1")
    same = {"path": "."}
    llm = FakeLLM([
        tool_call("list_files", same),
        tool_call("list_files", same),   # 1st repeat -> nudged, not fatal
        tool_call("list_files", same),   # 2nd repeat -> abort
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    errors = [e for e in events if e.type == "error"]
    assert errors and errors[0].code == "LOOP_DETECTED"
    assert events[-1].stop_reason == "error"


async def test_single_repeat_is_nudged_not_fatal(ws, registry, store, audit):
    """One repeated call must not end the run.

    At the temperature the loop actually samples at, the same context was
    measured emitting a duplicate call on one sample and the correct final
    answer on the next. Aborting on the first repeat threw away runs that had
    the answer already in context -- which is what killed the retrieval demo.
    """
    store.ensure_session("s1")
    same = {"path": "."}
    llm = FakeLLM([
        tool_call("list_files", same),
        tool_call("list_files", same),   # identical -> corrected, run continues
        final("The answer is 12.5 barg."),
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert [e for e in events if e.type == "error"] == []
    assert events[-1].stop_reason == "final_answer"
    # The correction is fed back as a message, so the model can see why.
    assert any("Do not repeat it" in r["messages"][-1]["content"]
               for r in llm.requests if r["messages"])


async def test_repeat_of_failed_call_is_not_a_loop(ws, registry, store, audit):
    """A failed call may be retried verbatim; only MAX_TOOL_RETRIES bounds it.

    This is the case that made a dead Docker daemon look like a wedged model:
    execute_python failed every time, the model reissued it, and the run was
    reported as LOOP_DETECTED rather than as the tool being unavailable.
    """
    store.ensure_session("s1")
    same = {"path": "does-not-exist.txt"}    # read_file fails -> ok=False
    llm = FakeLLM([
        tool_call("read_file", same),
        tool_call("read_file", same),
        final("Gave up on the file and answered anyway."),
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert [e.code for e in events if e.type == "error"] == []
    assert events[-1].stop_reason == "final_answer"


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


async def test_unavailable_tool_is_dropped_not_retried(ws, registry, store, audit):
    """A dead backing service must cost one call, not MAX_TOOL_RETRIES.

    In one bench run a stopped Docker engine produced 11 of 16 failures --
    every task that wanted to run code burned three identical retries and died
    as TOOL_RETRIES_EXCEEDED, which reads as a broken agent rather than a
    stopped daemon. The tool is now dropped from the grammar for the rest of
    the run and the model is told once.
    """
    store.ensure_session("s1")

    class DeadTool:
        name = "execute_python"
        description = "Run Python in an offline sandbox."
        args_model = registry._tools["list_files"].args_model
        calls = 0

        def run(self, args, ctx):
            DeadTool.calls += 1
            return ToolResult(ok=False, content="", duration_ms=1,
                              error="sandbox unavailable: the Docker engine is not reachable")

    registry.register(DeadTool())
    llm = FakeLLM([
        tool_call("execute_python", {"path": "."}),
        final("I could not run code, so here is the reasoning instead."),
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert DeadTool.calls == 1, "the dead tool must be called once, not retried"
    assert [e for e in events if e.type == "error"] == []
    assert events[-1].stop_reason == "final_answer"
    # Asserted on the audit trail, not on llm.requests: FakeLLM stores a
    # reference to the loop's live message list, so every recorded request
    # aliases the same object and cannot show what was sent when.
    kinds = [r["kind"] for r in audit.trail("s1")]
    assert "tool.unavailable" in kinds
    # The second request went out with the tool removed from the grammar, so
    # the model cannot utter it again even if it wants to.
    assert "execute_python" not in (llm.requests[-1]["grammar"] or "")


async def test_failed_turn_still_leaves_an_assistant_row(ws, registry, store, audit):
    """A turn that ends in error must not vanish from the transcript.

    Before: error / max_steps / cancelled stored the user's question and
    nothing else, so a reopened session showed a blank answer with no reason.
    The stored row carries a "[stopped: <reason>]" marker the UI turns into
    the same badge a live run shows.
    """
    store.ensure_session("s1")
    store.add_message("s1", "user", "do the thing")
    same = {"path": "."}
    llm = FakeLLM([
        tool_call("list_files", same),
        tool_call("list_files", same),   # nudged
        tool_call("list_files", same),   # LOOP_DETECTED -> error
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert events[-1].stop_reason == "error"
    rows = [m for m in store.get_session("s1")["messages"] if m["role"] == "assistant"]
    assert len(rows) == 1
    assert rows[0]["content"].startswith("[stopped: error] LOOP_DETECTED")


async def test_max_steps_turn_records_why(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([
        tool_call("write_file", {"path": f"f{i}.txt", "content": str(i)})
        for i in range(MAX_STEPS)
    ])
    events = await collect(make_loop(llm, registry, store, audit, ws))
    assert events[-1].stop_reason == "max_steps"
    rows = [m for m in store.get_session("s1")["messages"] if m["role"] == "assistant"]
    assert rows and rows[-1]["content"].startswith("[stopped: max_steps]")


async def test_final_answer_stores_exactly_one_assistant_row(ws, registry, store, audit):
    store.ensure_session("s1")
    llm = FakeLLM([final("42")])
    await collect(make_loop(llm, registry, store, audit, ws))
    rows = [m for m in store.get_session("s1")["messages"] if m["role"] == "assistant"]
    assert [m["content"] for m in rows] == ["42"]
