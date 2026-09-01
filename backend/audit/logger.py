"""Append-only audit log. Owner: person 3. Empty by design.

Will contain: every prompt, routing decision, tool invocation and artifact
written to DB_PATH with a monotonic sequence number. This is the evidence
trail the sovereignty claim rests on -- it must survive a crash mid-stream.
"""
