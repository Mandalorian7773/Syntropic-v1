"""Tool contract. Every tool in backend/tools/ and ragsvc/tools.py implements this.

The two length limits below are enforced at import time on purpose: a 7B model
picks the wrong tool when descriptions are long, and a loud ImportError in
October beats a silent wrong-tool selection in December.

Owner: shared (person1 + person2 + person3, all three must approve).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type

from pydantic import BaseModel, Field

MAX_NAME_LEN = 24
MAX_DESCRIPTION_LEN = 120


class ToolResult(BaseModel):
    ok: bool
    content: str  # ALWAYS <= 1000 tokens -- truncate before constructing, not after
    raw_path: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int
    error: str | None = None


class RunContext(BaseModel):
    session_id: str
    workspace_dir: str
    artifacts_dir: str


class Tool(ABC):
    name: str  # snake_case, <= 24 chars
    description: str  # ONE sentence, <= 120 chars
    args_model: Type[BaseModel]

    def __init_subclass__(cls, **kwargs) -> None:
        """Validate the class attributes at import time, not at call time."""
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return  # still abstract, subclass will be checked instead
        name = getattr(cls, "name", None)
        description = getattr(cls, "description", None)
        if not name or len(name) > MAX_NAME_LEN:
            raise ValueError(
                f"{cls.__name__}.name must be 1..{MAX_NAME_LEN} chars, got {name!r}"
            )
        if not description or len(description) > MAX_DESCRIPTION_LEN:
            raise ValueError(
                f"{cls.__name__}.description must be 1..{MAX_DESCRIPTION_LEN} chars, "
                f"got {len(description or '')}"
            )
        if not getattr(cls, "args_model", None):
            raise ValueError(f"{cls.__name__}.args_model is required")

    @abstractmethod
    def run(self, args: BaseModel, ctx: RunContext) -> ToolResult: ...

    def schema(self) -> dict:
        """JSON schema for the model's tool list."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
        }


__all__ = [
    "MAX_NAME_LEN",
    "MAX_DESCRIPTION_LEN",
    "ToolResult",
    "RunContext",
    "Tool",
]
