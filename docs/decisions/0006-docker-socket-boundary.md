# 0006 — The backend holds the host docker socket

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 3

## Context

`execute_python` runs model-written code in an ephemeral sibling container
(sandbox/Dockerfile) with no network, read-only root, 512 MB, 64 pids, all
capabilities dropped. Launching siblings requires the backend container to
mount `/var/run/docker.sock`, and whoever holds that socket effectively holds
root on the host. Alternatives considered: rootless Docker (not on the demo
image), a socket proxy limiting the API surface (another service that can
fail on stage), running the backend on bare metal (loses the compose
single-command demo).

## Decision

The backend mounts the host docker socket and is treated as a privileged,
trusted component; the UNTRUSTED code runs only inside the sandbox containers
it launches, never in the backend.

## Consequences

The security boundary is the sandbox container's constraint set, not the
backend process — the backend is exactly as trusted as the host, and we say
so out loud rather than implying the compose file is a jail. Mitigations in
place: the backend container has no route out (internal network), the sandbox
launch parameters are hard-coded in backend/tools/sandbox.py rather than
model-influenced, and every execution is audited. A judge who asks "what if
the agent escapes the sandbox?" gets this file as the honest answer.
