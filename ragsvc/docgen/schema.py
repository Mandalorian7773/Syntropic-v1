"""Argument shapes for the deliverable generators. Owner: person 2.

These are the models the agent fills in, so they are shaped for a 7B model to
fill in: flat, few fields, obvious names, and every field optional except the
one that carries the text. A schema with nested unions produces beautifully
typed arguments that the model never manages to construct.

`Section` deliberately accepts body, bullets and a table on the same object
rather than being a union of three kinds. The model can then emit whichever it
has without choosing a discriminator first, which it gets wrong often enough to
matter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Section(BaseModel):
    """One section of a generated document."""

    heading: str = Field(default="", description="Section heading.")
    body: str = Field(default="", description="Section text, plain prose.")
    bullets: list[str] = Field(
        default_factory=list, description="Bullet points for this section."
    )
    table: list[list[str]] = Field(
        default_factory=list,
        description="Rows of cells; the first row is the header.",
    )
    key: str | None = Field(
        default=None,
        description="Template slot to fill, e.g. background or findings.",
    )

    @property
    def is_empty(self) -> bool:
        return not (self.body.strip() or self.bullets or self.table)


class Sheet(BaseModel):
    """One worksheet of a generated workbook."""

    name: str = Field(default="Sheet1", description="Worksheet tab name.")
    title: str = Field(default="", description="Title row above the table.")
    columns: list[str] = Field(default_factory=list, description="Column headers.")
    rows: list[list[Any]] = Field(default_factory=list, description="Data rows.")
    notes: list[str] = Field(
        default_factory=list, description="Footnotes placed below the table."
    )


class ArtifactRecord(BaseModel):
    """What a generator returns and what the artifacts table stores."""

    artifact_id: str
    filename: str
    path: str
    mime: str
    size_bytes: int
    template: str | None = None
    title: str | None = None
