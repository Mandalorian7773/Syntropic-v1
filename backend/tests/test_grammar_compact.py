"""Grammar builder and context compaction."""

from agent.compact import CHARS_PER_TOKEN, compact, used_fraction
from agent.grammar import build_grammar


def test_grammar_names_are_the_only_utterable_tools():
    g = build_grammar(["read_file", "execute_python"])
    assert '"\\"read_file\\""' in g
    assert '"\\"execute_python\\""' in g
    assert "root ::= toolcall | final" in g
    # Rule structure: tool name is an alternation of literals, not free string.
    toolname_line = next(l for l in g.splitlines() if l.startswith("toolname"))
    assert "string" not in toolname_line


def test_grammar_with_no_tools_only_allows_final():
    g = build_grammar([])
    assert "toolcall" not in g and '"\\"final\\""' in g


def test_used_fraction_estimates():
    messages = [{"role": "user", "content": "x" * (CHARS_PER_TOKEN * 1000)}]
    assert abs(used_fraction(messages, 2000) - 0.5) < 0.01


def test_compact_preserves_head_and_tail():
    messages = (
        [{"role": "system", "content": "SYS"},
         {"role": "user", "content": "QUESTION"}]
        + [{"role": "user", "content": f"Observation: tool output {i} " + "z" * 500}
           for i in range(8)]
        + [{"role": "assistant", "content": "LATEST"}]
    )
    out = compact(messages)
    assert out[0]["content"] == "SYS"
    assert out[1]["content"] == "QUESTION"
    assert out[-1]["content"] == "LATEST"
    assert len(out) < len(messages)
    digest = out[2]
    assert "compacted" in digest["content"]


def test_compact_is_idempotent():
    messages = (
        [{"role": "system", "content": "SYS"},
         {"role": "user", "content": "Q"}]
        + [{"role": "user", "content": "o" * 400} for _ in range(10)]
    )
    once = compact(messages)
    twice = compact(once)
    assert len(twice) <= len(once)


def test_compact_shrinks_token_estimate():
    messages = (
        [{"role": "system", "content": "SYS"},
         {"role": "user", "content": "Q"}]
        + [{"role": "user", "content": "long tool output " * 200} for _ in range(6)]
    )
    before = used_fraction(messages, 16384)
    after = used_fraction(compact(messages), 16384)
    assert after < before
