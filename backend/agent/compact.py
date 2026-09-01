"""Context compaction. Owner: person 3. Empty by design.

Will contain: the strategy for keeping a multi-step conversation inside a
16k window on an 8 GB GPU -- summarising older turns, dropping raw tool output
in favour of ToolResult.content, and keeping citations verbatim.
"""
