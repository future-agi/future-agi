"""The small ``fi.simulate.environment`` contract a generated world implements.

The parent package currently imports optional LiveKit code from ``fi.simulate.__init__`` before a
submodule can be imported. Keep the normal integration when that import is available, while
letting the standalone harness run its deterministic world and scenario gates from the base
editable dependency alone.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Mapping

try:
    from fi.simulate.environment import (
        EnvironmentAdapter,
        EnvironmentSnapshot,
        ToolExecutionResult,
    )
except ImportError:  # pragma: no cover - exercised only without the parent's optional extras
    from pydantic import BaseModel, Field

    class EnvironmentSnapshot(BaseModel):
        """State and tool definitions published by a local environment."""

        tools: list[dict[str, Any]] = Field(default_factory=list)
        artifacts: list[Any] = Field(default_factory=list)
        events: list[Any] = Field(default_factory=list)
        state: dict[str, Any] = Field(default_factory=dict)
        metadata: dict[str, Any] = Field(default_factory=dict)

    class ToolExecutionResult(BaseModel):
        """Result from executing one local tool call."""

        tool_call_id: str | None = None
        tool_name: str
        content: str
        result: Any = None
        success: bool = True
        error: str | None = None
        state_updates: dict[str, Any] = Field(default_factory=dict)
        artifacts: list[Any] = Field(default_factory=list)
        events: list[Any] = Field(default_factory=list)
        metadata: dict[str, Any] = Field(default_factory=dict)

        def to_tool_message(self) -> dict[str, Any]:
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id or self.tool_name,
                "content": self.content,
            }

    class EnvironmentAdapter(ABC):
        """The subset of the parent environment protocol the harness needs."""

        name = "environment"

        def reset(self, **_context: Any) -> EnvironmentSnapshot:
            return EnvironmentSnapshot()

        def observe(self, **_context: Any) -> EnvironmentSnapshot:
            return EnvironmentSnapshot()

        def handle_tool_call(
            self, tool_call: Mapping[str, Any], **_context: Any
        ) -> ToolExecutionResult | None:
            return None
