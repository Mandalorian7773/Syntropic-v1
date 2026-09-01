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
import time
import uuid
from pathlib import Path
from typing import Any, Type

import httpx
from pydantic import BaseModel, ValidationError, create_model

from contracts import RunContext, Tool, ToolResult

MAX_TOOLS = 8
MAX_CONTENT_TOKENS = 1000
MAX_CONTENT_CHARS = MAX_CONTENT_TOKENS * 4


def truncate_content(content: str, ctx: RunContext, name: str) -> tuple[str, str | None]:
    """Enforce the <=1000-token contract; overflow goes to a raw file the
    model can read_file if it truly needs the rest."""
    if len(content) <= MAX_CONTENT_CHARS:
        return content, None
    raw_dir = Path(ctx.workspace_dir) / ".raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{name}-{uuid.uuid4().hex[:8]}.txt"
    raw_path.write_text(content, encoding="utf-8")
    kept = content[:MAX_CONTENT_CHARS]
    return f"{kept}\n[truncated; full output at {raw_path}]", str(raw_path)


class RemoteTool(Tool):
    """A tool that lives in ragsvc. Same contract, executed over HTTP."""

    name = "remote_placeholder"
    description = "placeholder"
    args_model: Type[BaseModel] = BaseModel

    def __init__(self, endpoint: str, spec: dict) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.name = spec["name"]
        self.description = spec["description"]
        fields: dict[str, Any] = {}
        for prop, schema in spec.get("parameters", {}).get("properties", {}).items():
            py = {"string": str, "integer": int, "number": float, "boolean": bool}.get(
                schema.get("type", "string"), str
            )
            required = prop in spec.get("parameters", {}).get("required", [])
            fields[prop] = (py, ... if required else None)
        self.args_model = create_model(f"{self.name}_args", **fields)

    def run(self, args: BaseModel, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        try:
            resp = httpx.post(
                f"{self.endpoint}/tools/{self.name}",
                json={"args": args.model_dump(), "session_id": ctx.session_id},
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
        """The tool list as it appears in the system prompt."""
        lines = []
        for t in self._tools.values():
            params = ", ".join(t.args_model.model_json_schema().get("properties", {}))
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
