"""Deliverable generation: real .docx and .xlsx files. Owner: person 2.

The public surface is two functions. Everything else in this package -- the
YAML templates, the OOXML helpers, the artifact store -- is how they work.

    from docgen import create_docx, create_xlsx, Section, Sheet

Both write to ./artifacts/{artifact_id}/{filename} and record a row in the
shared SQLite artifacts table, so a file exists on disk and is downloadable the
moment the call returns.
"""

from __future__ import annotations

from .docx_builder import build_docx
from .schema import ArtifactRecord, Section, Sheet
from .store import MIME_DOCX, MIME_XLSX, allocate, register, slugify
from .templates import UnknownTemplate, available as available_templates, load as load_template
from .xlsx_builder import build_xlsx

__all__ = [
    "ArtifactRecord",
    "Section",
    "Sheet",
    "UnknownTemplate",
    "available_templates",
    "load_template",
    "create_docx",
    "create_xlsx",
]


def create_docx(
    template: str,
    title: str,
    sections: list[Section],
    *,
    meta: dict | None = None,
    filename: str | None = None,
    session_id: str | None = None,
    artifacts_dir: str | None = None,
) -> ArtifactRecord:
    """Generate a Word deliverable from a template.

    Raises UnknownTemplate for a template name with no YAML file, which is a
    caller error worth surfacing rather than papering over with a default --
    an approval note silently rendered as a blank layout is worse than an error
    naming the three templates that do exist.
    """
    spec = load_template(template)
    name = filename or f"{slugify(title, fallback=spec.id)}.docx"
    if not name.lower().endswith(".docx"):
        name += ".docx"

    artifact_id, path = allocate(name, artifacts_dir)
    build_docx(template, title, sections, path, meta=meta)
    return register(
        artifact_id, path, MIME_DOCX, template=spec.id, title=title, session_id=session_id
    )


def create_xlsx(
    sheets: list[Sheet],
    *,
    title: str | None = None,
    filename: str | None = None,
    session_id: str | None = None,
    artifacts_dir: str | None = None,
) -> ArtifactRecord:
    """Generate an Excel workbook from one or more sheet specifications."""
    label = title or (sheets[0].title or sheets[0].name if sheets else "workbook")
    name = filename or f"{slugify(label, fallback='workbook')}.xlsx"
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"

    artifact_id, path = allocate(name, artifacts_dir)
    build_xlsx(sheets, path)
    return register(
        artifact_id, path, MIME_XLSX, template=None, title=label, session_id=session_id
    )
