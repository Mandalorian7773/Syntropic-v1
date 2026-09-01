"""Test setup. Owner: person 2.

Two things have to happen before any ragsvc module is imported, and both are
done at module scope here rather than in a fixture, because `import ragconfig`
resolves paths and pins environment variables at import time -- a fixture runs
too late to change either.

1.  `ragsvc/` goes on the front of sys.path. backend/ installs a top-level
    package called `tools` too, and the wrong one being found first produces a
    confusing ImportError three files away from the cause.
2.  Every writable path is redirected into a temporary directory, so running
    the tests never touches the real workspace, artifacts or database.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAGSVC = Path(__file__).resolve().parent.parent
if sys.path[0] != str(RAGSVC):
    sys.path.insert(0, str(RAGSVC))

_TMP = Path(tempfile.mkdtemp(prefix="ragsvc-tests-"))
os.environ["WORKSPACE_DIR"] = str(_TMP / "workspace")
os.environ["ARTIFACTS_DIR"] = str(_TMP / "artifacts")
os.environ["DB_PATH"] = str(_TMP / "data" / "test.db")
os.environ["RAG_QDRANT_LOCAL"] = "1"
os.environ["RAG_QDRANT_LOCAL_PATH"] = str(_TMP / "qdrant")
# The guard patches the socket module process-wide; a test suite that opens no
# sockets does not need it, and leaving it off keeps failures legible.
os.environ["RAG_NETGUARD"] = "0"
# Ingest inline rather than through the OCR process pool. Under pytest, a
# spawned worker re-imports the parent's __main__ -- which is the pytest
# console script -- and would run the test session again inside every worker.
# The pool is exercised by eval/ingest_bench.py, which is a plain script.
os.environ["RAG_OCR_WORKERS"] = "1"

import pytest  # noqa: E402

import ragconfig as cfg  # noqa: E402
import ragdb  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_storage():
    cfg.ensure_dirs()
    ragdb.init()
    yield


@pytest.fixture
def tmp_root() -> Path:
    return _TMP


@pytest.fixture
def run_context():
    from contracts import RunContext

    return RunContext(
        session_id="test-session",
        workspace_dir=str(cfg.WORKSPACE_DIR),
        artifacts_dir=str(cfg.ARTIFACTS_DIR),
    )
