# 0004 — No agent framework

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 3

## Context

The loop we need is small: prompt, parse a grammar-constrained tool call,
execute, observe, repeat, bounded by MAX_STEPS. It landed at ~300 lines
(backend/agent/loop.py) including loop detection, retry caps, compaction and
audit persistence. LangChain/LlamaIndex/CrewAI add abstraction layers that
assume hosted models, sometimes touch the network at import, and each drag in
a dependency tree that we would have to vendor wheel-by-wheel into the
offline bundle — every transitive dependency is a thing that can fail on the
demo host with no PyPI to reach. And when the loop misbehaves the night
before the demo, the stack trace is either 300 lines of ours or 30 frames of
someone else's. Evaluators will ask how the loop works; "here is the file"
is an answer, "here is the framework" is not.

## Decision

The agent loop is hand-written in backend/agent/loop.py; no agent framework
anywhere in the dependency tree.

## Consequences

We reimplemented things frameworks give away — retries, loop detection,
context compaction — and we own their bugs; that is a real cost and we paid
it. In return: zero framework dependencies to vendor, every control-flow
decision explainable line-by-line, and event emission/audit persistence woven
exactly where our contract needs them rather than adapted around someone
else's callback model.
