"""SSE plumbing beyond contracts.to_sse. Owner: person 3.

Two things live here:

  stream_events()  turns the agent loop's async iterator of contract events
                   into SSE frames, inserting `: ping` comment frames whenever
                   the model is quiet for HEARTBEAT_S -- a model swap is 5-15 s
                   of silence, and an idle TCP stream through a proxy is a
                   dropped TCP stream.

  Cancels          the session_id -> asyncio.Event map behind
                   POST /api/chat/cancel. The loop checks the event between
                   steps; cancellation is honoured at the next step boundary,
                   not mid-token, which is why cancel is "cancelled" and not
                   "killed".
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from pydantic import BaseModel

from contracts import to_sse

HEARTBEAT_S = 5.0
PING_FRAME = ": ping\n\n"


async def stream_events(
    events: AsyncIterator[BaseModel], heartbeat_s: float = HEARTBEAT_S
) -> AsyncIterator[str]:
    iterator = events.__aiter__()
    while True:
        next_event = asyncio.ensure_future(iterator.__anext__())
        while True:
            done, _ = await asyncio.wait({next_event}, timeout=heartbeat_s)
            if done:
                break
            yield PING_FRAME
        try:
            event = next_event.result()
        except StopAsyncIteration:
            return
        yield to_sse(event)


class Cancels:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def register(self, session_id: str) -> asyncio.Event:
        event = self._events.get(session_id)
        if event is None or event.is_set():
            event = asyncio.Event()
            self._events[session_id] = event
        return event

    def cancel(self, session_id: str) -> bool:
        event = self._events.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    def clear(self, session_id: str) -> None:
        self._events.pop(session_id, None)
