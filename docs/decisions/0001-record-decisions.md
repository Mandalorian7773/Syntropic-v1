# 0001 — Record architecture decisions

**Status:** accepted
**Date:** 2026-09-02

## Context

Three people are building one system in parallel, and the constraints driving
our choices (8 GB of VRAM, no network, one demo machine) are not obvious from
reading the code afterwards.

## Decision

We record every architecturally significant decision as a short numbered file
in this directory, using this template: Context, Decision, Consequences.

## Consequences

Writing one costs ten minutes; re-litigating a decision in week three costs an
afternoon, and explaining it to a judge without notes costs the demo.

---

## Template

```markdown
# NNNN — Title

**Status:** proposed | accepted | superseded by NNNN
**Date:** YYYY-MM-DD

## Context
What forced a choice. Include the constraint, with numbers.

## Decision
What we chose, in one sentence.

## Consequences
What this costs us and what it buys us. Be honest about the cost.
```
