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

import json
import re
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
                content=json.dumps({"hits": [], "note": f"nothing matched {args.query!r}"}),
                raw_path=None,
                duration_ms=_timed(started),
            )

        full = "\n\n".join(
            f"[{hit.filename} p.{hit.page}] {hit.section}\n{hit.text}".rstrip()
            for hit in result.hits
        )
        content = _hits_payload(result.hits, args.query)
        raw_path = None
        if ragbudget.count_tokens(full) > cfg.TOOL_TOKEN_BUDGET:
            raw_path = ragbudget.spill(full, "search")
        return ToolResult(
            ok=True, content=content, raw_path=raw_path, duration_ms=_timed(started)
        )


STOPWORDS = {
    "the", "and", "for", "what", "which", "was", "were", "are", "his", "her",
    "its", "from", "with", "that", "this", "into", "does", "did", "how", "why",
    "when", "who", "whom", "you", "your", "our", "has", "have", "had", "not",
    "any", "all", "can", "could", "would", "should", "will", "shall", "may",
}
TERM_RE = re.compile(r"[a-z0-9]+(?:[-_/.][a-z0-9]+)*")


def _query_terms(query: str) -> set[str]:
    """Content words from the query, with compound identifiers split as well."""
    terms: set[str] = set()
    for match in TERM_RE.findall(query.lower()):
        if len(match) > 2 and match not in STOPWORDS:
            terms.add(match)
            for part in re.split(r"[-_/.]", match):
                if len(part) > 2 and part not in STOPWORDS:
                    terms.add(part)
    return terms


def _line_score(line: str, terms: set[str]) -> int:
    """How many query terms a line carries. Exact substring, deliberately.

    Crude stemming was tried here and measured worse: clipping two characters
    off terms of six or more so that "approval" would match "approved" took
    answer-present-in-output from 29/30 to 27/30. It fixed the case it was
    written for and broke two others, because the extra loose matches reorder
    the line ranking and evict the line actually carrying the answer. A real
    stemmer might do better; a half one does not, and the harness says so.
    """
    lowered = line.lower()
    return sum(1 for term in terms if term in lowered)


def _focused_snippet(text: str, query: str, budget: int) -> str:
    """The part of a chunk most likely to hold the answer, within `budget`.

    Truncating a chunk from the front is the obvious thing and it is wrong.
    Measured case: a query for "PSV-2103 set pressure" retrieved the right
    chunk three times over, but the valve register row sits 464 characters in,
    so every snippet stopped before the answer. The model then had five
    passages, none containing what it was asked for, reissued the identical
    query, and the agent's loop detector killed the turn. Retrieval was never
    the problem; the window onto it was.

    So lines are scored against the query and the best ones kept, with two
    lines that are always kept regardless: the section heading, and a markdown
    table's header and rule -- a table row without its header is a row of
    unlabelled numbers.
    """
    if budget <= 0:
        return ""
    lines = text.splitlines()
    terms = _query_terms(query)
    scores = [_line_score(line, terms) for line in lines]

    if not terms or len(lines) < 2 or max(scores, default=0) == 0:
        body, truncated = ragbudget.truncate_to_tokens(text, budget)
        return body + (" …" if truncated else "")

    keep: dict[int, str] = {}
    used = 0

    def take(index: int, allow_window: bool = False, max_share: float = 1.0) -> bool:
        """Keep a line, or a window inside it when the whole line will not fit.

        The window is not a nicety. Layout merges a paragraph into a single
        line, so the line carrying the answer is routinely 294 tokens against a
        150-token allowance -- measured, on exactly the PSV-2103 case above.
        All-or-nothing selection drops it and returns the neighbouring headers
        instead, which looks like retrieval failing when it is presentation
        failing.

        `max_share` can cap how much of the budget one line claims, and is
        left at 1.0 because capping measured worse. The worry it addresses is
        real -- the top line on "PSV-2103 set pressure" is a 294-token prose
        paragraph *about* the valve, and starving the register row that carries
        "12.5 barg" would be a bad trade. But the windowing above already
        prevents that, and rationing on top of it costs more than it saves:

            caps 0.55 / 0.30   28/30 answers present in output
            caps 0.75 / 0.75   28/30
            no caps            29/30

        Re-measure with eval/questions.jsonl before reintroducing a cap.
        """
        nonlocal used
        if index in keep or not 0 <= index < len(lines):
            return False
        line = lines[index]
        cost = ragbudget.count_tokens(line) + 1
        ceiling = min(budget, used + max(1, int(budget * max_share)))
        if used + cost <= ceiling:
            keep[index] = line
            used += cost
            return True
        if not allow_window:
            return False
        spare = ceiling - used - 4
        if spare < 25:  # too little room to say anything useful
            return False
        keep[index] = _window(line, terms, spare)
        used += spare + 4
        return True

    # The best-matching line is reserved BEFORE anything else, including the
    # heading and the table header. Ordering this the other way round is what
    # broke the PSV-2103 case: the heading and a six-column header row ate the
    # whole allowance, and the register row carrying the answer was evicted by
    # its own table's furniture. Context is worth having; the answer is worth
    # more.
    # Ties broken by brevity, not by position. A 294-token prose paragraph
    # *about* PSV-2103 and the 40-token register row *stating* its set pressure
    # both match three query terms; sorting by position hands it to the
    # paragraph, which is discussion rather than data. Among lines matching
    # equally, the shorter one is far more likely to be the fact itself.
    widths = [ragbudget.count_tokens(line) for line in lines]
    ranked = sorted(range(len(lines)), key=lambda i: (-scores[i], widths[i], i))
    take(ranked[0], allow_window=True, max_share=1.0)
    if len(ranked) > 1 and scores[ranked[1]] > 0:
        take(ranked[1], allow_window=True, max_share=1.0)
    take(ranked[0] + 1)  # prose answers usually continue onto the next line

    for index, line in enumerate(lines):  # the section heading
        if line.strip():
            take(index)
            break
    pipes = [i for i, line in enumerate(lines) if line.lstrip().startswith("|")]
    if len(pipes) >= 3:  # header row and the --- rule beneath it
        take(pipes[0])
        take(pipes[1])

    for index in ranked[1:]:
        if scores[index] == 0:
            break
        take(index)

    out: list[str] = []
    previous: int | None = None
    for index in sorted(keep):
        if previous is not None and index > previous + 1:
            out.append("...")
        out.append(keep[index])
        previous = index
    return "\n".join(out).strip()


