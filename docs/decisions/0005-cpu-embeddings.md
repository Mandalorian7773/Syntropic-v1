# 0005 — Embeddings and OCR run on CPU

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 2

## Context

<!-- TO BE FILLED IN. Points to cover:
     - the VRAM budget is already fully spent on the resident LLM (see 0003)
     - an embedding model on the GPU means evicting the LLM per query, which is
       ~8 s per query -- unusable
     - measured CPU throughput: chunks/sec at ingest, ms/query at search time.
       Put the real numbers here from bench/results/
     - ingest is a one-off cost paid before the demo; query-time embedding of a
       single short query is small
     - onnxruntime on CPU, not torch: smaller to vendor, faster to start
-->

## Decision

<!-- TO BE FILLED IN -->

## Consequences

<!-- TO BE FILLED IN -->
