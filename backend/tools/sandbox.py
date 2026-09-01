"""Sandboxed Python execution. Owner: person 3. Empty by design.

Will contain: execute_python, running user/model code in the container built
by sandbox/Dockerfile -- no network, read-only root, workspace bind-mounted,
wall-clock timeout emitting error code TOOL_TIMEOUT.
"""
