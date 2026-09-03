"""Tool registry. Owner: person 3.

The name -> instance map, the schema list handed to the model, and the single
choke point every execution passes through: args validated against the tool's
Pydantic model, wall-clock measured, content truncated to the contract's 1000
tokens with the full output parked at raw_path.

HARD CAP: 8 tools. A 7B model handed more chooses badly. register() raises on
the ninth -- if someone wants a ninth tool, something must be removed, and
that argument should happen in a PR, not in production.

Person 2's tools live in ragsvc and are pulled in over HTTP at startup:
GET {RAG_ENDPOINT}/tools lists them, POST {RAG_ENDPOINT}/tools/{name} runs
one. They count against the same cap.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Type

import httpx
from pydantic import BaseModel, ValidationError, create_model

from contracts import RunContext, Tool, ToolResult

MAX_TOOLS = 8
# The B5 contract says 1000 tokens. 1000 cut a single scanned page of a valve
# register mid-table, and the agent then answered with the NEIGHBOURING row --
# "PSV-2103 = 16.4 barg" when 16.4 is PSV-2105's and the truth is 12.5. A
# confidently wrong number with a citation attached is worse than an error, so
# the budget is sized to fit one document page instead. Override with
# AGENT_MAX_CONTENT_TOKENS to re-measure.
MAX_CONTENT_TOKENS = int(os.getenv("AGENT_MAX_CONTENT_TOKENS", "2500"))
MAX_CONTENT_CHARS = MAX_CONTENT_TOKENS * 4


def truncate_content(content: str, ctx: RunContext, name: str) -> tuple[str, str | None]:
    """Bound tool output; overflow is written to disk for the audit trail.

    The notice deliberately does NOT hand the model the raw path. It used to,
    and the model reasonably tried read_file on an absolute path outside the
    workspace, got "escapes the workspace", searched again, re-read the same
    page, and burned all ten steps in that cycle. raw_path stays on the
    ToolResult -- the trace and the audit row still point at the full output --
    but what goes into the model's context is an instruction it can act on.
    """
    if len(content) <= MAX_CONTENT_CHARS:
        return content, None
    raw_dir = Path(ctx.workspace_dir) / ".raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{name}-{uuid.uuid4().hex[:8]}.txt"
    raw_path.write_text(content, encoding="utf-8")
    kept = content[:MAX_CONTENT_CHARS]
    return (
        f"{kept}\n[output truncated here. Do NOT try to open the full file -- it "
        f"is not reachable from the workspace. If what you need is missing, call "
        f"{name} again for a narrower part, e.g. a single page.]"
    ), str(raw_path)


_SCALARS = {"string": str, "integer": int, "number": float, "boolean": bool}


def _python_type(schema: dict) -> Any:
    """JSON Schema property -> Python type for the generated args model.

    `anyOf` has to be handled, not defaulted past. ragsvc declares
    read_document's `pages` as {"anyOf": [{"type": "array", "items":
    {"type": "integer"}}, {"type": "null"}]}, which carries no top-level
    "type" -- so a `schema.get("type", "string")` silently types it `str`,
    the model correctly sends [1], and Pydantic rejects it with "Input should
    be a valid string". The tool then fails every time with args that were
    right all along, and the agent burns its retries on a phantom.
    """
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            if branch.get("type") != "null":
                return _python_type(branch)
        return Any
    kind = schema.get("type")
    if kind == "array":
        return list[_python_type(schema.get("items", {}))]
    if kind == "object":
        return dict
    if kind is None:
        # No type at all: accept whatever the model sends rather than
        # inventing a constraint ragsvc never declared.
        return Any
    return _SCALARS.get(kind, str)


def _shape_hint(schema: dict, defs: dict, depth: int = 0) -> str:
    """Compact, model-facing rendering of a parameter's shape.

    str | int | [str] | {heading, body, bullets} | [{name, columns, rows}]

    Kept to one level of nesting on purpose: the point is to stop the model
    guessing "list of strings" for a list of objects, not to reproduce the
    JSON Schema in the system prompt. A 7B model handed paragraphs picks badly.
    """
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        return _shape_hint(defs.get(ref, {}), defs, depth)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            if branch.get("type") != "null":
                return _shape_hint(branch, defs, depth)
        return "any"
    kind = schema.get("type")
    if kind == "array":
        return f"[{_shape_hint(schema.get('items', {}), defs, depth + 1)}]"
    if kind == "object" or "properties" in schema:
        # depth 1 is still worth naming: `sections: [{heading, body, ...}]` is
        # an array (0) whose items are the object (1), and those keys are
        # precisely what the model was guessing wrong.
        if depth >= 2:
            return "{...}"
        keys = list(schema.get("properties", {}))[:5]
        return "{" + ", ".join(keys) + "}" if keys else "{...}"
    return {"string": "str", "integer": "int", "number": "num",
            "boolean": "bool"}.get(kind, "any")


def clamp_to_schema(payload: dict, parameters: dict) -> dict:
    """Pull numeric arguments inside the bounds the tool's schema declares.

    ragsvc says top_k is 1..20; the model asked for 100 ("list every
    equipment tag in all of them") and the whole call was rejected with a
    Pydantic validation error it could not act on, then repeated. A number
    outside the declared range is a request for "as many as you allow", so
    clamping is the faithful translation, and one fewer way to burn a step.
    Only `minimum` / `maximum` are honoured; nothing else is rewritten.
    """
    props = (parameters or {}).get("properties", {})
    out = dict(payload)
    for name, value in payload.items():
        spec = props.get(name) or {}
        if "anyOf" in spec:
            spec = next((b for b in spec["anyOf"] if b.get("type") != "null"), spec)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if lo is not None and value < lo:
            out[name] = lo
        if hi is not None and value > hi:
            out[name] = hi
    return out


class RemoteTool(Tool):
    """A tool that lives in ragsvc. Same contract, executed over HTTP."""

    name = "remote_placeholder"
    description = "placeholder"
    args_model: Type[BaseModel] = BaseModel

    def __init__(self, endpoint: str, spec: dict) -> None:  # noqa: D107
        self.endpoint = endpoint.rstrip("/")
        self.name = spec["name"]
        self.description = spec["description"]
        # ragsvc's own JSON Schema, kept verbatim. The args model generated
        # below flattens $ref'd objects to Any, so it is the wrong thing to
        # describe the tool to the model with -- prompt_block uses this.
        self.raw_parameters = spec.get("parameters", {})
        fields: dict[str, Any] = {}
        for prop, schema in spec.get("parameters", {}).get("properties", {}).items():
            py = _python_type(schema)
            required = prop in spec.get("parameters", {}).get("required", [])
            fields[prop] = (py, ... if required else None)
        self.args_model = create_model(f"{self.name}_args", **fields)

    def run(self, args: BaseModel, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        payload = args.model_dump()
        if self.name == "search_documents":
            # Floor top_k. The model asked for top_k=1, got a single chunk
            # that opened with the TAIL of a table (PSV-2105's row, 16.4 barg)
            # followed by prose about PSV-2103, and reported 16.4 as PSV-2103's
            # set pressure. The row that actually names PSV-2103 sits in the
            # neighbouring chunk. One hit is never enough to ground a number;
            # five costs nothing extra -- the reranker already scores 30.
            payload["top_k"] = max(int(payload.get("top_k") or 5), 5)
        payload = clamp_to_schema(payload, self.raw_parameters)
        try:
            resp = httpx.post(
                f"{self.endpoint}/tools/{self.name}",
                json={"args": payload, "session_id": ctx.session_id},
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            return ToolResult(
                ok=bool(body.get("ok")),
                content=str(body.get("content", ""))[:MAX_CONTENT_CHARS],
                raw_path=body.get("raw_path"),
                artifacts=body.get("artifacts", []),
                duration_ms=int((time.monotonic() - started) * 1000),
                error=body.get("error"),
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                ok=False, content="", duration_ms=int((time.monotonic() - started) * 1000),
                error=f"ragsvc unreachable: {exc}",
            )


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name {tool.name!r}")
        if len(self._tools) >= MAX_TOOLS:
            raise ValueError(
                f"tool cap is {MAX_TOOLS}; refusing {tool.name!r}. "
                "Small models handed more tools choose badly -- remove one first."
            )
        self._tools[tool.name] = tool

    def register_remote(self, endpoint: str) -> int:
        """Pull Person 2's tools from ragsvc. Returns how many registered;
        zero (with the reason swallowed into the log) when ragsvc is down,
        because the file tools must still work without it."""
        try:
            resp = httpx.get(f"{endpoint.rstrip('/')}/tools", timeout=5)
            resp.raise_for_status()
            specs = resp.json().get("tools", [])
        except httpx.HTTPError:
            return 0
        count = 0
        for spec in specs:
            if spec.get("name") in self._tools:
                continue
            self.register(RemoteTool(endpoint, spec))
            count += 1
        return count

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def prompt_block(self) -> str:
        """The tool list as it appears in the system prompt.

        Parameter SHAPES, not just names. ragsvc's create_docx takes
        sections: [{heading, body, bullets[], table[][]}] behind a $ref, and a
        bare "create_docx(template, title, sections)" left the model to guess --
        it guessed a list of strings, every call was rejected as
        "Input should be a valid dictionary or instance of Section", and the
        artifact demo could never produce a file. One extra line per tool is
        cheaper than a tool that can never be called correctly.
        """
        lines = []
        for t in self._tools.values():
            schema = getattr(t, "raw_parameters", None) or t.args_model.model_json_schema()
            defs = schema.get("$defs", {})
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            params = ", ".join(
                f"{name}{'' if name in required else '?'}: {_shape_hint(spec, defs)}"
                for name, spec in props.items()
            )
            lines.append(f"- {t.name}({params}): {t.description}")
        return "\n".join(lines)

    def execute(self, name: str, args: dict, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False, content="", duration_ms=0,
                error=f"unknown tool {name!r}; available: {self.names()}",
            )
        try:
            parsed = tool.args_model(**args)
        except ValidationError as exc:
            return ToolResult(
                ok=False, content="", duration_ms=0,
                error=f"bad args for {name}: {json.dumps(exc.errors(), default=str)[:500]}",
            )
        try:
            result = tool.run(parsed, ctx)
        except Exception as exc:  # a tool bug must not kill the agent run
            return ToolResult(
                ok=False, content="",
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"{name} raised {type(exc).__name__}: {exc}",
            )
        if len(result.content) > MAX_CONTENT_CHARS:
            content, raw_path = truncate_content(result.content, ctx, name)
            result = result.model_copy(
                update={"content": content, "raw_path": result.raw_path or raw_path}
            )
        return result
