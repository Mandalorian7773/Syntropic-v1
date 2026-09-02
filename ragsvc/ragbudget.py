"""Token counting and truncation. Owner: person 2.

Every tool result this service returns crosses into a 7B model with a 16K
context window. A single unbounded result poisons the context and the agent
fails three steps later for reasons nobody can debug, so truncation happens
here, once, and every tool goes through it.

Counting uses the BGE-M3 tokenizer already on disk for embeddings -- no second
vocabulary to vendor, and no `tiktoken`, which downloads its BPE table from the
network on first use and would fail closed on the demo host.

When that tokenizer is absent the estimator falls back to a character ratio.
The fallback deliberately *over*-estimates: guessing high truncates a little
early, guessing low blows the context. Only one of those is recoverable.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import ragconfig as cfg

_tokenizer = None
_tokenizer_tried = False
_lock = threading.Lock()

# Conservative characters-per-token for the fallback estimator. Real English
# prose on this tokenizer runs about 4.2; 3.4 leaves margin for tables and
# tag numbers, which tokenize far worse than prose.
_FALLBACK_CHARS_PER_TOKEN = 3.4


def _get_tokenizer():
    """Load the BGE-M3 tokenizer once, or return None if it is not vendored."""
    global _tokenizer, _tokenizer_tried
    if _tokenizer_tried:
        return _tokenizer
    with _lock:
        if _tokenizer_tried:
            return _tokenizer
        _tokenizer_tried = True
        try:
            from tokenizers import Tokenizer  # noqa: PLC0415

            if cfg.EMBED_TOKENIZER.exists():
                _tokenizer = Tokenizer.from_file(str(cfg.EMBED_TOKENIZER))
        except Exception:  # noqa: BLE001 - a missing tokenizer is not fatal
            _tokenizer = None
        return _tokenizer


def count_tokens(text: str) -> int:
    """Token count for `text`, exact when the tokenizer is present."""
    if not text:
        return 0
    tok = _get_tokenizer()
    if tok is None:
        return int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1
    return len(tok.encode(text, add_special_tokens=False).ids)


def truncate_to_tokens(text: str, budget: int) -> tuple[str, bool]:
    """Cut `text` to at most `budget` tokens.

    Returns (text, was_truncated). Cuts on a line boundary where one is close
    by, because a table sliced mid-row reads as corruption to the model.
    """
    if budget <= 0:
        return "", bool(text)
    if not text:
        return "", False

    # Cheap pre-cut so the tokenizer never sees a 40 MB string.
    ceiling = int(budget * 12) + 512
    head = text[:ceiling]
    if count_tokens(head) <= budget and len(head) == len(text):
        return text, False

    tok = _get_tokenizer()
    if tok is not None:
        encoding = tok.encode(head, add_special_tokens=False)
        if len(encoding.ids) <= budget and len(head) == len(text):
            return text, False
        offsets = encoding.offsets[:budget]
        cut_at = offsets[-1][1] if offsets else 0
    else:
        cut_at = min(len(head), int(budget * _FALLBACK_CHARS_PER_TOKEN))

    clipped = head[:cut_at]
    # Prefer a line boundary if one is within the last 15% of the budget.
    newline = clipped.rfind("\n")
    if newline > cut_at * 0.85:
        clipped = clipped[:newline]
    return clipped.rstrip(), True


def tail_to_tokens(text: str, budget: int) -> str:
    """Last `budget` tokens of `text`. The mirror of truncate_to_tokens.

    Needed because a carry-over has to come off the *end* of a chunk, and
    slicing characters from the end can land inside a multi-byte token.
    """
    if budget <= 0 or not text:
        return ""
    ceiling = int(budget * 12) + 512
    tail = text[-ceiling:]

    tok = _get_tokenizer()
    if tok is None:
        return tail[-int(budget * _FALLBACK_CHARS_PER_TOKEN) :].lstrip()

    encoding = tok.encode(tail, add_special_tokens=False)
    if len(encoding.ids) <= budget:
        return tail.lstrip()
    start = encoding.offsets[-budget][0]
    return tail[start:].lstrip()


def raw_dir() -> Path:
    """Directory holding untruncated tool output. Created on demand."""
    path = cfg.WORKSPACE_DIR / "tool_output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def spill(full_text: str, prefix: str) -> str:
    """Write the untruncated output to disk and return its path.

    Named by content hash so the same oversized result written twice does not
    fill the workspace with duplicates.
    """
    digest = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]
    path = raw_dir() / f"{prefix}-{digest}.txt"
    if not path.exists():
        path.write_text(full_text, encoding="utf-8")
    return str(path)


def fit(full_text: str, prefix: str, budget: int | None = None) -> tuple[str, str | None]:
    """Fit text into the tool budget, spilling the remainder to disk.

    Returns (content, raw_path). `raw_path` is None when nothing was cut. The
    content always ends with an explicit note when it was cut, because a model
    that does not know it is reading a fragment will answer as though it read
    the whole thing.
    """
    limit = cfg.TOOL_TOKEN_BUDGET if budget is None else budget
    total = count_tokens(full_text)
    if total <= limit:
        return full_text, None

    path = spill(full_text, prefix)

    def notice_for(shown: int) -> str:
        return (
            f"\n\n[truncated: showing about {shown} of {total} tokens. "
            f"Full output written to {path}. Narrow the query or request specific "
            f"pages to see the rest.]"
        )

    # The notice is measured, not estimated. It carries a filesystem path, and
    # a long temp path tokenises to far more than any fixed reservation would
    # allow -- which is exactly how a "1000 token" result becomes 1032.
    allowance = limit - count_tokens(notice_for(limit))
    for _ in range(3):
        body, _cut = truncate_to_tokens(full_text, max(0, allowance))
        content = body + notice_for(count_tokens(body))
        overshoot = count_tokens(content) - limit
        if overshoot <= 0:
            return content, path
        allowance -= overshoot + 4
    return notice_for(0).strip(), path
