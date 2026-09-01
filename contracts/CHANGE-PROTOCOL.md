# Contract change protocol

`contracts/` is the only code all three of us depend on. It has no single owner,
which means it needs a rule instead of a person.

## The rule

A change to `contracts/` is:

1. **A separate pull request that touches only `contracts/`.** No feature code in
   the same PR. If your feature needs a contract change, that is two PRs.
2. **Approved by all three developers.** CODEOWNERS enforces this. One approval
   is not enough — a field you find obvious is a field someone else is parsing.
3. **Announced in the team channel before merge.** Post the diff. Two people
   are mid-branch when you merge this, and they need to know before they rebase.
4. **Followed immediately by `make types` and a commit of the regenerated
   `frontend/src/types/events.ts`.** The generated file is committed on purpose:
   the frontend build must break the moment the contract moves. That break is
   the feature, not a nuisance.

## Why it is this strict

Three people building against an implicit contract on three laptops, with no
internet and one demo slot, will discover the drift on stage. A hard failure at
build time is the only feedback loop that reaches all three of us at once.

## What is not a contract change

Adding a docstring, a comment, or a test. Those go in your normal PR.
