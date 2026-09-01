"""Workspace file tools. Owner: person 3. Empty by design.

Will contain: read/write/list confined to WORKSPACE_DIR, with path traversal
refused at the boundary. Returns ToolResult with content already truncated to
<= 1000 tokens and the full output at raw_path.
"""
