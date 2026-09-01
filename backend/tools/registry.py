"""Tool registry. Owner: person 3. Empty by design.

Will contain: discovery of every contracts.Tool subclass, the name -> instance
map, and the schema list handed to the model. The 24-char name and 120-char
description limits are enforced by contracts.Tool at import time, so a bad
tool fails the process start rather than the demo.
"""
