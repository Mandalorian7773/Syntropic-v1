"""Registry: the 8-tool cap, validation at the boundary, truncation backstop."""

import pytest
from pydantic import BaseModel

from contracts import RunContext, Tool, ToolResult
from tools.registry import MAX_TOOLS, Registry


class NoArgs(BaseModel):
    pass


def make_tool(tool_name: str, behaviour=None):
    class T(Tool):
        name = tool_name
        description = "A test tool that does test things."
        args_model = NoArgs

        def run(self, args, ctx):
            if behaviour:
                return behaviour()
            return ToolResult(ok=True, content="fine", duration_ms=1)

    return T()


@pytest.fixture()
def ctx(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return RunContext(session_id="s", workspace_dir=str(ws), artifacts_dir=str(tmp_path))


def test_hard_cap_is_eight():
    r = Registry()
    for i in range(MAX_TOOLS):
        r.register(make_tool(f"tool_{i}"))
    with pytest.raises(ValueError, match="cap"):
        r.register(make_tool("one_too_many"))


def test_duplicate_name_refused():
    r = Registry()
    r.register(make_tool("dup"))
    with pytest.raises(ValueError, match="duplicate"):
        r.register(make_tool("dup"))


def test_unknown_tool_is_error_result(ctx):
    result = Registry().execute("ghost", {}, ctx)
    assert not result.ok and "unknown tool" in result.error


def test_bad_args_rejected_before_run(ctx):
    class Args(BaseModel):
        path: str

    class T(Tool):
        name = "needs_path"
        description = "Needs a path argument."
        args_model = Args

        def run(self, args, ctx):  # pragma: no cover - must not be reached
            raise AssertionError("run() must not execute on bad args")

    r = Registry()
    r.register(T())
    result = r.execute("needs_path", {"wrong": 1}, ctx)
    assert not result.ok and "bad args" in result.error


def test_tool_exception_becomes_error_result(ctx):
    def boom():
        raise RuntimeError("kaput")

    r = Registry()
    r.register(make_tool("bomb", boom))
    result = r.execute("bomb", {}, ctx)
    assert not result.ok and "kaput" in result.error


def test_oversized_content_truncated_with_raw_path(ctx):
    def huge():
        return ToolResult(ok=True, content="y" * 20000, duration_ms=1)

    r = Registry()
    r.register(make_tool("bigmouth", huge))
    result = r.execute("bigmouth", {}, ctx)
    assert result.ok
    assert len(result.content) < 20000
    assert result.raw_path and "truncated" in result.content


def test_prompt_block_lists_all_tools():
    r = Registry()
    r.register(make_tool("alpha_tool"))
    r.register(make_tool("beta_tool"))
    block = r.prompt_block()
    assert "alpha_tool" in block and "beta_tool" in block
