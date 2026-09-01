"""The agent loop. Owner: person 3. No framework -- see docs/decisions/0004.

The cycle: generate under a GBNF grammar that can only utter a registered
tool call or a final answer; execute the call; append the observation; repeat,
bounded by MAX_STEPS. Four rules keep the demo from hanging:

  1. Grammar-constrained decoding on EVERY tool-calling request. Malformed
     JSON becomes a measured rarity instead of a parsing adventure.
  2. Loop detection: a call identical to either of the previous two aborts
     the run with LOOP_DETECTED. A 7B model that repeats itself once will
     happily repeat itself forever.
  3. Context compaction at 75% -- oldest tool results collapse into a digest.
  4. Every step is persisted BEFORE the next begins: each event is written to
     the audit log before it is yielded, so a crash mid-run still leaves a
     complete trail (acceptance criterion 7).

The self-correction demo (criterion 4) falls out of the loop shape: a failed
execute_python returns its stderr as the observation, the model fixes the code
and calls again. MAX_TOOL_RETRIES caps consecutive failures of the same tool
so "let it fix it" cannot become "let it flail".
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel

from contracts import (
    AgentError,
    AgentStep,
    Artifact,
    Citation,
    Done,
    RunContext,
    Token,
    ToolCall,
    ToolResultEvent,
)

from agent.compact import compact, used_fraction
from agent.grammar import build_grammar
from audit.logger import AuditLog
from db.store import Store
from llm.client import LLMClient, ParsedToolCall
from tools.registry import Registry

MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))
MAX_TOOL_RETRIES = 3
CONTEXT_COMPACT_THRESHOLD = 0.75
TOKEN_CHUNK_CHARS = 48

SYSTEM_PROMPT = """\
You are the on-premise assistant of a refinery. Everything runs locally; you
have no internet. You work in steps. On every step you emit EXACTLY ONE JSON
object and nothing else, in one of two shapes:

  {{"tool": "<name>", "args": {{...}}}}   to use a tool
  {{"final": "<your answer>"}}            when you are done

Available tools:
{tools}

