"""Append-only audit log. Owner: person 3.

Thin wrapper over db.store.Store.audit that also mirrors every SSE event into
the trail. The rule the whole module exists for: the row is committed BEFORE
the event leaves the process, so the trail can never claim less than the UI
showed. Acceptance criterion 7 -- a full session reconstructable from the
audit table alone -- is a query over what this writes.
"""

from __future__ import annotations

from pydantic import BaseModel

from db.store import Store


class AuditLog:
    def __init__(self, store: Store) -> None:
        self._store = store

    def event(self, event: BaseModel, session_id: str | None = None) -> None:
        """Persist any contract SSE event verbatim, keyed by its `type`."""
        kind = getattr(event, "type", event.__class__.__name__)
        self._store.audit(kind, event.model_dump(), session_id)

    def record(self, kind: str, payload: dict, session_id: str | None = None) -> None:
        """Persist a non-SSE fact: the incoming prompt, a startup check, an eviction."""
        self._store.audit(kind, payload, session_id)

    def trail(self, session_id: str | None = None, limit: int = 500) -> list[dict]:
        return self._store.audit_trail(session_id, limit)
