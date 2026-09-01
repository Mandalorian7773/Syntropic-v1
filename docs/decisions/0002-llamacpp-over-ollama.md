# 0002 — llama.cpp rather than Ollama

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 3

## Context

On 6.5 GB of usable VRAM every knob matters: `n_gpu_layers`, q8_0 KV cache
(halves KV memory, the difference between 16k context fitting and not),
`--mmproj` for vision input, and above all **GBNF grammar-constrained
decoding**, which our tool-call protocol depends on (agent/grammar.py). Ollama
exposes a curated subset of these; its structured output is JSON-schema-based
and narrower than raw GBNF, and its automatic memory management decides for
itself when to evict a model. We need eviction to be OUR decision, announced
over SSE (`model.loading` with `evicting` and `eta_s`) before it blocks.
On the air-gap side, llama-server is one process with zero first-run
downloads; Ollama's registry-shaped workflow is one more thing to prove
inert to a judge.

## Decision

We run `llama-server` directly and wrote our own ~100-line supervisor
(backend/llm/manager.py) for load/evict, instead of using Ollama.

## Consequences

We gave up Ollama's genuinely easier setup and model management UX; we own
process supervision, health probing and swap timing ourselves, and that code
is ours to debug at 2am. In exchange: full GBNF support at the decoder,
deterministic single-residency on the GPU, swap events the UI can narrate
honestly, and an inference stack whose only network behaviour is listening on
localhost.
