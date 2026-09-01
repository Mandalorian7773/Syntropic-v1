"""Backend gateway. Owner: person 3.

Today this is a scaffold with exactly two live endpoints. It exists to prove
the SSE plumbing works end to end before any feature is written:

    GET  /api/health   real HealthResponse shape, fake values
    POST /api/chat     three hardcoded contract events over SSE

Person 3 replaces the hardcoded stream with the real router -> model manager ->
agent loop pipeline. Everything else in the contract API table is still to do;
add endpoints here and keep the request/response models coming from contracts.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from contracts import (
    ChatRequest,
    Done,
    HealthResponse,
    SessionStart,
    Token,
    to_sse,
)

app = FastAPI(title="SIH26117 backend", version="0.1.0")

MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # Real values arrive when llm/manager.py and the qdrant client exist.
    return HealthResponse(ok=True, model_loaded=None, qdrant=False, vram_free_mb=0)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id or str(uuid.uuid4())
    started = time.monotonic()

    async def stream():
        yield to_sse(SessionStart(session_id=session_id, ts=int(time.time())))
        await asyncio.sleep(0.2)
        yield to_sse(Token(text=f"scaffold backend received: {req.message}"))
        await asyncio.sleep(0.2)
        yield to_sse(
            Done(
                stop_reason="final_answer",
                steps_used=1,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
