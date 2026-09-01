-- SQLite schema. Owner: person 3.
--
-- One laptop, one file. WAL so the audit writer never blocks the reader that
-- is showing the trail on screen. The audit_log is append-only by convention
-- and by trigger: UPDATE and DELETE on it are refused at the database level,
-- because "append-only" enforced only in Python is a claim, not a property.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    task_type    TEXT,
    created_ts   INTEGER NOT NULL,
    updated_ts   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    role         TEXT NOT NULL,           -- user | assistant | tool
    content      TEXT NOT NULL,
    ts           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    call_id      TEXT NOT NULL,
    step         INTEGER NOT NULL,
    name         TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    ok           INTEGER,                 -- null until the result lands
    summary      TEXT,
    duration_ms  INTEGER,
    ts           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id, id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    filename     TEXT NOT NULL,
    mime         TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    path         TEXT NOT NULL,
    ts           INTEGER NOT NULL
);

-- Append-only evidence trail. Every prompt, route, model load, tool call,
-- tool result, artifact and error, in the order they happened. seq is the
-- monotonic ordering the reconstruction query sorts by.
CREATE TABLE IF NOT EXISTS audit_log (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    session_id   TEXT,
    kind         TEXT NOT NULL,           -- prompt | router.decision | model.loading | ...
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, seq);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Observed llama-server load times, keyed by model. Feeds the eta_s field of
-- model.loading so the UI can show an honest countdown instead of a spinner.
CREATE TABLE IF NOT EXISTS model_loads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT NOT NULL,
    load_ms      INTEGER NOT NULL,
    ts           INTEGER NOT NULL
);
