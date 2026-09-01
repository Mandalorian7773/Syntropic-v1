# 0003 — One GPU model resident at a time

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 3

## Context

<!-- TO BE FILLED IN. Points to cover:
     - the arithmetic: 8 GB total, ~5.1-5.6 GB per Q4_K_M 7-8B model, plus KV
       cache at 16k context, plus what the display already holds
     - what happens if you try to hold two (it does not fit; measure and record
       the actual OOM)
     - why not one smaller model for everything
     - the cost we accept: a visible ~8 s swap, which is why model.loading
       carries `evicting` and `eta_s` -- the UI turns dead air into feedback
-->

## Decision

<!-- TO BE FILLED IN -->

## Consequences

<!-- TO BE FILLED IN -->
