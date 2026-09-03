"""The two rewrites execute_python applies to the model's code before it runs.

Both exist because the model was resending the SAME broken program three
times and dying on TOOL_RETRIES_EXCEEDED for code that was correct."""

from tools.sandbox import _denature_escapes, _strip_fence


def test_fence_around_whole_script_is_stripped():
    code = "```python\nfrom typing import List\n\ndef f(x):\n    return x\n```"
    assert _strip_fence(code) == "from typing import List\n\ndef f(x):\n    return x"


def test_fence_without_language_and_with_crlf():
    assert _strip_fence("```\r\nprint(1)\r\n```\r\n") == "print(1)"


def test_fence_inside_a_string_is_left_alone():
    code = 'doc = """```python\nnot a fence```"""\nprint(doc)'
    assert _strip_fence(code) == code


def test_plain_code_untouched():
    assert _strip_fence("print(17 * 23)\n") == "print(17 * 23)\n"


def test_literal_backslash_n_becomes_newline_only_when_no_real_newlines():
    assert _denature_escapes("def f():\n    return 1") == "def f():\n    return 1"
    real = 'print("a\nb")\nprint(2)'          # has a real newline: keep the literal \n
    assert _denature_escapes(real) == real
