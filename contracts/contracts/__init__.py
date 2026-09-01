"""Shared contracts for the SIH26117 sovereign AI workbench.

Import from here, never redefine locally:

    from contracts import SessionStart, Token, Done, to_sse
    from contracts import Tool, ToolResult, RunContext
    from contracts import HealthResponse, SearchRequest

Changing anything in this package is a separate PR touching only this package.
See CHANGE-PROTOCOL.md.
"""

from .api import *  # noqa: F401,F403
from .events import *  # noqa: F401,F403
from .tools import *  # noqa: F401,F403

from . import api, events, tools  # noqa: F401

__version__ = "0.1.0"
