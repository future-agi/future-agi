from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.formatting import markdown_table
from ai_tools.registry import register_tool


@register_tool
class ListMemoriesTool(BaseTool):
    name = "list_memories"
    description = "List all saved workspace memories."
    category = "context"
    input_model = EmptyInput

    def execute(self, params: EmptyInput, context: ToolContext) -> ToolResult:
        from ee.falcon_ai.models import FalconMemory

        memories = FalconMemory.objects.filter(
            workspace=context.workspace,
            organization=context.organization,
        ).values("key", "value", "source", "created_at")[:50]

        if not memories:
            return ToolResult(content="No workspace memories saved yet.")

        rows = [[m["key"], m["value"][:100], m["source"]] for m in memories]
        headers = ["Key", "Value", "Source"]
        table = markdown_table(headers, rows)
        return ToolResult(
            content=f"**Workspace Memories** ({len(rows)}):\n\n{table}",
            data={"count": len(rows)},
        )
