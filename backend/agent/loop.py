"""The agent loop. Owner: person 3. Empty by design.

Will contain: the think -> tool-call -> observe cycle, bounded by MAX_STEPS,
emitting agent.step / tool.call / tool.result / token events as it goes, and
terminating with a `done` event carrying a StopReason.

No agent framework here -- see docs/decisions/0004-no-agent-framework.md.
"""
