"""llama.cpp HTTP client. Owner: person 3.

The single seam between this system and inference. Everything downstream calls
generate() and nothing else; nothing downstream knows a llama-server is behind
it. Swapping llama.cpp for vLLM later is a rewrite of this file and only this
file.

Tool-call protocol: when a grammar is supplied the model can only emit one of

    {"tool": "<registered name>", "args": {...}}
    {"final": "<answer text>"}

so parsing is json.loads and two key checks, not regex archaeology. When no
grammar is supplied the raw text is the final answer. The malformed-JSON rate
with and without the grammar is measured by bench/run.py -- that number is
acceptance criterion 3.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

from llm.manager import ModelManager


class ParsedToolCall(BaseModel):
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel):
    text: str                      # final-answer text ("" when a tool was called)
    raw: str                       # exactly what the model emitted
    tool_calls: list[ParsedToolCall] = Field(default_factory=list)
    is_final: bool
    malformed: bool = False        # grammar asked for JSON, model broke it anyway
    # True when the answer already reached the UI token-by-token as it was
    # generated. The loop must then NOT re-chunk `text` into token events, or
    # the user sees the whole answer twice.
    streamed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


class FinalAnswerStreamer:
    r"""Streams the answer out of `{"final": "..."}` while it is still arriving.

    The tool protocol wraps every answer in JSON, so the raw deltas cannot go
    to the UI -- they are protocol syntax. The loop's previous answer was to
    buffer the whole response, parse it, then chop the text into token events.
    Measured cost of that: time-to-first-token equal to total latency, 20 s of
    blank screen on a 20 s request, and a `gen_ms` of 7 ms because every token
    event was emitted after generation had already finished.

    So decode incrementally instead. Wait for the opening `{"final": "`, then
    emit each character of the JSON string as it decodes, honouring escapes,
    and stop at the closing quote. Anything that is not that shape -- a tool
    call, or `{"tool":"final","args":{...}}` -- streams nothing and is handled
    by the existing buffered path, which stays correct.
    """

    # Matched a character at a time, skipping the whitespace JSON allows
    # between tokens. A regex cannot do this: the deltas arrive split at
    # arbitrary boundaries ('{"fi' then 'nal"'), and re has no partial-match
    # mode, so any "does it match yet" test either fires early or gives up on
    # a prefix that was still going to arrive.
    _OPENING = '{"final":"'
    _ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
                "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

    def __init__(self) -> None:
        self.started = False      # we are inside the final string
        self.done = False         # closing quote seen
        self.emitted = ""
        self._buf = ""            # raw not yet consumed
        self._pending_escape = False
        self._unicode: str | None = None

    def _scan_opening(self) -> int | None:
        """Chars consumed once `{"final":"` is complete, else None.

        Sets self.done when the buffer can no longer become that opening --
        a tool call, or the {"tool":"final",...} shape -- so the rest of the
        response streams nothing.
        """
        want = 0
        for i, ch in enumerate(self._buf):
            if ch.isspace():
                continue            # JSON whitespace between tokens
            if ch != self._OPENING[want]:
                self.done = True    # definitively some other shape
                return None
            want += 1
            if want == len(self._OPENING):
                return i + 1
        return None                 # ran out of buffer, still undecided

    def feed(self, piece: str) -> str:
        """Add raw model output; return newly decoded answer text."""
        if self.done:
            return ""
        self._buf += piece
        if not self.started:
            consumed = self._scan_opening()
            if consumed is None:
                return ""           # not this shape, or still undecided
            self.started = True
            self._buf = self._buf[consumed:]

        out = []
        i = 0
        while i < len(self._buf):
            ch = self._buf[i]
            if self._unicode is not None:
                self._unicode += ch
                if len(self._unicode) == 4:
                    try:
                        out.append(chr(int(self._unicode, 16)))
                    except ValueError:
                        pass
                    self._unicode = None
                i += 1
                continue
            if self._pending_escape:
                self._pending_escape = False
                if ch == "u":
                    self._unicode = ""
                else:
                    out.append(self._ESCAPES.get(ch, ch))
                i += 1
                continue
            if ch == "\\":
                self._pending_escape = True
                i += 1
                continue
            if ch == '"':
                self.done = True
                i += 1
                break
            out.append(ch)
            i += 1
        self._buf = self._buf[i:]
        text = "".join(out)
        self.emitted += text
        return text


def _final_text(args: Any) -> str:
    """Answer text out of a {"tool":"final","args":...} payload.

    The args are whatever the model felt like: a bare string, {"answer": ...},
    or the answer spread across fields such as
    {"set_pressure": "12.5 barg", "page": 1, "filename": "SOP-INSP-014.pdf"}.
    The last case is why this does not just pick a key -- dropping the other
    fields would throw away the citation the user asked for.
    """
    if isinstance(args, str):
        return args
    if not isinstance(args, dict):
        return "" if args is None else str(args)
    for key in ("answer", "final", "text", "content", "response"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parts = [f"{k}: {v}" for k, v in args.items() if v is not None]
    return "; ".join(parts)


class LLMClient:
    def __init__(self, manager: ModelManager, timeout_s: float = 300.0) -> None:
        self._manager = manager
        self._timeout = timeout_s
        # One client, reused. A fresh AsyncClient per generate() means a new
        # connection per agent step, and the agent makes one call per step for
        # up to MAX_STEPS steps on every message.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_keepalive_connections=4,
                                    max_connections=8),
            )
        return self._client

    async def aclose(self) -> None:
        """Called from the FastAPI shutdown hook."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model_id: str | None = None,
        grammar: str | None = None,
        stream: bool = True,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        expect_json: bool = False,
    ) -> Response:
        """The one method. Ensures model_id is resident (emitting model.loading
        and model.ready through `emit` if a swap happens), then completes."""
        target = model_id or self._manager.loaded_id or self._manager.registry.default.id
        await self._manager.ensure(target, emit)

        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            # Explicit, not left to the server default: every agent step
            # re-sends the whole conversation, so reusing the cached prefix is
            # the difference between reprocessing ~3000 tokens per step and
            # processing only what was appended.
            "cache_prompt": True,
        }
        if grammar:
            # Grammar-constrained decoding: the tool protocol is enforced at
            # the decoder, so `tools` stays out of the request -- the agent has
            # already described them in the system prompt.
            payload["grammar"] = grammar
        elif tools:
            payload["tools"] = tools
        if stream:
            payload["stream_options"] = {"include_usage": True}

        url = f"{self._manager.endpoint}/v1/chat/completions"
        raw, tokens_in, tokens_out = "", 0, 0
        # expect_json without grammar is the measurement mode: same protocol,
        # decoder unconstrained, so bench can put a number on what the grammar
        # is worth. json_mode output never streams to the UI as tokens.
        json_mode = expect_json or grammar is not None
        streamer = FinalAnswerStreamer() if (json_mode and on_token and stream) else None

        # One retry, and only if nothing has reached the UI yet. A managed
        # llama-server is replaced on every model swap and on every gateway
        # restart; a request that lands in that window used to fail the whole
        # turn with LLM_ERROR 'All connection attempts failed'. ensure() probes
        # the endpoint and restarts a dead server, so going back through it
        # turns a fatal error into a pause. A disconnect AFTER tokens streamed
        # is not retried -- replaying would show the user the answer twice.
        for attempt in (1, 2):
            try:
                client = self._http()
                if stream:
                    async with client.stream("POST", url, json=payload) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[len("data: "):]
                            if data.strip() == "[DONE]":
                                break
                            chunk = json.loads(data)
                            usage = chunk.get("usage")
                            if usage:
                                tokens_in = usage.get("prompt_tokens", 0)
                                tokens_out = usage.get("completion_tokens", 0)
                            for choice in chunk.get("choices", []):
                                piece = (choice.get("delta") or {}).get("content") or ""
                                if piece:
                                    raw += piece
                                    if not on_token:
                                        continue
                                    if not json_mode:
                                        await on_token(piece)
                                    elif streamer is not None:
                                        # Protocol JSON never goes to the UI raw --
                                        # but the answer INSIDE it can, as it lands.
                                        text = streamer.feed(piece)
                                        if text:
                                            await on_token(text)
                else:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                    raw = body["choices"][0]["message"].get("content") or ""
                    usage = body.get("usage") or {}
                    tokens_in = usage.get("prompt_tokens", 0)
                    tokens_out = usage.get("completion_tokens", 0)
                break
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
                if attempt == 2 or raw:
                    raise
                if streamer is not None:
                    streamer = FinalAnswerStreamer()
                await self._manager.ensure(target, emit)


        if not tokens_in:
            tokens_in = sum(len(str(m.get("content", ""))) for m in messages) // 4
        if not tokens_out:
            tokens_out = len(raw) // 4

        parsed = self._parse(raw, json_mode=json_mode,
                             tokens_in=tokens_in, tokens_out=tokens_out)
        if streamer is not None and streamer.emitted and parsed.is_final:
            # Only trust the incremental decode when it agrees with the
            # authoritative parse. If they differ the model produced something
            # the streamer misread, and re-chunking the parsed text is the safe
            # outcome -- a duplicated answer is better than a truncated one.
            parsed.streamed = streamer.emitted == parsed.text
        return parsed

    @staticmethod
    def _parse(raw: str, json_mode: bool, tokens_in: int, tokens_out: int) -> Response:
        if not json_mode:
            return Response(text=raw, raw=raw, is_final=True,
                            tokens_in=tokens_in, tokens_out=tokens_out)
        cleaned = raw.strip()
        # Ungrammared models love markdown fences; stripping them is standard
        # leniency, not part of the malformed measurement. Malformed = what is
        # left still does not parse.
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else ""
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        try:
            obj = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            # The whole point of the grammar is that this branch never runs.
            # It exists so that when it does, the measurement is honest.
            return Response(text=raw, raw=raw, is_final=True, malformed=True,
                            tokens_in=tokens_in, tokens_out=tokens_out)
        if isinstance(obj, dict) and "tool" in obj:
            # {"tool":"final","args":{...}} is the model finishing in the tool
            # shape rather than the final shape. Both mean "done"; treating the
            # first as a call to a tool named `final` sends the loop round again
            # and it eventually trips LOOP_DETECTED with the answer in hand.
            if str(obj["tool"]).lower() == "final":
                return Response(text=_final_text(obj.get("args")), raw=raw,
                                is_final=True,
                                tokens_in=tokens_in, tokens_out=tokens_out)
            call = ParsedToolCall(
                call_id=f"c{uuid.uuid4().hex[:8]}",
                name=str(obj["tool"]),
                args=obj.get("args") or {},
            )
            return Response(text="", raw=raw, tool_calls=[call], is_final=False,
                            tokens_in=tokens_in, tokens_out=tokens_out)
        if isinstance(obj, dict) and "final" in obj:
            return Response(text=str(obj["final"]), raw=raw, is_final=True,
                            tokens_in=tokens_in, tokens_out=tokens_out)
        return Response(text=raw, raw=raw, is_final=True, malformed=True,
                        tokens_in=tokens_in, tokens_out=tokens_out)
