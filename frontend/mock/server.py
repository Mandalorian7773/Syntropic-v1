#!/usr/bin/env python3
"""Mock SSE server. Owner: person 1.

Standard library only, on purpose: this must run on a laptop with no venv, no
pip and no network. It emits the same three contract events the real backend
emits so the frontend can be built before the backend exists.

Person 1 expands this into four full scenarios (document Q&A, code execution,
data analysis, vision). Today it is one hardcoded happy path.

    python3 frontend/mock/server.py          # serves on :8000
"""

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000


def frame(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def scenario() -> list[tuple[float, dict]]:
    """(delay_before_frame_seconds, event) -- realistic-ish pacing."""
    sid = str(uuid.uuid4())
    return [
        (0.0, {"type": "session.start", "session_id": sid, "ts": int(time.time())}),
        (0.4, {"type": "token", "text": "Mock stream from frontend/mock/server.py."}),
        (
            0.3,
            {
                "type": "done",
                "stop_reason": "final_answer",
                "steps_used": 1,
                "tokens_in": 12,
                "tokens_out": 9,
                "latency_ms": 700,
            },
        ),
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/health":
            self.send_error(404)
            return
        body = json.dumps(
            {"ok": True, "model_loaded": None, "qdrant": False, "vram_free_mb": 0}
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/chat":
            self.send_error(404)
            return
        self.rfile.read(int(self.headers.get("Content-Length") or 0))

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for delay, event in scenario():
            time.sleep(delay)
            try:
                self.wfile.write(frame(event))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        self.close_connection = True

    def log_message(self, fmt: str, *args) -> None:
        print(f"mock: {fmt % args}")


if __name__ == "__main__":
    print(f"mock: SSE server on http://0.0.0.0:{PORT}  (POST /api/chat, GET /api/health)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