def _window(line: str, terms: set[str], budget: int) -> str:
    """A slice of one long line, centred on the first query term it contains."""
    lowered = line.lower()
    hits = [pos for pos in (lowered.find(term) for term in terms) if pos >= 0]
    centre = min(hits) if hits else 0

    width = max(80, int(budget * 3.4))
    start = max(0, centre - width // 3)
    end = min(len(line), start + width)
    if start:  # do not begin mid-word
        space = line.find(" ", start)
        start = space + 1 if 0 <= space < start + 30 else start
    return ("..." if start else "") + line[start:end].strip() + ("..." if end < len(line) else "")


def focused_snippet(text: str, query: str, budget: int = 90) -> str:
    """Public wrapper, shared with the /search endpoint.

    The Documents and Search panels show the same passages the agent reads, so
    they get the same query-focused window. Without this the UI falls back to
    head truncation and a judge looking at "what is the set pressure of
    PSV-2103" sees three snippets of letterhead and section headings, none
    containing a pressure -- retrieval working perfectly and looking broken.
    """
    return _focused_snippet(text, query, budget)


def _hits_payload(hits: list, query: str) -> str:
    """Hits as JSON: {"hits":[{doc_id, filename, page, section, score, snippet}]}.

    JSON rather than the numbered prose list this returned originally, because
    the agent turns tool results into `citation` events and a formatted string
    cannot carry `doc_id` or `score`. Person 3's loop parses this and refuses to
    invent the missing fields -- correctly, since the UI pins sources by doc_id
    and a fabricated one would look like it worked while silently mispointing.
    The model reads this perfectly well; the citation panel cannot read the
    alternative at all.

    The budget is shared across hits and recomputed each time, so a short
    passage hands its unused allowance to the ones after it.
    """
    if not hits:
        return json.dumps({"hits": []})

    entries: list[dict] = []
    remaining = cfg.TOOL_TOKEN_BUDGET - FRAME_TOKENS
    for index, hit in enumerate(hits):
        # Weighted by rank rather than split evenly. An even split across five
        # hits leaves about 150 tokens each, which will not hold a wide table
        # row plus its header, so every hit ends up equally useless. The top hit
        # is the one most likely to carry the answer and gets room to prove it;
        # the rest share what is left and still keep enough to be judged and
        # cited.
        share = (
            int(remaining * 0.40) if index == 0 else remaining // (len(hits) - index)
        )
        skeleton = {
            "doc_id": hit.doc_id,
            "filename": hit.filename,
            "page": hit.page,
            "section": hit.section,
            "score": round(float(hit.score), 4),
            "snippet": "",
        }
        overhead = ragbudget.count_tokens(json.dumps(skeleton, ensure_ascii=False))
        entry = dict(skeleton)
        entry["snippet"] = _focused_snippet(hit.text, query, max(0, share - overhead))
        entries.append(entry)
        remaining -= ragbudget.count_tokens(json.dumps(entry, ensure_ascii=False))
        if remaining <= 0:
            break

    payload = json.dumps({"hits": entries}, ensure_ascii=False)
    # Belt and braces: the caller's context is the thing being protected, so
    # the budget is enforced on the finished string, not assumed from the parts.
    while ragbudget.count_tokens(payload) > cfg.TOOL_TOKEN_BUDGET and len(entries) > 1:
        entries.pop()
        payload = json.dumps({"hits": entries}, ensure_ascii=False)
    return payload


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
        # A request that NAMES its pages gets a page-sized budget. Under the
        # shared 1000-token budget, page 2 of SOP-INSP-014 (a valve register)
        # was cut at ~911 tokens: "PSV-2103" survived, its "12.5 barg" did not,
        # and the agent answered with the neighbouring row's 16.4 barg -- a
        # confident wrong number with a citation attached. The gateway accepts
        # up to AGENT_MAX_CONTENT_TOKENS (2500) from a tool, so this matches it.
        # Whole-document reads keep the small budget on purpose: a 20-page
        # scanned SOP would otherwise swamp the model's context.
        budget = cfg.READ_PAGE_TOKEN_BUDGET if args.pages else None
        content, raw_path = ragbudget.fit(body, f"read-{document['id'][:8]}", budget)
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
