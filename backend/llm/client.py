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
    tokens_in: int = 0
    tokens_out: int = 0


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

        async with httpx.AsyncClient(timeout=self._timeout) as client:
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
                                # Only free-text streams to the UI; protocol
                                # JSON is parsed, never shown raw.
                                if on_token and not json_mode:
                                    await on_token(piece)
            else:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
                raw = body["choices"][0]["message"].get("content") or ""
                usage = body.get("usage") or {}
                tokens_in = usage.get("prompt_tokens", 0)
                tokens_out = usage.get("completion_tokens", 0)

        if not tokens_in:
            tokens_in = sum(len(str(m.get("content", ""))) for m in messages) // 4
        if not tokens_out:
            tokens_out = len(raw) // 4

        return self._parse(raw, json_mode=json_mode,
                           tokens_in=tokens_in, tokens_out=tokens_out)

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