Rules: one tool per step. Read a tool's observation before deciding the next
step. If a tool fails, fix your input and try again. When you have enough to
answer, emit the final object -- do not call a tool you have already called
with the same arguments.
"""


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        registry: Registry,
        store: Store,
        audit: AuditLog,
        workspace_dir: str,
        artifacts_dir: str,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._store = store
        self._audit = audit
        self._workspace_dir = workspace_dir
        self._artifacts_dir = artifacts_dir

    async def run(
        self,
        session_id: str,
        user_message: list[dict] | str,
        model_id: str,
        context_tokens: int,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[BaseModel]:
        """Yields contract events. Every yielded event is already audited."""
        started = time.monotonic()
        ctx = RunContext(
            session_id=session_id,
            workspace_dir=self._workspace_dir,
            artifacts_dir=self._artifacts_dir,
        )
        # AGENT_GRAMMAR=off is the bench's measurement mode: identical protocol,
        # unconstrained decoder, so the malformed-JSON rate can be compared
        # with and without the grammar (acceptance criterion 3).
        grammar_on = os.getenv("AGENT_GRAMMAR", "on").lower() != "off"
        grammar = build_grammar(self._registry.names()) if grammar_on else None
        messages: list[dict] = [
            {"role": "system",
             "content": SYSTEM_PROMPT.format(tools=self._registry.prompt_block())},
            {"role": "user", "content": user_message},
        ]
        swap_events: list[BaseModel] = []

        async def collect_swap(event: BaseModel) -> None:
            # model.loading / model.ready arrive from inside generate();
            # buffer them, audited, so the generator can yield them in order.
            self._audit.event(event, session_id)
            swap_events.append(event)

        recent_calls: list[tuple[str, str]] = []   # (name, canonical args) history
        fail_streak: dict[str, int] = {}
        tokens_in = tokens_out = 0
        steps_used = 0

        def audited(event: BaseModel) -> BaseModel:
            self._audit.event(event, session_id)
            return event

        async def finish(stop_reason: str) -> Done:
            return audited(Done(
                stop_reason=stop_reason,
                steps_used=steps_used,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=int((time.monotonic() - started) * 1000),
            ))

        for step in range(MAX_STEPS):
            if cancel is not None and cancel.is_set():
                yield await finish("cancelled")
                return
            if used_fraction(messages, context_tokens) > CONTEXT_COMPACT_THRESHOLD:
                messages = compact(messages)
                self._audit.record("context.compacted",
                                   {"step": step, "messages": len(messages)}, session_id)

            steps_used = step + 1
            yield audited(AgentStep(step=steps_used, max_steps=MAX_STEPS))

            try:
                resp = await self._llm.generate(
                    messages, model_id=model_id, grammar=grammar,
                    expect_json=True, emit=collect_swap,
                )
            except Exception as exc:
                yield audited(AgentError(code="LLM_ERROR", message=str(exc)[:300],
                                         recoverable=False))
                yield await finish("error")
                return
            while swap_events:
                yield swap_events.pop(0)
            tokens_in += resp.tokens_in
            tokens_out += resp.tokens_out

            if resp.malformed:
                self._audit.record("llm.malformed", {"raw": resp.raw[:500]}, session_id)
                messages.append({"role": "assistant", "content": resp.raw})
                messages.append({"role": "user",
                                 "content": "That was not valid tool-call JSON. Emit "
                                            "exactly one {\"tool\":...} or {\"final\":...} object."})
                continue

            if resp.is_final:
                self._store.add_message(session_id, "assistant", resp.text)
                for i in range(0, len(resp.text), TOKEN_CHUNK_CHARS):
                    yield audited(Token(text=resp.text[i:i + TOKEN_CHUNK_CHARS]))
                yield await finish("final_answer")
                return

            call = resp.tool_calls[0]
            signature = (call.name, json.dumps(call.args, sort_keys=True))
            if signature in recent_calls[-2:]:
                yield audited(AgentError(
                    code="LOOP_DETECTED",
                    message=f"{call.name} repeated with identical arguments; aborting",
                    recoverable=False,
                ))
                yield await finish("error")
                return
            recent_calls.append(signature)

            # Persist the call before executing it -- if execution wedges the
            # process, the trail still shows what was attempted.
            self._store.add_tool_call(session_id, call.call_id, steps_used,
                                      call.name, call.args)
            yield audited(ToolCall(call_id=call.call_id, name=call.name, args=call.args))

            result = await asyncio.to_thread(
                self._registry.execute, call.name, call.args, ctx
            )

            summary = (result.content or result.error or "")[:200]
            self._store.finish_tool_call(session_id, call.call_id, result.ok,
                                         summary, result.duration_ms)
            yield audited(ToolResultEvent(
                call_id=call.call_id, ok=result.ok, summary=summary,
                duration_ms=result.duration_ms,
                truncated=result.raw_path is not None,
            ))

            for event in self._citations_from(call, result):
                yield audited(event)
            for event in self._artifacts_from(session_id, result):
                yield audited(event)

            if result.ok:
                fail_streak[call.name] = 0
            else:
                fail_streak[call.name] = fail_streak.get(call.name, 0) + 1
                if fail_streak[call.name] >= MAX_TOOL_RETRIES:
                    yield audited(AgentError(
                        code="TOOL_RETRIES_EXCEEDED",
                        message=f"{call.name} failed {MAX_TOOL_RETRIES} times in a row; "
                                f"last error: {result.error}",
                        recoverable=False,
                    ))
                    yield await finish("error")
                    return

            observation = json.dumps({
                "tool": call.name,
                "ok": result.ok,
                "content": result.content,
                "error": result.error,
            })
            messages.append({"role": "assistant", "content": resp.raw})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            self._store.add_message(session_id, "tool", observation)

        yield await finish("max_steps")

    # --- event extraction -----------------------------------------------------

    def _citations_from(self, call: ParsedToolCall, result) -> list[Citation]:
        """search_documents returns its hits as JSON; surface them as citation
        events so the UI can pin sources. Anything unparseable is ignored."""
        if call.name != "search_documents" or not result.ok:
            return []
        try:
            hits = json.loads(result.content).get("hits", [])
            return [Citation(**{k: h[k] for k in
                                ("doc_id", "filename", "page", "score", "snippet")})
                    for h in hits[:5]]
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            return []

    def _artifacts_from(self, session_id: str, result) -> list[Artifact]:
        events = []
        for rel in result.artifacts:
            for base in (self._artifacts_dir, self._workspace_dir):
                path = Path(base) / rel
                if path.is_file():
                    break
            else:
                continue
            artifact_id = f"a{uuid.uuid4().hex[:8]}"
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            self._store.add_artifact(artifact_id, session_id, path.name,
                                     mime, size, str(path))
            events.append(Artifact(
                artifact_id=artifact_id, filename=path.name, mime=mime,
                size_bytes=size, url=f"/api/artifacts/{artifact_id}",
            ))
        return events
