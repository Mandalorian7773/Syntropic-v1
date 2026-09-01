#!/usr/bin/env python3
"""Validate a running backend against contracts/. Shared tool.

Point it at any host and it says, per endpoint, whether the response actually
matches the Pydantic model the frontend generates its TypeScript from. This is
the fastest way to answer "is the backend ready for the frontend yet".

    python scripts/check-backend.py http://192.168.1.10:8000

Exit 0 only if every endpoint conforms.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

from pydantic import TypeAdapter, ValidationError

from contracts import (
    CancelResponse, DocumentInfo, Event, HealthResponse, ModelInfo,
    NetworkStatus, SessionDetail, SessionSummary,
)

TIMEOUT = 10
OK, BAD, SKIP = "  PASS", "  FAIL", "  SKIP"


def call(url: str, method: str = "GET", body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return json.loads(res.read() or b"null")


def check(name: str, model: Any, url: str, method: str = "GET",
          body: dict | None = None) -> bool:
    try:
        payload = call(url, method, body)
    except urllib.error.HTTPError as e:
        print(f"{BAD}  {name}  HTTP {e.code}")
        return False
    except Exception as e:  # connection refused, timeout, bad JSON
        print(f"{BAD}  {name}  {type(e).__name__}: {e}")
        return False
    try:
        TypeAdapter(model).validate_python(payload)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "(root)"
        print(f"{BAD}  {name}  shape mismatch at `{loc}`: {first['msg']}")
        print(f"         got: {json.dumps(payload)[:180]}")
        return False
    print(f"{OK}  {name}")
    return True


def check_stream(base: str) -> bool:
    """POST /api/chat must be SSE, and every frame must be a contract event."""
    req = urllib.request.Request(
        f"{base}/api/chat", method="POST",
        data=json.dumps({"session_id": None, "message": "conformance probe"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    adapter = TypeAdapter(Event)
    seen: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            ctype = res.headers.get("Content-Type", "")
            if "text/event-stream" not in ctype:
                print(f"{BAD}  POST /api/chat  Content-Type is {ctype!r},"
                      " not text/event-stream")
                return False
            buf = ""
            for raw in res:
                buf += raw.decode("utf-8", "replace")
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    data = "\n".join(
                        line[5:].strip() for line in frame.splitlines()
                        if line.startswith("data:")
                    )
                    if not data:
                        continue
                    try:
                        seen.append(adapter.validate_python(json.loads(data)).type)
                    except (ValidationError, json.JSONDecodeError) as e:
                        print(f"{BAD}  POST /api/chat  bad frame: {data[:120]}")
                        print(f"         {e}")
                        return False
    except Exception as e:
        print(f"{BAD}  POST /api/chat  {type(e).__name__}: {e}")
        return False

    if not seen:
        print(f"{BAD}  POST /api/chat  stream produced no frames")
        return False
    if seen[0] != "session.start":
        print(f"{BAD}  POST /api/chat  first frame is {seen[0]!r},"
              " expected session.start")
        return False
    if seen[-1] != "done":
        print(f"{BAD}  POST /api/chat  last frame is {seen[-1]!r}, expected done")
        return False
    kinds = sorted(set(seen))
    print(f"{OK}  POST /api/chat  {len(seen)} frames, types: {', '.join(kinds)}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    print(f"check-backend: {base}\n")

    results = [
        check("GET  /api/health", HealthResponse, f"{base}/api/health"),
        check("GET  /api/models", list[ModelInfo], f"{base}/api/models"),
        check("GET  /api/sessions", list[SessionSummary], f"{base}/api/sessions"),
        check("GET  /api/documents", list[DocumentInfo], f"{base}/api/documents"),
        check("GET  /api/network/status", NetworkStatus,
              f"{base}/api/network/status"),
        check("POST /api/chat/cancel", CancelResponse, f"{base}/api/chat/cancel",
              "POST", {"session_id": "conformance-probe"}),
    ]

    # Session detail needs a real id, so it is conditional on the list above.
    try:
        listing = call(f"{base}/api/sessions")
        if listing:
            sid = listing[0].get("id") or listing[0].get("session_id")
            results.append(check(f"GET  /api/sessions/{sid}", SessionDetail,
                                 f"{base}/api/sessions/{sid}"))
        else:
            print(f"{SKIP}  GET  /api/sessions/{{id}}  no sessions stored yet")
    except Exception:
        print(f"{SKIP}  GET  /api/sessions/{{id}}  session list unavailable")

    results.append(check_stream(base))

    failed = results.count(False)
    print()
    if failed:
        print(f"check-backend: {failed} endpoint(s) do not match contracts/.")
        print("               Frontend will break on those. See "
              "contracts/CHANGE-PROTOCOL.md.")
        return 1
    print("check-backend: every endpoint matches contracts/. "
          "Point the frontend at it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
