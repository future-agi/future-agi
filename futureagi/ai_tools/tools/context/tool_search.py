from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool, registry
from ai_tools.tool_aliases import alias_tool_names


class ToolSearchInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Tool name, action, entity, or workflow to search for.",
    )
    category: str | None = Field(
        default=None,
        description="Optional tool category filter, such as datasets, tracing, prompts, or simulation.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Maximum number of matching tools to return.",
    )


@register_tool
class ToolSearchTool(BaseTool):
    name = "tool_search"
    category = "context"
    description = (
        "Search available Falcon tools by name, category, or description. Use this "
        "when the needed tool is not currently loaded or when a workflow needs a "
        "specific capability."
    )
    input_model = ToolSearchInput

    def execute(self, params: ToolSearchInput, context: ToolContext) -> ToolResult:
        query = (params.query or "").strip().lower()
        category = (params.category or "").strip().lower()
        alias_tool_names = self._alias_tool_names(query)
        query_words = {
            word
            for word in re.split(r"[\s\-_,./]+", query)
            if len(word) > 1
        }

        scored = []
        for tool in registry.list_all():
            if tool.name == self.name:
                continue
            tool_category = getattr(tool, "category", "")
            if category and category not in tool_category.lower():
                continue

            searchable = " ".join(
                [
                    tool.name.replace("_", " "),
                    tool.name,
                    tool_category,
                    getattr(tool, "description", "") or "",
                ]
            ).lower()
            name_words = set(tool.name.lower().replace("_", " ").split())
            score = 0
            if not query_words:
                score = 1
            else:
                if tool.name in alias_tool_names:
                    score += 100 - alias_tool_names.index(tool.name)
                score += 8 * len(name_words & query_words)
                for word in query_words:
                    if word in tool.name.lower():
                        score += 5
                    if word in tool_category.lower():
                        score += 3
                    if word in searchable:
                        score += 1
            if score:
                scored.append((score, tool.name, tool))

        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = [tool for _, _, tool in scored[: params.limit]]

        if not matches:
            return ToolResult(
                content=(
                    "No matching tools found. Try a broader query or omit the category filter."
                ),
                data={"tool_names": []},
            )

        lines = [
            "Matching tools. These tool schemas will be available on the next step:",
        ]
        tool_names = []
        for tool in matches:
            tool_names.append(tool.name)
            description = (getattr(tool, "description", "") or "").strip()
            if len(description) > 180:
                description = description[:177].rstrip() + "..."
            lines.append(
                f"- `{tool.name}` ({getattr(tool, 'category', 'unknown')}): {description}"
            )

        return ToolResult(
            content="\n".join(lines),
            data={"tool_names": tool_names},
        )

    @staticmethod
    def _alias_tool_names(query: str) -> list[str]:
        return alias_tool_names(query)
