"""Context compaction. Owner: person 3.

A 16k window fills in three steps if a tool returns a raw document. When usage
crosses the threshold the OLDEST tool results collapse into one digest
message; the system prompt, the user's question and the most recent exchanges
survive verbatim, because those are what the next completion actually needs.

Deliberately deterministic -- no summarisation call. Spending a model round
trip to save context is how a 75%-full window becomes a 95%-full one.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4          # blunt but stable estimate for budgeting
KEEP_RECENT = 4              # trailing messages never compacted
DIGEST_SNIPPET_CHARS = 200   # per collapsed tool result


def estimate_tokens(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages) // CHARS_PER_TOKEN


def used_fraction(messages: list[dict], context: int) -> float:
    return estimate_tokens(messages) / max(1, context)


def compact(messages: list[dict]) -> list[dict]:
    """Collapse old tool results into a single digest message. Idempotent:
    running it twice changes nothing the second time."""
    if len(messages) <= KEEP_RECENT + 2:
        return messages

    head: list[dict] = []
    body = list(messages)
    # System prompt and the opening user message are load-bearing; keep them.
    while body and body[0].get("role") in ("system", "user") and len(head) < 2:
        head.append(body.pop(0))
    tail = body[len(body) - KEEP_RECENT:] if len(body) > KEEP_RECENT else body
    middle = body[: len(body) - len(tail)]
    if not middle:
        return messages

    snippets = []
    for m in middle:
        content = str(m.get("content", ""))
        snippet = content[:DIGEST_SNIPPET_CHARS]
        if len(content) > DIGEST_SNIPPET_CHARS:
            snippet += " ..."
        snippets.append(f"[{m.get('role', '?')}] {snippet}")

    digest = {
        "role": "system",
        "content": (
            "Earlier steps were compacted to stay inside the context window. "
            "Digest of what happened:\n" + "\n".join(snippets)
        ),
    }
    return head + [digest] + tail
