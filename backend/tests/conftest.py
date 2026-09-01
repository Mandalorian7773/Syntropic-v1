import sys
from pathlib import Path

import pytest

# Tests must run from a bare checkout too, before `make setup` has installed
# the backend package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.store import Store  # noqa: E402
from audit.logger import AuditLog  # noqa: E402
from tools.registry import Registry  # noqa: E402
from tools.files import ListFilesTool, ReadFileTool, WriteFileTool  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture()
def audit(store):
    return AuditLog(store)


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def registry():
    r = Registry()
    for tool in (ReadFileTool(), WriteFileTool(), ListFilesTool()):
        r.register(tool)
    return r
