"""Store + audit: append-only is a database property, reconstruction works."""

import sqlite3

import pytest

from contracts import SessionStart


def test_session_roundtrip(store):
    store.ensure_session("s1", "hello world")
    store.add_message("s1", "user", "hello")
    store.add_message("s1", "assistant", "hi")
    detail = store.get_session("s1")
    assert detail["title"] == "hello world"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert store.list_sessions()[0]["message_count"] == 2


def test_audit_is_append_only_at_db_level(store):
    store.audit("prompt", {"q": "hi"}, "s1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE audit_log SET kind='tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM audit_log")


def test_audit_event_mirroring(store, audit):
    event = SessionStart(session_id="s9", ts=123)
    audit.event(event, "s9")
    trail = audit.trail("s9")
    assert trail[0]["kind"] == "session.start"
    assert '"ts": 123' in trail[0]["payload_json"]


def test_full_session_reconstructable_in_order(store, audit):
    """Acceptance criterion 7 in miniature: the trail alone tells the story."""
    audit.record("prompt", {"message": "fix the pump report"}, "s1")
    audit.record("router.decision", {"model_id": "m"}, "s1")
    audit.record("model.loading", {"evicting": None}, "s1")
    audit.record("model.ready", {"load_ms": 8000}, "s1")
    for i in range(6):
        audit.record("tool.call", {"name": f"t{i}"}, "s1")
        audit.record("tool.result", {"ok": True}, "s1")
    audit.record("done", {"stop_reason": "final_answer"}, "s1")
    kinds = [r["kind"] for r in audit.trail("s1")]
    assert kinds[0] == "prompt" and kinds[-1] == "done"
    assert kinds.count("tool.call") == 6
    seqs = [r["seq"] for r in audit.trail("s1")]
    assert seqs == sorted(seqs)


def test_load_estimates_learn(store):
    assert store.estimate_load_s("m1", default_s=10) == 10  # nothing observed yet
    store.record_load("m1", 8400)
    store.record_load("m1", 9100)
    assert store.estimate_load_s("m1") == 9  # mean of observed, rounded
