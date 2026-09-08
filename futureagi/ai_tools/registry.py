from __future__ import annotations

import logging
from typing import Type

from ai_tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Singleton registry for all AI tools.

    Tools register themselves via the @register_tool decorator.
    Falcon AI and internal evaluation workflows query this registry. The
    standalone MCP has its own generated OpenAPI registry.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._categories: dict[str, list[str]] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance.

        Idempotent: a repeat registration of the same tool class (same
        ``name`` and same class identity) is a no-op. Module-level
        ``@register_tool`` decorators fire on every import, and
        Granian's ``--reload`` loader plus Django's app-ready hook can
        end up importing the same tool module twice in the same
        process — raising here crashes the whole app on a benign
        re-import. Tools registered under the SAME name but a
        DIFFERENT class still raise, since that's a real collision.
        """
        existing = self._tools.get(tool.name)
        if existing is not None:
            if type(existing) is type(tool):
                logger.debug(
                    "Tool '%s' re-registered (same class) — ignoring",
                    tool.name,
                )
                return
            raise ValueError(
                f"Tool '{tool.name}' is already registered by a different "
                f"class ({type(existing).__module__}.{type(existing).__name__} "
                f"vs {type(tool).__module__}.{type(tool).__name__})"
            )

        self._tools[tool.name] = tool

        if tool.category not in self._categories:
            self._categories[tool.category] = []
        self._categories[tool.category].append(tool.name)

        logger.debug(f"Registered tool: {tool.name} (category: {tool.category})")

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_all(self) -> list[BaseTool]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_by_category(self, category: str) -> list[BaseTool]:
        """List tools in a specific category."""
        tool_names = self._categories.get(category, [])
        return [self._tools[name] for name in tool_names]

    def categories(self) -> list[str]:
        """List all categories."""
        return list(self._categories.keys())

    def count(self) -> int:
        """Total number of registered tools."""
        return len(self._tools)

    def clear(self) -> None:
        """Clear all tools. Used in tests."""
        self._tools.clear()
        self._categories.clear()


# Module-level singleton
registry = ToolRegistry()


def register_tool(cls: Type[BaseTool]) -> Type[BaseTool]:
    """Decorator to register a tool class.

    Usage:
        @register_tool
        class WhoamiTool(BaseTool):
            name = "whoami"
            ...
    """
    instance = cls()
    registry.register(instance)
    return cls
