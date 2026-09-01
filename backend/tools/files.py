"""Workspace file tools. Owner: person 3.

read_file / write_file / list_files, confined to WORKSPACE_DIR. The
confinement rule: resolve the path (which follows symlinks), then require the
result to still be inside the resolved workspace. A path that escapes AFTER
resolution -- `../`, absolute, or a symlink planted inside the workspace --
is refused with the same error, before any filesystem access happens.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from contracts import RunContext, Tool, ToolResult

MAX_CONTENT_CHARS = 4000  # contract: content <= 1000 tokens


class PathEscapeError(Exception):
    pass


def safe_path(workspace_dir: str, rel: str) -> Path:
    root = Path(workspace_dir).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapeError(f"path {rel!r} escapes the workspace")
    return candidate


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class ReadArgs(BaseModel):
    path: str


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file from the workspace and return its contents."
    args_model = ReadArgs

    def run(self, args: ReadArgs, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        try:
            target = safe_path(ctx.workspace_dir, args.path)
            content = target.read_text(encoding="utf-8", errors="replace")
        except PathEscapeError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started), error=str(exc))
        except OSError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started),
                              error=f"cannot read {args.path!r}: {exc}")
        truncated = len(content) > MAX_CONTENT_CHARS
        return ToolResult(
            ok=True,
            content=content[:MAX_CONTENT_CHARS]
            + (f"\n[truncated, {len(content)} chars total]" if truncated else ""),
            raw_path=str(target) if truncated else None,
            duration_ms=_ms(started),
        )


class WriteArgs(BaseModel):
    path: str
    content: str


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write text to a file in the workspace, creating parents as needed."
    args_model = WriteArgs

    def run(self, args: WriteArgs, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        try:
            target = safe_path(ctx.workspace_dir, args.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args.content, encoding="utf-8")
        except PathEscapeError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started), error=str(exc))
        except OSError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started),
                              error=f"cannot write {args.path!r}: {exc}")
        return ToolResult(
            ok=True,
            content=f"wrote {len(args.content)} chars to {args.path}",
            duration_ms=_ms(started),
        )


class ListArgs(BaseModel):
    path: str = "."


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files and directories at a workspace path."
    args_model = ListArgs

    def run(self, args: ListArgs, ctx: RunContext) -> ToolResult:
        started = time.monotonic()
        try:
            target = safe_path(ctx.workspace_dir, args.path)
            if not target.is_dir():
                return ToolResult(ok=False, content="", duration_ms=_ms(started),
                                  error=f"{args.path!r} is not a directory")
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = [
                f"{'d' if p.is_dir() else 'f'} {p.name}"
                + ("" if p.is_dir() else f" ({p.stat().st_size} B)")
                for p in entries
                if not p.name.startswith(".raw")
            ]
        except PathEscapeError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started), error=str(exc))
        except OSError as exc:
            return ToolResult(ok=False, content="", duration_ms=_ms(started),
                              error=f"cannot list {args.path!r}: {exc}")
        return ToolResult(
            ok=True,
            content="\n".join(lines) if lines else "(empty)",
            duration_ms=_ms(started),
        )
