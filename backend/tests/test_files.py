"""File tools: the confinement boundary is the thing under test."""

import os

import pytest

from contracts import RunContext
from tools.files import (
    ListFilesTool, PathEscapeError, ReadFileTool, WriteFileTool, safe_path,
)


@pytest.fixture()
def ctx(workspace, tmp_path):
    return RunContext(session_id="s1", workspace_dir=str(workspace),
                      artifacts_dir=str(tmp_path / "artifacts"))


def test_roundtrip(ctx):
    write = WriteFileTool().run(WriteFileTool.args_model(path="a/b.txt", content="hello"), ctx)
    assert write.ok
    read = ReadFileTool().run(ReadFileTool.args_model(path="a/b.txt"), ctx)
    assert read.ok and read.content == "hello"
    listing = ListFilesTool().run(ListFilesTool.args_model(path="a"), ctx)
    assert listing.ok and "b.txt" in listing.content


@pytest.mark.parametrize("bad", ["../outside.txt", "a/../../x", "..", "a/../.."])
def test_relative_escape_refused(ctx, bad):
    result = WriteFileTool().run(WriteFileTool.args_model(path=bad, content="x"), ctx)
    assert not result.ok and "escapes" in (result.error or "")


def test_absolute_path_refused(ctx, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    result = ReadFileTool().run(ReadFileTool.args_model(path=str(outside)), ctx)
    # An absolute path that resolves outside the workspace must be refused;
    # one already inside is fine to accept.
    assert not result.ok


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_symlink_escape_refused(ctx, workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (workspace / "link.txt").symlink_to(outside)
    with pytest.raises(PathEscapeError):
        safe_path(str(workspace), "link.txt")


def test_read_truncates_to_contract(ctx):
    WriteFileTool().run(WriteFileTool.args_model(path="big.txt", content="x" * 10000), ctx)
    result = ReadFileTool().run(ReadFileTool.args_model(path="big.txt"), ctx)
    assert result.ok
    assert len(result.content) <= 4100  # 1000 tokens + truncation notice
    assert result.raw_path  # the rest is reachable, not lost


def test_missing_file_is_error_not_crash(ctx):
    result = ReadFileTool().run(ReadFileTool.args_model(path="nope.txt"), ctx)
    assert not result.ok and result.error
