"""RAG-side tools exposed to the agent. Owner: person 2. Empty by design.

Will contain: contracts.Tool subclasses for search_documents, read_document
and generate_report, registered by backend/tools/registry.py over HTTP.

Keep `name` <= 24 chars and `description` to one sentence <= 120 chars --
contracts.Tool raises at import if you do not, and it raises here, not in
December on stage.
"""
