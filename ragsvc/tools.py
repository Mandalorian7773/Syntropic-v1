"""RAG-side tools exposed to the agent. Owner: person 2.

Four tools, implementing `contracts.Tool`. The base class is imported from
`contracts`, never redefined here -- it is the shared surface all three
services agree on, and it enforces the 24-character name and 120-character
description limits at import time so a bad tool fails process start rather than
the demo.

Two rules shape everything below.

**`content` is consumed by a 7B model with a 16K window.** Every result goes
through `ragbudget.fit`, which truncates to 1000 tokens, writes the full text
to `raw_path`, and says so inside the content. One unbounded tool result blows
the context and the agent fails three steps later for reasons nobody can debug.

**`description` is one short sentence.** The model picks a tool by reading
these. A paragraph makes it pick wrong.

Person 3 can consume these two ways. In one process, import the classes here.
Across containers -- which is what docker-compose.yml actually runs -- use the
`GET /tools` and `POST /tools/{name}` endpoints in main.py, which wrap exactly
these objects and return the same ToolResult JSON.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

import ragbudget
import ragconfig as cfg
from contracts import RunContext, Tool, ToolResult
from docgen import Section, Sheet, UnknownTemplate, available_templates, create_docx, create_xlsx

# Room for the framing lines the formatters add around the retrieved text.
FRAME_TOKENS = 60


def _timed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _failure(started: float, message: str) -> ToolResult:
    """Errors come back as a result, never as an exception.

    The agent loop has to be able to show the model what went wrong and let it
    try something else; a traceback crossing the boundary ends the turn instead.
    """
    return ToolResult(ok=False, content=message, raw_path=None, duration_ms=_timed(started), error=message)


# --- search_documents -------------------------------------------------------


class SearchArgs(BaseModel):
    query: str = Field(description="What to search for, in natural language.")
    top_k: int = Field(default=5, ge=1, le=20, description="How many passages to return.")


class SearchDocuments(Tool):
    name = "search_documents"
    description = "Search the ingested documents and return passages with their filename and page."
    args_model = SearchArgs

    def run(self, args: SearchArgs, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        try:
            from index.search import search  # noqa: PLC0415

            result = search(args.query, top_k=args.top_k)
        except Exception as exc:  # noqa: BLE001
            return _failure(started, f"search failed: {exc}")

        if not result.hits:
            return ToolResult(
                ok=True,
                content=f"No passages matched {args.query!r}. The corpus may not contain it.",
                raw_path=None,
                duration_ms=_timed(started),
            )

        full = "\n\n".join(
            f"[{hit.filename} p.{hit.page}] {hit.section}\n{hit.text}".rstrip()
            for hit in result.hits
        )
        content = _format_hits(result.hits)
        raw_path = None
        if ragbudget.count_tokens(full) > cfg.TOOL_TOKEN_BUDGET:
            raw_path = ragbudget.spill(full, "search")
        return ToolResult(
            ok=True, content=content, raw_path=raw_path, duration_ms=_timed(started)
        )


def _format_hits(hits: list) -> str:
    """Numbered passages, each prefixed [filename p.N], inside the token budget.

    The budget is shared out across hits rather than spent front to back on
    purpose: the citation line of the fifth hit is worth more to the agent than
    the last hundred tokens of the first. Every hit keeps its provenance, and
    the passage bodies shrink to fit.
    """
    if not hits:
        return ""
    remaining = cfg.TOOL_TOKEN_BUDGET - FRAME_TOKENS

    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        header = f"{index}. [{hit.filename} p.{hit.page}]"
        if hit.section:
            header += f" - {hit.section}"

        # The share is recomputed each time so a short passage hands its unused
        # budget to the ones after it, and the header is measured rather than
        # assumed -- a long filename plus a section title is not a fixed cost.
        share = remaining // (len(hits) - index + 1)
        body_budget = max(0, share - ragbudget.count_tokens(header) - 4)
        body, truncated = ragbudget.truncate_to_tokens(hit.text.strip(), body_budget)
        if truncated:
            body += " …"

        piece = f"{header}\n{body}".rstrip()
        lines.append(piece)
        remaining -= ragbudget.count_tokens(piece) + 2
    return "\n\n".join(lines)


# --- read_document ----------------------------------------------------------


class ReadArgs(BaseModel):
    file_id: str = Field(description="Document id or filename to read.")
    pages: list[int] | None = Field(
        default=None, description="Page numbers to read; omit for the whole document."
    )


class ReadDocument(Tool):
    name = "read_document"
    description = "Read the text of an ingested document, optionally only the pages you name."
    args_model = ReadArgs

    def run(self, args: ReadArgs, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        try:
            import corpus  # noqa: PLC0415

            document, pages = corpus.read_document(args.file_id, args.pages)
        except KeyError:
            import ragdb  # noqa: PLC0415

            known = ", ".join(d["filename"] for d in ragdb.list_documents()[:10]) or "none"
            return _failure(
                started, f"no document matched {args.file_id!r}. Ingested: {known}"
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(started, f"read failed: {exc}")

        if not pages:
            return _failure(
                started,
                f"{document['filename']} has no extracted text for the requested pages "
                f"(document has {document['pages']} page(s)).",
            )

        body = "\n\n".join(
            f"--- {document['filename']} page {page['page']} of {document['pages']} ---\n"
            f"{page['text']}"
            for page in pages
        )
        content, raw_path = ragbudget.fit(body, f"read-{document['id'][:8]}")
        return ToolResult(
            ok=True, content=content, raw_path=raw_path, duration_ms=_timed(started)
        )


# --- create_docx ------------------------------------------------------------


class CreateDocxArgs(BaseModel):
    template: str = Field(
        default="approval_note",
        description="One of: approval_note, inspection_summary, calculation_sheet.",
    )
    title: str = Field(description="Subject line of the document.")
    sections: list[Section] = Field(
        default_factory=list, description="Sections to fill in, in order."
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Header fields such as equipment, unit, inspector.",
    )


class CreateDocx(Tool):
    name = "create_docx"
    description = "Generate a Word document from a refinery template such as an approval note."
    args_model = CreateDocxArgs

    def run(self, args: CreateDocxArgs, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        try:
            record = create_docx(
                args.template,
                args.title,
                args.sections,
                meta=args.meta,
                session_id=ctx.session_id,
                artifacts_dir=ctx.artifacts_dir or None,
            )
        except UnknownTemplate:
            return _failure(
                started,
                f"unknown template {args.template!r}. Available: "
                f"{', '.join(available_templates())}",
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(started, f"document generation failed: {exc}")

        content = (
            f"Created {record.filename} ({record.size_bytes:,} bytes) from the "
            f"{record.template} template, with {len(args.sections)} section(s). "
            f"Artifact id {record.artifact_id}."
        )
        return ToolResult(
            ok=True,
            content=content,
            raw_path=record.path,
            artifacts=[record.artifact_id],
            duration_ms=_timed(started),
        )


# --- create_xlsx ------------------------------------------------------------


class CreateXlsxArgs(BaseModel):
    sheets: list[Sheet] = Field(description="Worksheets, each with columns and rows.")
    title: str = Field(default="", description="Workbook title, used for the filename.")


class CreateXlsx(Tool):
    name = "create_xlsx"
    description = "Generate an Excel workbook from one or more sheets of columns and rows."
    args_model = CreateXlsxArgs

    def run(self, args: CreateXlsxArgs, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        if not args.sheets:
            return _failure(started, "no sheets given; provide at least one sheet")
        try:
            record = create_xlsx(
                args.sheets,
                title=args.title or None,
                session_id=ctx.session_id,
                artifacts_dir=ctx.artifacts_dir or None,
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(started, f"workbook generation failed: {exc}")

        rows = sum(len(sheet.rows) for sheet in args.sheets)
        content = (
            f"Created {record.filename} ({record.size_bytes:,} bytes) with "
            f"{len(args.sheets)} sheet(s) and {rows} data row(s). "
            f"Artifact id {record.artifact_id}."
        )
        return ToolResult(
            ok=True,
            content=content,
            raw_path=record.path,
            artifacts=[record.artifact_id],
            duration_ms=_timed(started),
        )


# --- registry ---------------------------------------------------------------

TOOLS: list[Tool] = [SearchDocuments(), ReadDocument(), CreateDocx(), CreateXlsx()]
BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


def get_tools() -> list[Tool]:
    """Every tool this service exposes. Person 3's registry calls this."""
    return list(TOOLS)


def schemas() -> list[dict]:
    """JSON schemas for the model's tool list."""
    return [tool.schema() for tool in TOOLS]


def run_tool(name: str, arguments: dict, ctx: RunContext) -> ToolResult:
    """Validate arguments and dispatch. Used by the HTTP wrapper in main.py."""
    started = time.perf_counter()
    tool = BY_NAME.get(name)
    if tool is None:
        return _failure(
            started, f"unknown tool {name!r}. Available: {', '.join(BY_NAME)}"
        )
    try:
        args = tool.args_model.model_validate(arguments or {})
    except Exception as exc:  # noqa: BLE001
        return _failure(started, f"invalid arguments for {name}: {exc}")
    return tool.run(args, ctx)


__all__ = [
    "SearchDocuments",
    "ReadDocument",
    "CreateDocx",
    "CreateXlsx",
    "TOOLS",
    "BY_NAME",
    "get_tools",
    "schemas",
    "run_tool",
]
