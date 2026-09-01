"""SSE helpers beyond contracts.to_sse. Owner: person 3.

Will contain: heartbeat/keepalive frames so proxies do not drop an idle stream
during a slow model load, per-session cancellation wiring for POST
/api/chat/cancel, and the async generator that fans agent-loop output into
frames. Empty by design.
"""
