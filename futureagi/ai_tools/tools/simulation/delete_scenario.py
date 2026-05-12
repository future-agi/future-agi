from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.simulation._utils import resolve_scenario


class DeleteScenarioInput(PydanticBaseModel):
    scenario_id: str = Field(
        default="",
        description="Scenario name or UUID to delete. If omitted, candidates are returned.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms deletion.",
    )


@register_tool
class DeleteScenarioTool(BaseTool):
    name = "delete_scenario"
    description = "Soft-deletes a test scenario by marking it as deleted."
    category = "simulation"
    input_model = DeleteScenarioInput

    def execute(self, params: DeleteScenarioInput, context: ToolContext) -> ToolResult:
        from django.utils import timezone

        scenario, unresolved = resolve_scenario(
            params.scenario_id,
            context,
            title="Scenario Required To Delete",
        )
        if unresolved:
            return unresolved

        if not params.confirm_delete:
            info = key_value_block(
                [
                    ("ID", f"`{scenario.id}`"),
                    ("Name", scenario.name),
                    ("Status", "Awaiting confirmation"),
                ]
            )
            return ToolResult(
                content=section("Confirm Scenario Deletion", info),
                data={
                    "requires_confirmation": True,
                    "confirm_delete": True,
                    "id": str(scenario.id),
                    "name": scenario.name,
                },
            )

        scenario_name = scenario.name
        scenario.deleted = True
        scenario.deleted_at = timezone.now()
        scenario.save(update_fields=["deleted", "deleted_at", "updated_at"])

        info = key_value_block(
            [
                ("ID", f"`{scenario.id}`"),
                ("Name", scenario_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Scenario Deleted", info)

        return ToolResult(
            content=content,
            data={
                "id": str(scenario.id),
                "name": scenario_name,
                "deleted": True,
            },
        )
