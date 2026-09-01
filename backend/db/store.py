"""SQLite data access. Owner: person 3.

One connection per Store, serialized writes, WAL. Everything the API serves
about past sessions comes from here, and everything the agent loop persists
goes through here -- each step is committed before the next begins, so a crash
mid-run still leaves a complete trail.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def _now() -> int:
    return int(time.time())


class Store:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + our own lock: FastAPI handlers run on a
        # threadpool, and sqlite3's default guard is per-thread, not per-lock.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA.read_text())
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid or 0

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- sessions -------------------------------------------------------------

    def ensure_session(self, session_id: str, title: str = "") -> None:
        now = _now()
        self._write(
            "INSERT INTO sessions (session_id, title, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET updated_ts = excluded.updated_ts",
            (session_id, title[:80], now, now),
        )

    def set_task_type(self, session_id: str, task_type: str) -> None:
        self._write(
            "UPDATE sessions SET task_type = ?, updated_ts = ? WHERE session_id = ?",
            (task_type, _now(), session_id),
        )

    def list_sessions(self) -> list[dict]:
        rows = self._read(
            "SELECT s.*, COUNT(m.id) AS message_count FROM sessions s "
            "LEFT JOIN messages m ON m.session_id = s.session_id "
            "GROUP BY s.session_id ORDER BY s.updated_ts DESC"
        )
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        rows = self._read("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        if not rows:
            return None
        session = dict(rows[0])
        session["messages"] = [
            dict(r)
            for r in self._read(
                "SELECT role, content, ts FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
        ]
        return session

    # --- messages / tool calls / artifacts ------------------------------------

    def add_message(self, session_id: str, role: str, content: str) -> None:
        now = _now()
        self._write(
            "INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )
        self._write(
            "UPDATE sessions SET updated_ts = ? WHERE session_id = ?", (now, session_id)
        )

    def add_tool_call(
        self, session_id: str, call_id: str, step: int, name: str, args: dict
    ) -> None:
        self._write(
            "INSERT INTO tool_calls (session_id, call_id, step, name, args_json, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, call_id, step, name, json.dumps(args), _now()),
        )

    def finish_tool_call(
        self, session_id: str, call_id: str, ok: bool, summary: str, duration_ms: int
    ) -> None:
        self._write(
            "UPDATE tool_calls SET ok = ?, summary = ?, duration_ms = ? "
            "WHERE session_id = ? AND call_id = ?",
            (int(ok), summary, duration_ms, session_id, call_id),
        )

    def add_artifact(
        self,
        artifact_id: str,
        session_id: str,
        filename: str,
        mime: str,
        size_bytes: int,
        path: str,
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO artifacts "
            "(artifact_id, session_id, filename, mime, size_bytes, path, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, session_id, filename, mime, size_bytes, path, _now()),
        )

    def get_steps(self, session_id: str) -> list[dict]:
        """Completed tool calls in order, for rehydrating the frontend's trace
        panel (contracts.SessionStep) when a past session is reopened."""
        return [
            dict(r)
            for r in self._read(
                "SELECT step, name, args_json, ok, summary, duration_ms "
                "FROM tool_calls WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
        ]

    def get_artifact(self, artifact_id: str) -> dict | None:
        rows = self._read("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return dict(rows[0]) if rows else None

    # --- audit ----------------------------------------------------------------

    def audit(self, kind: str, payload: dict, session_id: str | None = None) -> None:
        self._write(
            "INSERT INTO audit_log (ts, session_id, kind, payload_json) VALUES (?, ?, ?, ?)",
            (_now(), session_id, kind, json.dumps(payload, default=str)),
        )

    def audit_trail(self, session_id: str | None = None, limit: int = 500) -> list[dict]:
        if session_id:
            rows = self._read(
                "SELECT * FROM audit_log WHERE session_id = ? ORDER BY seq LIMIT ?",
                (session_id, limit),
            )
        else:
            rows = self._read("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # --- model load history (feeds model.loading eta_s) -----------------------

    def record_load(self, model_id: str, load_ms: int) -> None:
        self._write(
            "INSERT INTO model_loads (model_id, load_ms, ts) VALUES (?, ?, ?)",
            (model_id, load_ms, _now()),
        )

    def estimate_load_s(self, model_id: str, default_s: int = 10) -> int:
        rows = self._read(
            "SELECT load_ms FROM model_loads WHERE model_id = ? ORDER BY id DESC LIMIT 3",
            (model_id,),
        )
        if not rows:
            return default_s
        avg_ms = sum(r["load_ms"] for r in rows) / len(rows)
        return max(1, round(avg_ms / 1000))
