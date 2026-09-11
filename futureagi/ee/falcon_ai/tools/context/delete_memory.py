from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool


class DeleteMemoryInput(PydanticBaseModel):
    key: str = Field(description="Key of the memory to delete")


@register_tool
class DeleteMemoryTool(BaseTool):
    name = "delete_memory"
    description = "Delete a workspace memory by key."
    category = "context"
    input_model = DeleteMemoryInput

    def execute(self, params: DeleteMemoryInput, context: ToolContext) -> ToolResult:
        from ee.falcon_ai.models import FalconMemory

        deleted_count, _ = FalconMemory.objects.filter(
            workspace=context.workspace,
            organization=context.organization,
            key=params.key,
        ).delete()

        if deleted_count:
            return ToolResult(content=f"Deleted memory: **{params.key}**")
        return ToolResult.not_found("memory", params.key)
