"""Tool contract and output budget. Owner: person 2.

The acceptance criterion this file exists for: every tool returns a ToolResult
whose `content` is under 1000 tokens, **verified by feeding in a pathologically
large document**. A single unbounded tool result blows a 16K context and the
agent fails three steps later for reasons nobody can debug, so the guarantee is
tested with inputs far larger than anything a real corpus produces.
"""

from __future__ import annotations

import json
import uuid

import pytest

import ragconfig as cfg
import ragdb
import tools as ragtools
from contracts import MAX_DESCRIPTION_LEN, MAX_NAME_LEN, ToolResult
from docgen import Section, Sheet
from index.search import Hit, SearchResult
from ragbudget import count_tokens

HUGE_PARAGRAPH = (
    "The measured wall thickness at condition monitoring location CML-19 was "
    "5.62 mm against a retirement thickness of 5.20 mm, giving a remaining "
    "life of 1.8 years at the governing corrosion rate of 0.24 mm per year. "
) * 400  # roughly 25,000 tokens


# --- contract ---------------------------------------------------------------


def test_exactly_four_tools_are_exported():
    assert {tool.name for tool in ragtools.get_tools()} == {
        "search_documents",
        "read_document",
        "create_docx",
        "create_xlsx",
    }


@pytest.mark.parametrize("tool", ragtools.get_tools(), ids=lambda t: t.name)
def test_tool_metadata_is_within_the_contract_limits(tool):
    assert len(tool.name) <= MAX_NAME_LEN
    assert tool.name == tool.name.lower().replace(" ", "_")
    assert len(tool.description) <= MAX_DESCRIPTION_LEN
    # One sentence. A paragraph makes a 7B model choose the wrong tool.
    assert tool.description.count(".") <= 1
    assert "\n" not in tool.description


@pytest.mark.parametrize("tool", ragtools.get_tools(), ids=lambda t: t.name)
def test_every_tool_publishes_a_usable_schema(tool):
    schema = tool.schema()
    assert schema["name"] == tool.name
    assert schema["parameters"]["type"] == "object"


# --- the pathological input -------------------------------------------------


def _fake_hits(count: int, text: str) -> SearchResult:
    result = SearchResult()
    for index in range(count):
        result.hits.append(
            Hit(
                chunk_id=f"chunk-{index}",
                doc_id="doc-huge",
                filename="TMS-2024-CDU-03-thickness-survey.pdf",
                page=index + 1,
                section="2. Measurement Results",
                text=text,
                score=1.0 - index * 0.01,
            )
        )
    return result


def test_search_content_stays_inside_the_budget_for_enormous_chunks(monkeypatch, run_context):
    monkeypatch.setattr(
        "index.search.search", lambda *a, **k: _fake_hits(5, HUGE_PARAGRAPH)
    )
    tool = ragtools.BY_NAME["search_documents"]
    result = tool.run(tool.args_model(query="remaining life", top_k=5), run_context)

    assert result.ok
    assert count_tokens(result.content) <= cfg.TOOL_TOKEN_BUDGET
    assert result.raw_path, "the untruncated output must be written to disk"


def test_search_keeps_provenance_for_every_hit_even_when_truncating(monkeypatch, run_context):
    """Truncation shortens snippets; it must never drop a citation.

    The agent turns each hit into a `citation` event, which needs doc_id,
    filename, page, score and snippet. A formatted string cannot carry doc_id
    or score, so the content is JSON -- and every field has to survive the
    truncation that a pathologically long passage forces.
    """
    monkeypatch.setattr(
        "index.search.search", lambda *a, **k: _fake_hits(5, HUGE_PARAGRAPH)
    )
    tool = ragtools.BY_NAME["search_documents"]
    result = tool.run(tool.args_model(query="remaining life", top_k=5), run_context)

    payload = json.loads(result.content)
    assert len(payload["hits"]) == 5, "a hit was dropped rather than shortened"
    for index, hit in enumerate(payload["hits"]):
        assert set(hit) >= {"doc_id", "filename", "page", "score", "snippet"}
        assert hit["doc_id"], "no doc_id: the UI pins sources by it"
        assert hit["filename"] == "TMS-2024-CDU-03-thickness-survey.pdf"
        assert hit["page"] == index + 1
        assert isinstance(hit["score"], (int, float))
        assert hit["snippet"], "a hit with no snippet tells the model nothing"


def test_search_content_is_parseable_json_when_nothing_matches(monkeypatch, run_context):
    """The empty case has to parse too, or the agent's parser throws on zero hits."""
    monkeypatch.setattr("index.search.search", lambda *a, **k: SearchResult())
    tool = ragtools.BY_NAME["search_documents"]
    result = tool.run(tool.args_model(query="nothing at all", top_k=5), run_context)

    assert result.ok is True
    assert json.loads(result.content)["hits"] == []


