# 0003 — One GPU model resident at a time

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 3

## Context

The arithmetic: 8 GB card, ~1.5 GB gone to CUDA context and the display.
A 7-8B model at Q4_K_M is 5.1-5.6 GB of weights, plus KV cache at 16k
context (~1 GB even at q8_0), plus the vision projector when images are in
play. One model fills the budget; two cannot coexist — attempting it makes
llama.cpp spill layers to CPU and throughput collapses below usable, which
is an OOM with extra steps. One smaller model for everything was considered
and rejected: the sponsor's four use cases include both vision and serious
code generation, and no single ≤4 GB model does both credibly.

## Decision

Exactly one GGUF is resident on the GPU at any moment; changing models is an
explicit evict-then-load owned by backend/llm/manager.py.

## Consequences

Every capability switch costs a visible 5-15 s reload. We spend that cost in
the open: `model.loading` is emitted BEFORE the swap blocks, carrying
`evicting` and an `eta_s` learned from previous loads (db table
`model_loads`), and the router charges a `switch_penalty` (config/models.yaml)
so a marginal classification never buys an 8-second reload. The reward is
that each task type gets the best full-sized 7-8B model we can hold, and the
demo can narrate the swap instead of hiding a frozen screen.
