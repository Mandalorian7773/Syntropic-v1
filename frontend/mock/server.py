#!/usr/bin/env python3
"""Mock backend for the SIH26117 workbench frontend. Owner: person 1.

Standard library only, on purpose: this runs on a laptop with no venv, no pip
and no network. It serves every REST endpoint the SPA consumes and streams four
scripted SSE scenarios with DELIBERATELY REALISTIC timing.

The timing is the point. A UI that looks good against instant mock data looks
broken against a real 8 GB GPU that takes nine seconds to swap a model. Every
delay here is chosen to match the real thing:

    model.loading   8-10 s    a Q4_K_M 7B off an SSD onto a 8 GB card
    token           30-80 ms  what a 7B at ~20 tok/s actually feels like
    tool call       1-3 s     sandbox container start + execute
    OCR-ish tools   2-4 s     because the document scenario hits a scanned page

Scenario is chosen from the message text, so you can drive it from the composer:

    "...document..."  -> vision model, retrieval with citations, docx artifact
    "...code..."      -> model swap, execute_python fails once then succeeds
    "...fail..."      -> TOOL_TIMEOUT mid-run, then recovery
    anything else     -> simple streamed tokens, no tools

    python3 frontend/mock/server.py [--port 8000] [--fast]

--fast collapses every delay for automated checks. Never use it to judge the UI.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------

FAST = False


def hold(seconds: float) -> None:
    """Sleep, unless --fast. Jittered, because real systems are not metronomes."""
    if FAST:
        time.sleep(0.001)
        return
    time.sleep(seconds * random.uniform(0.85, 1.15))


def tokens(text: str, wpm_ms: tuple[float, float] = (0.030, 0.080)) -> Iterator[dict]:
    """Yield `token` events word by word at a believable decode rate."""
    parts = re.findall(r"\S+\s*", text)
    for part in parts:
        hold(random.uniform(*wpm_ms))
        yield {"type": "token", "text": part}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

MODELS = [
    {
        "id": "qwen2.5-vl-7b",
        "capabilities": ["general", "document", "vision", "data"],
        "context": 16384,
        "vram_mb": 5600,
        "loaded": True,
    },
    {
        "id": "qwen3-coder-8b",
        "capabilities": ["general", "code"],
        "context": 16384,
        "vram_mb": 5100,
        "loaded": False,
    },
]

DOCUMENTS = [
    {"doc_id": "d7", "filename": "SOP-014-Pressure-Vessel-Inspection.pdf",
     "pages": 34, "chunks": 212, "ingested_at": 1788200000, "status": "indexed",
     "size_bytes": 4_182_233},
    {"doc_id": "d3", "filename": "MRPL-Hot-Work-Permit-Procedure.pdf",
     "pages": 12, "chunks": 78, "ingested_at": 1788203400, "status": "indexed",
     "size_bytes": 1_204_881},
    {"doc_id": "d9", "filename": "Crude-Unit-Turnaround-Log-2025.xlsx",
     "pages": 4, "chunks": 41, "ingested_at": 1788210000, "status": "indexed",
     "size_bytes": 823_004},
    {"doc_id": "d11", "filename": "Scanned-Thickness-Survey-B-Train.pdf",
     "pages": 18, "chunks": 96, "ingested_at": 1788240000, "status": "indexed",
     "size_bytes": 9_442_118},
    {"doc_id": "d12", "filename": "Vendor-Datasheet-Rotary-Pump-P-101.pdf",
     "pages": 6, "chunks": 0, "ingested_at": 1788248800, "status": "ingesting",
     "size_bytes": 2_118_440},
]

SESSIONS = [
    {"id": "s-1041", "title": "Wall loss limits, B-train thickness survey",
     "created_at": 1788240000, "message_count": 8},
    {"id": "s-1038", "title": "Hot work permit sign-off chain",
     "created_at": 1788198000, "message_count": 4},
    {"id": "s-1032", "title": "Turnaround log — downtime by unit",
     "created_at": 1788110000, "message_count": 12},
    {"id": "s-1027", "title": "P-101 seal flush plan review",
     "created_at": 1788040000, "message_count": 6},
]

# Server start time, so /api/network/status can report a growing `since`.
STARTED = int(time.time())

# Artifacts produced during this process's lifetime, served by /api/artifacts/{id}.
ARTIFACTS: dict[str, dict[str, Any]] = {}


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


def scenario_document(session_id: str) -> Iterator[dict]:
    """Vision model, read + search with citations, then a .docx artifact."""
    t0 = time.time()
    yield {"type": "session.start", "session_id": session_id, "ts": int(time.time())}
    hold(0.5)

    yield {
        "type": "router.decision",
        "model_id": "qwen2.5-vl-7b",
        "task_type": "document",
        "confidence": 0.91,
        "reason": "attachment is a scanned PDF; query asks for values from a table",
        "alternatives": ["qwen3-coder-8b"],
    }
    hold(0.6)
    yield {"type": "model.ready", "model_id": "qwen2.5-vl-7b",
           "load_ms": 0, "vram_mb": 5600}
    hold(0.3)

    # Step 1 -- read the document
    yield {"type": "agent.step", "step": 1, "max_steps": 10}
    yield from tokens("Reading the thickness survey to find the acceptance limit. ")
    yield {"type": "tool.call", "call_id": "c1", "name": "read_document",
           "args": {"doc_id": "d11", "pages": "12-14"}}
    hold(3.2)  # OCR on a scanned page is slow, and the UI must survive it
    yield {"type": "tool.result", "call_id": "c1", "ok": True,
           "summary": "3 pages, 1,412 tokens of text, 2 tables recovered via OCR",
           "duration_ms": 3180, "truncated": True}
    hold(0.4)

    # Step 2 -- retrieval
    yield {"type": "agent.step", "step": 2, "max_steps": 10}
    yield from tokens("Now cross-checking against the governing SOP. ")
    yield {"type": "tool.call", "call_id": "c2", "name": "search_documents",
           "args": {"query": "maximum permissible wall loss pressure vessel",
                    "top_k": 5}}
    hold(1.4)
    yield {"type": "tool.result", "call_id": "c2", "ok": True,
           "summary": "5 hits across 2 documents, top score 0.87",
           "duration_ms": 1370, "truncated": False}
    hold(0.3)

    for cit in (
        {"doc_id": "d7", "filename": "SOP-014-Pressure-Vessel-Inspection.pdf",
         "page": 4, "score": 0.87,
         "snippet": "max permissible wall loss is 20% of nominal thickness, "
                    "measured at the thinnest point of any 100 mm grid square"},
        {"doc_id": "d7", "filename": "SOP-014-Pressure-Vessel-Inspection.pdf",
         "page": 9, "score": 0.81,
         "snippet": "where loss exceeds 12%, inspection interval shall be "
                    "reduced to 18 months and recorded on Form PV-7"},
        {"doc_id": "d11", "filename": "Scanned-Thickness-Survey-B-Train.pdf",
         "page": 13, "score": 0.74,
         "snippet": "Shell course 3, grid E4: 11.2 mm against 14.0 mm nominal"},
    ):
        hold(0.25)
        yield {"type": "citation", **cit}

    hold(0.4)
    yield from tokens(
        "\n\n## Finding\n\n"
        "Grid **E4 on shell course 3** measures 11.2 mm against a 14.0 mm nominal "
        "thickness. That is a **20.0% wall loss**, which sits exactly on the "
        "acceptance limit in SOP-014 §4.2.\n\n"
        "| Location | Nominal | Measured | Loss | Status |\n"
        "|---|---|---|---|---|\n"
        "| Course 3, E4 | 14.0 mm | 11.2 mm | 20.0% | At limit |\n"
        "| Course 3, E5 | 14.0 mm | 12.6 mm | 10.0% | Acceptable |\n"
        "| Course 2, C2 | 16.0 mm | 15.1 mm | 5.6% | Acceptable |\n\n"
        "Because E4 is at the limit and two adjacent squares exceed 12%, "
        "§4.7 requires the inspection interval to drop to 18 months and a "
        "Form PV-7 entry. Drafting the approval note now.\n\n"
    )

    # Step 3 -- artifact
    yield {"type": "agent.step", "step": 3, "max_steps": 10}
    yield {"type": "tool.call", "call_id": "c3", "name": "create_docx",
           "args": {"template": "approval-note",
                    "title": "Inspection Interval Reduction — B-Train Shell Course 3",
                    "fields": {"vessel": "V-2103", "grid": "E4",
                               "loss_pct": 20.0, "next_interval_months": 18}}}
    hold(2.1)
    art_id = "a3"
    ARTIFACTS[art_id] = {"filename": "approval-note.docx", "mime": DOCX_MIME,
                         "size_bytes": 18422}
    yield {"type": "tool.result", "call_id": "c3", "ok": True,
           "summary": "wrote approval-note.docx (18.0 KB, 2 pages)",
           "duration_ms": 2090, "truncated": False}
    hold(0.3)
    yield {"type": "artifact", "artifact_id": art_id,
           "filename": "approval-note.docx", "mime": DOCX_MIME,
           "size_bytes": 18422, "url": f"/api/artifacts/{art_id}"}

    hold(0.4)
    yield from tokens(
        "The approval note is ready for the inspection engineer's signature. "
        "It cites SOP-014 §4.2 and §4.7 and records the E4 measurement."
    )

    yield {"type": "done", "stop_reason": "final_answer", "steps_used": 3,
           "tokens_in": 3120, "tokens_out": 880,
           "latency_ms": int((time.time() - t0) * 1000)}


def scenario_code(session_id: str) -> Iterator[dict]:
    """Model swap, then execute_python failing once and succeeding on retry."""
    t0 = time.time()
    yield {"type": "session.start", "session_id": session_id, "ts": int(time.time())}
    hold(0.5)

    yield {
        "type": "router.decision",
        "model_id": "qwen3-coder-8b",
        "task_type": "code",
        "confidence": 0.88,
        "reason": "request asks for a script; coder model scores higher on HumanEval",
        "alternatives": ["qwen2.5-vl-7b"],
    }
    hold(0.5)

    # The swap. Nine seconds of visible dead air if the UI does not handle it.
    yield {"type": "model.loading", "model_id": "qwen3-coder-8b",
           "evicting": "qwen2.5-vl-7b", "eta_s": 9}
    hold(9.4)  # deliberately slightly OVER eta_s, so the UI must handle overrun
    yield {"type": "model.ready", "model_id": "qwen3-coder-8b",
           "load_ms": 9420, "vram_mb": 5100}
    hold(0.4)

    yield {"type": "agent.step", "step": 1, "max_steps": 10}
    yield from tokens(
        "I'll parse the turnaround log and total the downtime per unit.\n\n"
        "```python\n"
        "import pandas as pd\n"
        "df = pd.read_excel('Crude-Unit-Turnaround-Log-2025.xlsx')\n"
        "print(df.groupby('unit')['downtime_hrs'].sum())\n"
        "```\n\n"
    )
    yield {"type": "tool.call", "call_id": "c1", "name": "execute_python",
           "args": {"code": "import pandas as pd\n"
                            "df = pd.read_excel('Crude-Unit-Turnaround-Log-2025.xlsx')\n"
                            "print(df.groupby('unit')['downtime_hrs'].sum())",
                    "timeout_s": 30}}
    hold(2.4)
    yield {"type": "tool.result", "call_id": "c1", "ok": False,
           "summary": "exit 1, KeyError: 'downtime_hrs'  "
                      "(columns are: unit, start, end, hours_down, reason)",
           "duration_ms": 2380, "truncated": False}
    hold(0.5)

    yield {"type": "agent.step", "step": 2, "max_steps": 10}
    yield from tokens(
        "Column is named `hours_down`, not `downtime_hrs`. Retrying.\n\n"
    )
    yield {"type": "tool.call", "call_id": "c2", "name": "execute_python",
           "args": {"code": "import pandas as pd\n"
                            "df = pd.read_excel('Crude-Unit-Turnaround-Log-2025.xlsx')\n"
                            "out = df.groupby('unit')['hours_down'].sum()\\\n"
                            "        .sort_values(ascending=False)\n"
                            "print(out.to_string())",
                    "timeout_s": 30}}
    hold(1.9)
    yield {"type": "tool.result", "call_id": "c2", "ok": True,
           "summary": "exit 0, stdout: CDU-1  412.5 / VDU-2  288.0 / "
                      "FCC-1  201.5 / HDT-3  96.0",
           "duration_ms": 1870, "truncated": False}
    hold(0.4)

    yield from tokens(
        "\n\n**Total downtime by unit, 2025 turnaround:**\n\n"
        "| Unit | Hours down |\n|---|---:|\n"
        "| CDU-1 | 412.5 |\n| VDU-2 | 288.0 |\n"
        "| FCC-1 | 201.5 |\n| HDT-3 | 96.0 |\n\n"
        "CDU-1 accounts for 41% of the total. The first run failed on a column "
        "name; the log uses `hours_down`."
    )
    yield {"type": "done", "stop_reason": "final_answer", "steps_used": 2,
           "tokens_in": 1840, "tokens_out": 612,
           "latency_ms": int((time.time() - t0) * 1000)}


def scenario_failure(session_id: str) -> Iterator[dict]:
    """A TOOL_TIMEOUT mid-run, then recovery on a narrower query."""
    t0 = time.time()
    yield {"type": "session.start", "session_id": session_id, "ts": int(time.time())}
    hold(0.5)

    yield {"type": "router.decision", "model_id": "qwen2.5-vl-7b",
           "task_type": "data", "confidence": 0.63,
           "reason": "tabular aggregation over an uploaded workbook; "
                     "confidence low, coder was close",
           "alternatives": ["qwen3-coder-8b"]}
    hold(0.5)
    yield {"type": "model.ready", "model_id": "qwen2.5-vl-7b",
           "load_ms": 0, "vram_mb": 5600}
    hold(0.3)

    yield {"type": "agent.step", "step": 1, "max_steps": 10}
    yield from tokens("Scanning every page of the survey for corroded grid squares. ")
    yield {"type": "tool.call", "call_id": "c1", "name": "execute_python",
           "args": {"code": "# full-document OCR sweep, all 18 pages\n"
                            "for page in range(18):\n"
                            "    ocr_and_parse(page)  # ~2s each",
                    "timeout_s": 30}}
    hold(4.0)
    yield {"type": "error", "code": "TOOL_TIMEOUT",
           "message": "execute_python exceeded 30s", "recoverable": True}
    hold(0.3)
    yield {"type": "tool.result", "call_id": "c1", "ok": False,
           "summary": "killed after 30000 ms; partial output discarded",
           "duration_ms": 30000, "truncated": True}
    hold(0.6)

    yield {"type": "agent.step", "step": 2, "max_steps": 10}
    yield from tokens(
        "That sweep was too broad for the 30 s sandbox budget. Narrowing to the "
        "three pages the index already flags as containing thickness tables.\n\n"
    )
    yield {"type": "tool.call", "call_id": "c2", "name": "search_documents",
           "args": {"query": "thickness table shell course", "top_k": 3}}
    hold(1.2)
    yield {"type": "tool.result", "call_id": "c2", "ok": True,
           "summary": "3 hits, pages 12-14 of Scanned-Thickness-Survey-B-Train.pdf",
           "duration_ms": 1190, "truncated": False}
    hold(0.3)
    yield {"type": "citation", "doc_id": "d11",
           "filename": "Scanned-Thickness-Survey-B-Train.pdf", "page": 13,
           "score": 0.79,
           "snippet": "Shell course 3, grid E4: 11.2 mm against 14.0 mm nominal"}
    hold(0.4)

    yield {"type": "agent.step", "step": 3, "max_steps": 10}
    yield {"type": "tool.call", "call_id": "c3", "name": "execute_python",
           "args": {"code": "parse_thickness_tables(pages=[12, 13, 14])",
                    "timeout_s": 30}}
    hold(2.2)
    yield {"type": "tool.result", "call_id": "c3", "ok": True,
           "summary": "exit 0, 3 tables, 96 grid squares, 4 above 12% loss",
           "duration_ms": 2160, "truncated": False}
    hold(0.4)

    yield from tokens(
        "\n\nRecovered. **4 of 96 grid squares** exceed 12% wall loss, all on "
        "shell course 3. The first attempt timed out because it tried to OCR all "
        "18 pages inside a 30 s sandbox budget; the retrieval index already knew "
        "which three pages mattered."
    )
    yield {"type": "done", "stop_reason": "final_answer", "steps_used": 3,
           "tokens_in": 2410, "tokens_out": 704,
           "latency_ms": int((time.time() - t0) * 1000)}


def scenario_simple(session_id: str) -> Iterator[dict]:
    """No tools. Just a routed model streaming tokens."""
    t0 = time.time()
    yield {"type": "session.start", "session_id": session_id, "ts": int(time.time())}
    hold(0.4)
    yield {"type": "router.decision", "model_id": "qwen2.5-vl-7b",
           "task_type": "general", "confidence": 0.77,
           "reason": "no attachment, no code request; generalist is already resident",
           "alternatives": ["qwen3-coder-8b"]}
    hold(0.4)
    yield {"type": "model.ready", "model_id": "qwen2.5-vl-7b",
           "load_ms": 0, "vram_mb": 5600}
    hold(0.3)
    yield {"type": "agent.step", "step": 1, "max_steps": 10}
    yield from tokens(
        "A hot work permit at MRPL is signed off by three people, in order:\n\n"
        "1. **The area authority** — the operations supervisor for the unit, who "
        "confirms the equipment is isolated, drained and gas-freed.\n"
        "2. **The safety officer** — who verifies the gas test reading is below "
        "1% LEL and that fire watch is posted.\n"
        "3. **The performing authority** — the maintenance supervisor whose crew "
        "does the work, who countersigns that the crew has been briefed.\n\n"
        "The permit is valid for one shift and must be re-tested and re-signed if "
        "work continues past it.\n\n"
        "> This is the general procedure. For the controlling document, ask me to "
        "search the SOPs and I will cite the page."
    )
    yield {"type": "done", "stop_reason": "final_answer", "steps_used": 1,
           "tokens_in": 420, "tokens_out": 198,
           "latency_ms": int((time.time() - t0) * 1000)}


SCENARIOS = {
    "document": scenario_document,
    "code": scenario_code,
    "failure": scenario_failure,
    "simple": scenario_simple,
}


def pick_scenario(message: str) -> str:
    """Route the composer text to a scenario so the demo can be driven by typing."""
    m = message.lower()
    if any(w in m for w in ("fail", "timeout", "error", "recover")):
        return "failure"
    if any(w in m for w in ("code", "python", "script", "downtime", "plot", "chart")):
        return "code"
    if any(w in m for w in ("document", "sop", "wall loss", "thickness", "pdf",
                            "cite", "citation", "report", "docx", "approval")):
        return "document"
    return "simple"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

# Set by POST /api/chat/cancel, read by the streaming loop.
CANCELLED: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "sih26117-mock/1.0"

    # -- helpers ----------------------------------------------------------

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes -----------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/api/health":
            return self._json({"ok": True, "model_loaded": "qwen2.5-vl-7b",
                               "qdrant": True, "vram_free_mb": 2400})

        if path == "/api/models":
            return self._json(MODELS)

        if path == "/api/sessions":
            return self._json(SESSIONS)

        if path.startswith("/api/sessions/"):
            sid = path.rsplit("/", 1)[-1]
            return self._json({
                "id": sid,
                "messages": [
                    {"role": "user", "ts": 1788240000,
                     "content": "What is the maximum permissible wall loss?"},
                    {"role": "assistant", "ts": 1788240042,
                     "content": "SOP-014 §4.2 sets it at 20% of nominal thickness."},
                ],
                "steps": [
                    {"step": 1, "tool": "search_documents",
                     "args": {"query": "wall loss", "top_k": 5}, "ok": True,
                     "summary": "5 hits, top score 0.87", "duration_ms": 1370},
                ],
            })

        if path == "/api/documents":
            return self._json(DOCUMENTS)

        if path == "/api/network/status":
            # rules_active stays true and the counters stay zero. That is the
            # whole claim. If this ever returns non-zero, the UI must go red.
            return self._json({"external_packets": 0, "dns_queries": 0,
                               "since": STARTED, "rules_active": True})

        if path.startswith("/api/artifacts/"):
            art_id = path.rsplit("/", 1)[-1]
            meta = ARTIFACTS.get(art_id, {"filename": f"{art_id}.bin",
                                          "mime": "application/octet-stream",
                                          "size_bytes": 1024})
            # A real file would come from ARTIFACTS_DIR. A placeholder byte blob
            # is enough to prove the download path works end to end.
            body = (f"Mock artifact {art_id} — {meta['filename']}\n"
                    "Person 2's ragsvc writes the real .docx here.\n"
                    ).encode() + b"\0" * 256
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", meta["mime"])
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{meta["filename"]}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)  # type: ignore[func-returns-value]

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/api/chat/cancel":
            body = self._read_json()
            sid = str(body.get("session_id") or "")
            CANCELLED.add(sid)
            return self._json({"ok": True})

        if path == "/api/documents/upload":
            # Do not parse the multipart body; the frontend only needs the shape.
            self._read_json()
            return self._json({"file_id": f"d{random.randint(20, 99)}",
                               "filename": "uploaded.pdf", "pages": 12,
                               "status": "queued"})

        if path == "/api/chat":
            return self._chat()

        self.send_error(404)

    # -- the stream -------------------------------------------------------

    def _chat(self) -> None:
        body = self._read_json()
        message = str(body.get("message") or "")
        session_id = str(body.get("session_id") or uuid.uuid4())
        name = pick_scenario(message)
        CANCELLED.discard(session_id)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        print(f"mock: scenario={name!r} session={session_id[:8]} msg={message[:48]!r}")

        steps = 0
        try:
            for event in SCENARIOS[name](session_id):
                if session_id in CANCELLED:
                    self._frame({"type": "done", "stop_reason": "cancelled",
                                 "steps_used": steps, "tokens_in": 0,
                                 "tokens_out": 0, "latency_ms": 0})
                    print(f"mock: session {session_id[:8]} cancelled")
                    break
                if event["type"] == "agent.step":
                    steps = event["step"]
                self._frame(event)
        except (BrokenPipeError, ConnectionResetError):
            print(f"mock: client disconnected from {session_id[:8]}")
        finally:
            self.close_connection = True

    def _frame(self, event: dict) -> None:
        payload = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        self.wfile.write(payload.encode())
        self.wfile.flush()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request would drown out the scenario logging above.
        pass


def main() -> None:
    global FAST
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--fast", action="store_true",
                    help="collapse all delays (for automated checks only)")
    args = ap.parse_args()
    FAST = args.fast

    print(f"mock: http://0.0.0.0:{args.port}"
          f"{'  [FAST -- timings are not representative]' if FAST else ''}")
    print("mock: scenarios -> type 'document', 'code', 'fail', or anything else")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
