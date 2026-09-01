"""LLMClient protocol parsing and the SSE heartbeat wrapper."""

import asyncio

from contracts import Token
from llm.client import LLMClient
from sse import Cancels, stream_events


def parse(raw, json_mode=True):
    return LLMClient._parse(raw, json_mode=json_mode, tokens_in=0, tokens_out=0)


def test_tool_call_parses():
    r = parse('{"tool": "read_file", "args": {"path": "a.txt"}}')
    assert not r.is_final and not r.malformed
    assert r.tool_calls[0].name == "read_file"
    assert r.tool_calls[0].args == {"path": "a.txt"}


def test_final_parses():
    r = parse('{"final": "All done."}')
    assert r.is_final and r.text == "All done." and not r.malformed


def test_garbage_is_malformed_not_crash():
    r = parse("Sure! Here is the JSON you asked for: {tool: read_file}")
    assert r.malformed and r.is_final


def test_fenced_json_is_leniently_accepted():
    r = parse('```json\n{"tool": "list_files", "args": {}}\n```')
    assert not r.malformed and r.tool_calls[0].name == "list_files"


def test_plain_text_mode_never_malformed():
    r = parse("Just a normal answer.", json_mode=False)
    assert r.is_final and not r.malformed and r.text == "Just a normal answer."


async def test_heartbeat_fills_silence():
    async def slow():
        yield Token(text="a")
        await asyncio.sleep(0.25)
        yield Token(text="b")

    frames = [f async for f in stream_events(slow(), heartbeat_s=0.1)]
    assert any(f.startswith(": ping") for f in frames)
    data_frames = [f for f in frames if f.startswith("event:")]
    assert len(data_frames) == 2


async def test_cancels_registry():
    c = Cancels()
    event = c.register("s1")
    assert not event.is_set()
    assert c.cancel("s1") and event.is_set()
    assert not c.cancel("ghost")
    # Re-registering after a set event hands back a fresh one.
    assert not c.register("s1").is_set()
