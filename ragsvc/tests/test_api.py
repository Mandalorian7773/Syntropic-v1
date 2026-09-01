"""HTTP surface. Owner: person 2.

The backend proxies these paths under /api/*, and Person 1's SPA renders what
comes back, so the shapes here are a contract with two other people. These
tests exercise them through the real ASGI app -- routing, validation and
response models included -- rather than calling the functions underneath.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import ragdb


@pytest.fixture(scope="module")
def client():
    import main

    with TestClient(main.app) as test_client:
        yield test_client


# --- health -----------------------------------------------------------------


def test_health_reports_index_state_and_missing_weights(client):
    body = client.get("/health").json()

    assert body["ok"] is True
    # The scaffold's original two keys, which the backend already reads.
    assert "qdrant" in body and "documents" in body
    assert "missing_weights" in body
    assert "degraded" in body
    assert "egress" in body


# --- documents --------------------------------------------------------------


def test_documents_listing_matches_the_contract_shape(client):
    body = client.get("/documents").json()
    assert "documents" in body
    for document in body["documents"]:
        assert {"id", "filename", "pages", "chunks", "size_bytes", "indexed", "ingested_ts"} <= set(document)


def test_unsupported_file_types_are_refused(client):
    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert response.status_code == 415


def test_an_empty_upload_is_refused(client):
    response = client.post(
        "/documents/upload", files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert response.status_code == 400


def test_reading_an_unknown_document_is_a_404(client):
    assert client.get("/documents/does-not-exist").status_code == 404


def test_deleting_an_unknown_document_is_a_404(client):
    assert client.delete("/documents/does-not-exist").status_code == 404


def test_bad_page_selectors_are_rejected(client):
    doc_id = "api-test-doc"
    ragdb.upsert_document(
        doc_id=doc_id, filename="api-test.pdf", path="/tmp/api-test.pdf", pages=1,
        chunk_count=0, size_bytes=1, sha256="0" * 64, scanned=False, indexed=False,
        ingest_ms=0,
    )
    ragdb.replace_pages(doc_id, [{"page": 1, "text": "hello", "scanned": False, "mean_conf": 1.0}])

    assert client.get(f"/documents/{doc_id}", params={"pages": "one,two"}).status_code == 400
    ok = client.get(f"/documents/{doc_id}", params={"pages": "1"})
    assert ok.status_code == 200
    assert ok.json()["pages"][0]["text"] == "hello"


# --- search -----------------------------------------------------------------


def test_search_returns_the_contract_shape(client):
    response = client.post("/search", json={"query": "relief valve set pressure", "top_k": 3})
    assert response.status_code == 200
    body = response.json()
    assert "hits" in body
    for hit in body["hits"]:
        # Provenance is the point. A hit without a filename and a page cannot
        # become a citation, and the frontend has nothing to render.
        assert hit["filename"]
        assert isinstance(hit["page"], int)
        assert {"doc_id", "filename", "page", "score", "snippet"} <= set(hit)


def test_an_empty_query_returns_no_hits_rather_than_an_error(client):
    response = client.post("/search", json={"query": "   ", "top_k": 5})
    assert response.status_code == 200
    assert response.json()["hits"] == []


# --- artifacts --------------------------------------------------------------


def test_templates_endpoint_describes_every_slot(client):
    body = client.get("/artifacts/templates").json()
    ids = {t["id"] for t in body["templates"]}
    assert {"approval_note", "inspection_summary", "calculation_sheet"} <= ids

    approval = next(t for t in body["templates"] if t["id"] == "approval_note")
    keys = {s["key"] for s in approval["sections"]}
    assert {"background", "recommendation"} <= keys


def test_generating_and_downloading_a_docx(client):
    response = client.post(
        "/artifacts/docx",
        json={
            "template": "approval_note",
            "title": "Replacement of relief valve PSV-2103",
            "sections": [
                {"key": "background", "body": "The valve failed its as-received pop test."},
                {"key": "recommendation", "body": "Replace with a like-for-like spare."},
            ],
        },
    )
    assert response.status_code == 200
    info = response.json()
    assert info["filename"].endswith(".docx")
    assert info["size_bytes"] > 0

    download = client.get(info["url"])
    assert download.status_code == 200
    assert download.content[:2] == b"PK", "a .docx is a zip; this is not one"


def test_generating_an_xlsx(client):
    response = client.post(
        "/artifacts/xlsx",
        json={
            "sheets": [
                {
                    "name": "CMLs",
                    "columns": ["CML", "Measured"],
                    "rows": [["CML-19", "5.62"]],
                }
            ],
            "title": "Thickness survey",
        },
    )
    assert response.status_code == 200
    assert response.json()["filename"].endswith(".xlsx")


def test_an_unknown_template_is_a_400_naming_the_real_ones(client):
    response = client.post(
        "/artifacts/docx", json={"template": "invoice", "title": "x", "sections": []}
    )
    assert response.status_code == 400
    assert "approval_note" in response.json()["detail"]


def test_downloading_an_unknown_artifact_is_a_404(client):
    assert client.get("/artifacts/deadbeefcafe").status_code == 404


# --- tools over HTTP --------------------------------------------------------


def test_tool_schemas_are_served_for_the_agent_registry(client):
    body = client.get("/tools").json()
    names = {tool["name"] for tool in body["tools"]}
    assert names == {"search_documents", "read_document", "create_docx", "create_xlsx"}
    for tool in body["tools"]:
        assert len(tool["description"]) <= 120
        assert tool["parameters"]["type"] == "object"


def test_running_a_tool_over_http_returns_a_toolresult(client):
    response = client.post(
        "/tools/search_documents",
        json={"arguments": {"query": "corrosion under insulation", "top_k": 3}},
    )
    assert response.status_code == 200
    body = response.json()
    assert {"ok", "content", "raw_path", "artifacts", "duration_ms", "error"} <= set(body)
    assert isinstance(body["duration_ms"], int)


def test_an_unknown_tool_name_comes_back_as_a_failed_result_not_a_500(client):
    response = client.post("/tools/rm_rf", json={"arguments": {}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "unknown tool" in body["content"]
