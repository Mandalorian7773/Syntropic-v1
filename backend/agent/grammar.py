"""GBNF grammar builder. Owner: person 3.

Generates, from the live tool registry, a grammar under which the ONLY
utterable strings are

    {"tool": "<one of the registered names>", "args": {<json object>}}
    {"final": "<string>"}

A 7B model free-forming JSON breaks it often enough to matter; constraining
the decoder makes the malformed rate a property of the sampler, not a hope
about the model. bench/run.py measures the rate with and without this grammar
(acceptance criterion 3). The static grammar.gbnf beside this file is the
tool-name-agnostic template kept for inspection; the runtime always uses
build_grammar() so a YAML-registered tool is a grammar change with zero code.
"""

from __future__ import annotations

# Standard JSON value rules, lifted from llama.cpp's json.gbnf.
_JSON_RULES = r"""
value  ::= object | array | string | number | ("true" | "false" | "null") ws
object ::= "{" ws ( string ":" ws value ("," ws string ":" ws value)* )? "}" ws
array  ::= "[" ws ( value ("," ws value)* )? "]" ws
string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4}) )* "\"" ws
number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})? ws
ws     ::= | " " | "\n" [ \t]{0,20}
"""


def build_grammar(tool_names: list[str]) -> str:
    if not tool_names:
        # No tools registered: the model may only answer.
        return (
            'root ::= "{" ws "\\"final\\"" ws ":" ws string ws "}"\n' + _JSON_RULES
        )
    name_alts = " | ".join(f'"\\"{name}\\""' for name in tool_names)
    return (
        "root ::= toolcall | final | finaltool\n"
        'final ::= "{" ws "\\"final\\"" ws ":" ws string ws "}"\n'
        # A 7B model told "use {"tool":...} for tools" generalises the shape and
        # tries to finish with {"tool":"final","args":{...}}. Without this rule
        # `final` is not a legal toolname, so the intended answer is literally
        # unutterable and the constrained decoder falls back to the next legal
        # path -- which is re-emitting the previous tool call. That looks like
        # the model looping; it is the grammar refusing to let it stop.
        # Observed on qwen2.5-vl-7b: ungrammared it emits
        #   {"tool":"final","args":{"set_pressure":"12.5 barg","page":1}}
        # and grammared it repeats search_documents until LOOP_DETECTED fires.
        'finaltool ::= "{" ws "\\"tool\\"" ws ":" ws "\\"final\\"" ws "," ws '
        '"\\"args\\"" ws ":" ws object ws "}"\n'
        'toolcall ::= "{" ws "\\"tool\\"" ws ":" ws toolname ws "," ws '
        '"\\"args\\"" ws ":" ws object ws "}"\n'
        f"toolname ::= {name_alts}\n"
        + _JSON_RULES
    )
