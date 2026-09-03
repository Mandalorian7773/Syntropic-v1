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
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

# Substrings a tool uses to say "my backing service is down", as opposed to
# "your arguments were wrong". Kept as a list rather than a ToolResult field
# because ToolResult is a frozen contract shared with person 2 -- widening it
# needs the change protocol, and this is a local scheduling decision.
_UNAVAILABLE_MARKERS = ("sandbox unavailable", "docker unavailable",
                        "ragsvc unreachable", "docker sdk not installed")


def _is_unavailable(error: str | None) -> bool:
    lowered = (error or "").lower()
    return any(m in lowered for m in _UNAVAILABLE_MARKERS)

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
# Consecutive identical tool calls tolerated, each answered with a corrective
# message, before LOOP_DETECTED aborts. 1 is enough: the failure this fixes is
# a single unlucky sample, not a model that has genuinely wedged.
MAX_LOOP_NUDGES = 1
CONTEXT_COMPACT_THRESHOLD = 0.75
TOKEN_CHUNK_CHARS = 48

SYSTEM_PROMPT = """\
You are the on-premise assistant of a refinery. Everything runs locally; you
have no internet. You work in steps. On every step you emit EXACTLY ONE JSON
object and nothing else, in one of two shapes:

  {{"tool": "<name>", "args": {{...}}}}   to use a tool
  {{"final": "<your answer>"}}            when you are done

MOST QUESTIONS NEED NO TOOL. If you already know the answer, emit
{{"final": ...}} on the FIRST step. Reaching for a tool you do not need costs
the user ten seconds and usually returns nothing relevant. Examples that need
no tool at all:
  "Why is nitrogen purging done before opening a vessel?"  -> answer directly
  "What is a flare stack for?"                             -> answer directly
  "What does corrosion under insulation mean?"             -> answer directly
Reach for a tool only when the answer depends on something you cannot know:
this plant's documents, a file in the workspace, or a number you must compute.

Available tools:
{tools}

Choosing a tool -- and whether to use one at all:
- Answer from your own knowledge, with NO tool, when the question is general
  engineering or process knowledge: what a flare stack is for, why nitrogen
  purging is done, what corrosion under insulation means. These have no answer
  in the document store and searching for them wastes a step.
- Use search_documents when the question is about THIS plant's paperwork: an
  equipment tag such as PSV-2103 or V-1201, a document number, "the SOP", "the
  inspection report", a specific measured value. That is the only way to reach
  the ingested corpus.
- If a search comes back with nothing that answers the question, do NOT run it
  again. Answer from your own knowledge and say plainly that the documents did
  not cover it.
- read_file, write_file and list_files see ONLY the scratch workspace, which
  starts empty. They cannot open an ingested document. Reaching for read_file
  to answer a question about a report will always fail.
- execute_python runs a real offline sandbox. Write the code as real source
  with real newlines and print() what you want back. Use it when a number has
  to be computed, not to restate arithmetic you can already do.

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
        # Tools whose backing service is down for this run. They are dropped
        # from the grammar, which makes them literally unutterable rather than
        # merely discouraged -- a dead Docker engine otherwise consumed all of
        # MAX_TOOL_RETRIES on every task that wanted to run code (measured: 11
        # of 16 bench failures in one run, every data task among them).
        unavailable: set[str] = set()

        def current_grammar() -> str | None:
            if not grammar_on:
                return None
            usable = [n for n in self._registry.names() if n not in unavailable]
            return build_grammar(usable)

        grammar = current_grammar()
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
        nudges = 0                                 # consecutive repeats corrected
        last_failed: dict[tuple[str, str], bool] = {}   # signature -> last run failed
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

            # Token events have to leave this generator WHILE generation is
            # still running, so generate() runs as a task and its on_token
            # callback feeds a queue we drain here. Awaiting generate() first
            # and chunking the finished text afterwards is what made
            # time-to-first-token equal total latency -- 20 s of blank screen
            # on a 20 s request, measured.
            token_q: asyncio.Queue = asyncio.Queue()
            done_marker = object()

            async def _runner():
                try:
                    return await self._llm.generate(
                        messages, model_id=model_id, grammar=grammar,
                        expect_json=True, emit=collect_swap,
                        on_token=token_q.put,
                    )
                finally:
                    await token_q.put(done_marker)

            gen_task = asyncio.create_task(_runner())
            while True:
                item = await token_q.get()
                if item is done_marker:
                    break
                # model.loading / model.ready were queued by ensure() before a
                # single token existed; they must precede them on the wire.
                while swap_events:
                    yield swap_events.pop(0)
                yield audited(Token(text=item))
            try:
                resp = await gen_task
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
                if not resp.streamed:
                    # Nothing reached the UI live: either the model finished in
                    # the {"tool":"final",...} shape, or the incremental decode
                    # disagreed with the authoritative parse. Fall back to
                    # chunking, which is what always used to happen.
                    for i in range(0, len(resp.text), TOKEN_CHUNK_CHARS):
                        yield audited(Token(text=resp.text[i:i + TOKEN_CHUNK_CHARS]))
                yield await finish("final_answer")
                return

            call = resp.tool_calls[0]
            signature = (call.name, json.dumps(call.args, sort_keys=True))
            # Retrying a call that FAILED is not a loop, it is the documented
            # recovery path -- execute_python is expected to fail, get its
            # stderr back, and be re-run. Only MAX_TOOL_RETRIES bounds that.
            # Counting those as loops made a broken sandbox (docker engine
            # down: 38 of 43 execute_python calls failing) surface as
            # LOOP_DETECTED, which points the blame at the model instead of
            # at the daemon that is actually down.
            if signature in recent_calls[-2:] and not last_failed.get(signature):
                # A repeat is not automatically a wedged model. At the sampling
                # temperature the loop actually uses, the same step-2 context
                # was measured emitting search_documents, then read_document,
                # then the correct final answer on three identical calls. That
                # first sample is the one this used to abort on -- with the
                # answer already in context. So: nudge once, abort on the
                # second consecutive repeat. Retries stay bounded either way.
                nudges += 1
                if nudges > MAX_LOOP_NUDGES:
                    yield audited(AgentError(
                        code="LOOP_DETECTED",
                        message=f"{call.name} repeated with identical arguments "
                                f"after {MAX_LOOP_NUDGES} nudge(s); aborting",
                        recoverable=False,
                    ))
                    yield await finish("error")
                    return
                self._audit.record("agent.loop_nudge",
                                   {"step": steps_used, "tool": call.name}, session_id)
                messages.append({"role": "assistant", "content": resp.raw})
                messages.append({"role": "user", "content":
                    f"You already called {call.name} with exactly those arguments "
                    f"and its observation is above. Do not repeat it. Either answer "
                    f'now with {{"final": "<your answer>"}}, or call a different '
                    f"tool, or call the same tool with different arguments."})
                continue
            nudges = 0
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

            last_failed[signature] = not result.ok
            if not result.ok and _is_unavailable(result.error):
                # The tool cannot work at all right now -- retrying is pure
                # latency. Drop it for the rest of the run and tell the model
                # once, plainly, so it answers by another route.
                unavailable.add(call.name)
                grammar = current_grammar()
                self._audit.record("tool.unavailable",
                                   {"tool": call.name, "error": (result.error or "")[:200]},
                                   session_id)
                messages.append({"role": "assistant", "content": resp.raw})
                messages.append({"role": "user", "content":
                    f"{call.name} is unavailable for the rest of this task -- its "
                    f"backing service is down. This is a host fault, not your "
                    f"input. Do not call it again. Answer using the other tools, "
                    f"or reason it out and say what you could not verify."})
                continue
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

            # A tool whose content is itself JSON (search_documents returns
            # {"hits": [...]}) must be embedded as structure, not as a string.
            # Nesting it doubles every quote: measured on one real result,
            # 2683 chars and 100 backslashes nested vs 2600 and 19 parsed. The
            # model then has to read \"doc_id\" through the escaping and still
            # emit a grammar-constrained call.
            # AGENT_UNNEST_OBS=on embeds a tool's JSON content as structure
            # rather than as a nested string. It reads better -- one real
            # search_documents result is 2683 chars and 100 backslashes nested
            # against 2600 and 19 parsed -- but it measured WORSE end to end
            # (5/15 vs 8/15 completions; the retrieval prompt went 3/3 to 0/3),
            # so the default stays off. Kept as a flag because that measurement
            # was taken while the Docker sandbox was down and every
            # execute_python failed; it deserves a rerun on a healthy stack.
            content = result.content
            if os.getenv("AGENT_UNNEST_OBS", "off").lower() == "on":
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    pass
            observation = json.dumps({
                "tool": call.name,
                "ok": result.ok,
                "content": content,
                "error": result.error,
            })
            messages.append({"role": "assistant", "content": resp.raw})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            self._store.add_message(session_id, "tool", observation)

        yield await finish("max_steps")

    # --- event extraction -----------------------------------------------------

    def _citations_from(self, call: ParsedToolCall, result) -> list[Citation]:
        """search_documents returns its hits as JSON; surface them as citation
        events so the UI can pin sources. Anything unparseable is ignored.

        ragsvc currently returns `content` as a human-formatted string
        ("1. [file.pdf p.1] - snippet"), which carries neither doc_id nor
        score -- both required by the Citation contract. Parsing that string
        would mean inventing a doc_id, and the UI pins sources by doc_id, so a
        fabricated one is worse than no citation: it looks like it works. This
        stays strict on purpose; ragsvc needs to send `{"hits": [...]}` with
        doc_id and score per hit. When it does, this fires with no further
        change here -- see _log_citation_gap for the warning that says so.
        """
        if call.name != "search_documents" or not result.ok:
            return []
        try:
            hits = json.loads(result.content).get("hits", [])
        except (json.JSONDecodeError, TypeError, AttributeError):
            self._log_citation_gap(result)
            return []
        out = []
        for h in hits[:5]:
            try:
                out.append(Citation(**{k: h[k] for k in
                                       ("doc_id", "filename", "page",
                                        "score", "snippet")}))
            except (KeyError, TypeError, ValidationError):
                self._log_citation_gap(result)
        return out

    @staticmethod
    def _log_citation_gap(result) -> None:
        """One loud line, not a silent empty list. A retrieval demo that shows
        no citation panel is the failure this is meant to make obvious."""
        log.warning(
            "search_documents returned content that carries no citable hits, "
            "so zero citation events were emitted. Expected JSON "
            '{"hits":[{"doc_id","filename","page","score","snippet"}]}; got %r',
            (result.content or "")[:160],
        )

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
