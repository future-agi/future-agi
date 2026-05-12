from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetScenarioInput(PydanticBaseModel):
    scenario_id: Optional[str] = Field(
        default=None,
        description="The UUID of the scenario to retrieve",
    )
    scenario_ref: Optional[str] = Field(
        default=None,
        description="Scenario ID or exact/fuzzy scenario name",
    )


@register_tool
class GetScenarioTool(BaseTool):
    name = "get_scenario"
    description = (
        "Returns detailed information about a specific test scenario, "
        "including name, type, source, dataset info, and associated agent."
    )
    category = "simulation"
    input_model = GetScenarioInput

    def execute(self, params: GetScenarioInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.simulation._utils import resolve_scenario

        scenario, error = resolve_scenario(
            params.scenario_id or params.scenario_ref,
            context,
            title="Scenario Needed",
        )
        if error:
            return error
        scenario = scenario.__class__.objects.select_related(
            "agent_definition", "dataset"
        ).get(
            id=scenario.id,
            organization=context.organization,
            deleted=False,
        )

        agent_name = (
            scenario.agent_definition.agent_name if scenario.agent_definition else "—"
        )
        dataset_name = scenario.dataset.name if scenario.dataset else "—"
        dataset_id = str(scenario.dataset.id) if scenario.dataset else None

        info = key_value_block(
            [
                ("ID", f"`{scenario.id}`"),
                ("Name", scenario.name),
                ("Type", scenario.scenario_type),
                ("Status", format_status(scenario.status) if scenario.status else "—"),
                ("Agent", agent_name),
                ("Source Type", scenario.source_type or "—"),
                ("Source", truncate(scenario.source, 500) if scenario.source else "—"),
                (
                    "Dataset",
                    f"{dataset_name} (`{dataset_id}`)" if dataset_id else "—",
                ),
                (
                    "Description",
                    (
                        truncate(scenario.description, 300)
                        if scenario.description
                        else "—"
                    ),
                ),
                ("Created", format_datetime(scenario.created_at)),
                ("Updated", format_datetime(scenario.updated_at)),
            ]
        )

        content = section(f"Scenario: {scenario.name}", info)

        # Metadata
        if scenario.metadata:
            content += "\n\n### Metadata\n\n"
            content += f"```json\n{truncate(str(scenario.metadata), 500)}\n```"

        data = {
            "id": str(scenario.id),
            "name": scenario.name,
            "type": scenario.scenario_type,
            "agent": agent_name,
            "source_type": scenario.source_type,
            "dataset_id": dataset_id,
        }

        return ToolResult(content=content, data=data)
