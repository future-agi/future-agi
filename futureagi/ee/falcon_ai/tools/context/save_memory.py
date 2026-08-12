from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool


class SaveMemoryInput(PydanticBaseModel):
    key: str = Field(
        description="Descriptive key for the memory (e.g., 'primary_model', 'main_dataset')"
    )
    value: str = Field(description="The information to remember")


@register_tool
class SaveMemoryTool(BaseTool):
    name = "save_memory"
    description = (
        "Save important context about this workspace to memory. "
        "Use when the user says 'remember that...' or when you discover "
        "crucial setup information. This is an internal tool — the user "
        "interacts via natural language."
    )
    category = "context"
    input_model = SaveMemoryInput

    def execute(self, params: SaveMemoryInput, context: ToolContext) -> ToolResult:
        from ee.falcon_ai.models import FalconMemory

        memory, created = FalconMemory.objects.update_or_create(
            workspace=context.workspace,
            key=params.key,
            defaults={
                "value": params.value,
                "source": "agent",
                "organization": context.organization,
                "created_by": context.user,
            },
        )
        action = "Saved" if created else "Updated"
        return ToolResult(
            content=f"{action} memory: **{params.key}** = {params.value}",
            data={"key": params.key, "value": params.value, "action": action.lower()},
        )
