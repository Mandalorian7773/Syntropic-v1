"""Artifact storage. Owner: person 2.

Files land at `./artifacts/{artifact_id}/{filename}` and the metadata row goes
into the shared SQLite `artifacts` table.

One directory per artifact rather than one flat directory: two approval notes
generated a minute apart are both called something like
"approval-note-relief-valve.docx", and a flat layout means the second silently
overwrites the first. Giving each its own directory keeps the human-readable
filename -- which is what appears in the download and what the judge sees --
without a uniqueness suffix mangled into it.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import ragconfig as cfg
import ragdb

from .schema import ArtifactRecord

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "document", max_len: int = 60) -> str:
    slug = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    return (slug[:max_len].rstrip("-") or fallback)


def allocate(filename: str, base_dir: str | Path | None = None) -> tuple[str, Path]:
    """Reserve an artifact id and its directory. Returns (artifact_id, path).

    `base_dir` lets a caller honour the `artifacts_dir` on its RunContext,
    which is the agent's declared location for this session's output. It falls
    back to the configured ARTIFACTS_DIR, which is what every non-agent caller
    (the HTTP endpoints, the tests) uses.
    """
    artifact_id = uuid.uuid4().hex[:12]
    root = Path(base_dir) if base_dir else cfg.ARTIFACTS_DIR
    directory = root / artifact_id
    directory.mkdir(parents=True, exist_ok=True)
    return artifact_id, directory / Path(filename).name


def register(
    artifact_id: str,
    path: Path,
    mime: str,
    template: str | None = None,
    title: str | None = None,
    session_id: str | None = None,
) -> ArtifactRecord:
    """Record a written file in the artifacts table."""
    size = path.stat().st_size
    ragdb.insert_artifact(
        artifact_id=artifact_id,
        filename=path.name,
        path=str(path),
        mime=mime,
        size_bytes=size,
        template=template,
        title=title,
        session_id=session_id,
    )
    return ArtifactRecord(
        artifact_id=artifact_id,
        filename=path.name,
        path=str(path),
        mime=mime,
        size_bytes=size,
        template=template,
        title=title,
    )
