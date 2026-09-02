"""Template loading and reference numbering. Owner: person 2.

Templates are YAML, not Python, for one practical reason: the fields on an
approval note are a matter of what the department already uses, and changing
them should be an edit to a data file that a domain person can read, not a code
change. `docgen/templates/*.yaml` is the whole vocabulary.

Reference numbers are generated in the house style -- MRPL/I&R/APR/2026/0007 --
and are sequential within a prefix and year. A document with no reference
number is not a document anyone files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Series code in the reference number. Kept short because it is read aloud in
# meetings: "the APR two-six oh-seven".
REFERENCE_ORG = "MRPL"
REFERENCE_UNIT = "I&R"


@dataclass
class TemplateSection:
    key: str
    heading: str
    required: bool = False
    kind: str = "prose"  # prose | table | formula | result | numbered
    columns: list[str] = field(default_factory=list)
    hint: str = ""


@dataclass
class Template:
    id: str
    title: str
    document_type: str
    reference_prefix: str
    orientation: str = "portrait"
    subject_label: str = "Subject"
    header_fields: list[dict[str, Any]] = field(default_factory=list)
    sections: list[TemplateSection] = field(default_factory=list)
    signature_block: dict[str, Any] = field(default_factory=dict)
    distribution: dict[str, Any] = field(default_factory=dict)
    severity: dict[str, Any] = field(default_factory=dict)

    def section(self, key: str) -> TemplateSection | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def severity_colours(self) -> dict[str, str]:
        return self.severity.get("colours", {}) if self.severity else {}


class UnknownTemplate(KeyError):
    """Raised for a template name that has no YAML file."""


@lru_cache(maxsize=None)
def available() -> tuple[str, ...]:
    if not TEMPLATE_DIR.exists():
        return ()
    return tuple(sorted(p.stem for p in TEMPLATE_DIR.glob("*.yaml")))


@lru_cache(maxsize=None)
def load(name: str) -> Template:
    """Load a template by id. Cached; templates do not change at runtime."""
    path = TEMPLATE_DIR / f"{name}.yaml"
    if not path.exists():
        raise UnknownTemplate(
            f"unknown template {name!r}. Available: {', '.join(available()) or 'none'}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sections = [
        TemplateSection(
            key=item["key"],
            heading=item.get("heading", item["key"].replace("_", " ").title()),
            required=bool(item.get("required", False)),
            kind=item.get("kind", "prose"),
            columns=list(item.get("columns", [])),
            hint=item.get("hint", ""),
        )
        for item in data.get("sections", [])
    ]
    return Template(
        id=data.get("id", name),
        title=data.get("title", name.replace("_", " ").title()),
        document_type=data.get("document_type", name.replace("_", " ").upper()),
        reference_prefix=data.get("reference_prefix", "DOC"),
        orientation=data.get("orientation", "portrait"),
        subject_label=data.get("subject_label", "Subject"),
        header_fields=list(data.get("header_fields", [])),
        sections=sections,
        signature_block=data.get("signature_block", {}) or {},
        distribution=data.get("distribution", {}) or {},
        severity=data.get("severity", {}) or {},
    )


def next_reference(prefix: str, template_id: str | None = None, year: int | None = None) -> str:
    """Next reference number in a series, e.g. MRPL/I&R/APR/2026/0007.

    Each document type keeps its own sequence, as it does in practice: the APR
    series and the INS series are numbered independently, and a shared counter
    would produce APR/0001 followed by APR/0003 whenever an inspection report
    was generated in between.

    The sequence is derived from the artifacts table rather than stored: one
    fewer piece of mutable state, and it stays correct if that table is
    restored from a backup.
    """
    year = year or time.localtime().tm_year
    sequence = 1
    try:
        import ragdb  # noqa: PLC0415

        start = int(time.mktime((year, 1, 1, 0, 0, 0, 0, 1, -1)))
        end = int(time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 1, -1)))
        if template_id:
            row = ragdb.connect().execute(
                "SELECT COUNT(*) FROM rag_artifacts "
                "WHERE template = ? AND created_at >= ? AND created_at < ?",
                (template_id, start, end),
            ).fetchone()
        else:
            row = ragdb.connect().execute(
                "SELECT COUNT(*) FROM rag_artifacts "
                "WHERE template IS NOT NULL AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
        sequence = int(row[0]) + 1
    except Exception:  # noqa: BLE001 - numbering must never fail a generation
        pass
    return f"{REFERENCE_ORG}/{REFERENCE_UNIT}/{prefix}/{year}/{sequence:04d}"


def today() -> str:
    """Date in the format Indian engineering paperwork uses."""
    return time.strftime("%d %b %Y")
