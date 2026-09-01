"""Egress monitor. Owner: person 3. Empty by design.

Will contain: the counters behind GET /api/network/status -- external packets
seen, DNS queries attempted, and whether the block rules are active. A
non-zero external_packets during the demo is a failed demo, so this needs to
be visibly zero on screen.
"""