def test_snippet_carries_the_answer_not_just_the_start_of_the_chunk(run_context):
    """The window onto a chunk must follow the query, not the chunk's first line.

    Regression test for a real failure: the retrieved chunk held the answer 464
    characters in, head-truncation stopped before it, the model reissued the
    identical query and the agent's loop detector aborted the turn.
    """
    buried = (
        "6. Valve Register\n\n"
        + "Preamble line that matches nothing in particular. " * 12
        + "\n| Tag No. | Location | Set Pressure |\n| --- | --- | --- |\n"
        + "| PSV-2101 | Reflux drum | 9.8 barg |\n"
        + "| PSV-2103 | Debutaniser overhead | 12.5 barg |\n"
        + "| PSV-2104 | Bottoms pump | 18.0 barg |\n"
    )
    result = SearchResult()
    result.hits.append(
        Hit(chunk_id="c1", doc_id="d1", filename="SOP.pdf", page=2,
            section="6. Valve Register", text=buried, score=0.9)
    )
    import index.search as search_module

    original = search_module.search
    try:
        search_module.search = lambda *a, **k: result
        tool = ragtools.BY_NAME["search_documents"]
        out = tool.run(tool.args_model(query="set pressure of PSV-2103", top_k=1), run_context)
    finally:
        search_module.search = original

    snippet = json.loads(out.content)["hits"][0]["snippet"]
    assert "PSV-2103" in snippet
    assert "12.5" in snippet, f"the answer never reached the snippet: {snippet!r}"


def test_read_document_truncates_and_spills_a_huge_document(run_context):
    doc_id = str(uuid.uuid4())
    ragdb.upsert_document(
        doc_id=doc_id,
        filename="pathological.pdf",
        path="/nonexistent/pathological.pdf",
        pages=3,
        chunk_count=0,
        size_bytes=1,
        sha256="0" * 64,
        scanned=False,
        indexed=False,
        ingest_ms=0,
    )
    ragdb.replace_pages(
        doc_id,
        [{"page": n, "text": HUGE_PARAGRAPH, "scanned": False, "mean_conf": 1.0} for n in (1, 2, 3)],
    )

    tool = ragtools.BY_NAME["read_document"]
    result = tool.run(tool.args_model(file_id="pathological.pdf"), run_context)

    assert result.ok
    assert count_tokens(result.content) <= cfg.TOOL_TOKEN_BUDGET
    assert result.raw_path
    from pathlib import Path

    spilled = Path(result.raw_path).read_text(encoding="utf-8")
    assert len(spilled) > len(result.content) * 5
    assert "truncated" in result.content


def test_read_document_reports_an_unknown_file_without_raising(run_context):
    tool = ragtools.BY_NAME["read_document"]
    result = tool.run(tool.args_model(file_id="no-such-file.pdf"), run_context)
    assert result.ok is False
    assert result.error
    assert count_tokens(result.content) <= cfg.TOOL_TOKEN_BUDGET


# --- generators -------------------------------------------------------------


def test_create_docx_returns_an_artifact_and_a_short_confirmation(run_context):
    tool = ragtools.BY_NAME["create_docx"]
    args = tool.args_model(
        template="approval_note",
        title="Repair of Nozzle N1 on Vessel V-1201",
        sections=[
            Section(key="background", body=HUGE_PARAGRAPH[:4000]),
            Section(key="recommendation", body="Replace the affected nozzle neck."),
        ],
    )
    result = tool.run(args, run_context)

    assert result.ok, result.error
    assert len(result.artifacts) == 1
    assert count_tokens(result.content) <= cfg.TOOL_TOKEN_BUDGET
    row = ragdb.get_artifact(result.artifacts[0])
    assert row and row["filename"].endswith(".docx")


def test_create_docx_rejects_an_unknown_template(run_context):
    tool = ragtools.BY_NAME["create_docx"]
    result = tool.run(tool.args_model(template="not_a_template", title="x"), run_context)
    assert result.ok is False
    assert "approval_note" in result.content


def test_create_xlsx_returns_an_artifact(run_context):
    tool = ragtools.BY_NAME["create_xlsx"]
    args = tool.args_model(
        sheets=[
            Sheet(
                name="Findings",
                title="CDU-P-11 thickness survey",
                columns=["CML", "Measured", "Rate"],
                rows=[["CML-12", "7.80", "0.12"], ["CML-19", "5.62", "0.24"]],
            )
        ],
        title="Thickness survey",
    )
    result = tool.run(args, run_context)

    assert result.ok, result.error
    assert len(result.artifacts) == 1
    row = ragdb.get_artifact(result.artifacts[0])
    assert row and row["filename"].endswith(".xlsx")


def test_create_xlsx_with_no_sheets_fails_cleanly(run_context):
    tool = ragtools.BY_NAME["create_xlsx"]
    result = tool.run(tool.args_model(sheets=[]), run_context)
    assert result.ok is False


# --- dispatch ---------------------------------------------------------------


def test_run_tool_rejects_an_unknown_name(run_context):
    result = ragtools.run_tool("delete_everything", {}, run_context)
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "unknown tool" in result.content


def test_run_tool_reports_invalid_arguments_rather_than_raising(run_context):
    result = ragtools.run_tool("search_documents", {"top_k": "not a number"}, run_context)
    assert result.ok is False
    assert result.error
