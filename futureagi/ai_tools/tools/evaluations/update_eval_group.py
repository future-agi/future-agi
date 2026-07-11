from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class UpdateEvalGroupInput(PydanticBaseModel):
    eval_group_id: UUID = Field(description="The UUID of the eval group to update")
    name: str | None = Field(
        default=None,
        description="New name for the eval group",
        min_length=1,
        max_length=255,
    )
    description: str | None = Field(
        default=None, description="New description for the eval group"
    )


@register_tool
class UpdateEvalGroupTool(BaseTool):
    name = "update_eval_group"
    description = (
        "Updates an evaluation group's name and/or description. "
        "At least one of name or description must be provided."
    )
    category = "evaluations"
    input_model = UpdateEvalGroupInput

    def execute(self, params: UpdateEvalGroupInput, context: ToolContext) -> ToolResult:

        from model_hub.models.eval_groups import EvalGroup

        if params.name is None and params.description is None:
            return ToolResult.error(
                "At least one of 'name' or 'description' must be provided.",
                error_code="VALIDATION_ERROR",
            )

        try:
            group = EvalGroup.objects.get(
                id=params.eval_group_id,
                organization=context.organization,
            )
        except EvalGroup.DoesNotExist:
            return ToolResult.not_found("Eval Group", str(params.eval_group_id))

        # Check name uniqueness
        if params.name:
            if (
                EvalGroup.objects.filter(
                    name=params.name,
                    deleted=False,
                    workspace=context.workspace,
                )
                .exclude(id=params.eval_group_id)
                .exists()
            ):
                return ToolResult.error(
                    f"An eval group named '{params.name}' already exists in this workspace.",
                    error_code="VALIDATION_ERROR",
                )
            group.name = params.name

        if params.description is not None:
            group.description = params.description

        group.save()

        info = key_value_block(
            [
                ("ID", f"`{group.id}`"),
                ("Name", group.name),
                ("Description", group.description or "—"),
            ]
        )

        return ToolResult(
            content=section("Eval Group Updated", info),
            data={"id": str(group.id), "name": group.name},
        )
