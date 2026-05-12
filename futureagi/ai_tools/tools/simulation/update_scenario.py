from typing import Any, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class UpdateScenarioInput(PydanticBaseModel):
    scenario_id: str = Field(
        default="",
        description="Scenario UUID or exact/fuzzy scenario name. Omit it to list candidates.",
    )
    name: Optional[str] = Field(
        default=None, max_length=255, description="New name for the scenario"
    )
    description: Optional[str] = Field(default=None, description="New description")
    source: Optional[str] = Field(default=None, description="New source content")
    graph: Optional[dict[str, Any]] = Field(
        default=None, description="Updated graph JSON data for graph-type scenarios"
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Updated prompt for the scenario's simulator agent",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.strip():
                raise ValueError("Name cannot be empty or just whitespace.")
            return v.strip()
        return v


@register_tool
class UpdateScenarioTool(BaseTool):
    name = "update_scenario"
    description = (
        "Updates an existing test scenario. Only provided fields will be changed."
    )
    category = "simulation"
    input_model = UpdateScenarioInput

    def execute(self, params: UpdateScenarioInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.simulation._utils import resolve_scenario

        scenario, unresolved = resolve_scenario(
            params.scenario_id,
            context,
            title="Scenario Required To Update",
        )
        if unresolved:
            return unresolved

        updated_fields = []
        if params.name is not None:
            scenario.name = params.name
            updated_fields.append("name")
        if params.description is not None:
            scenario.description = params.description
            updated_fields.append("description")
        if params.source is not None:
            scenario.source = params.source
            updated_fields.append("source")

        # Handle graph update through ScenarioGraph model
        if params.graph is not None:
            from simulate.models.scenario_graph import ScenarioGraph

            scenario_graph = (
                ScenarioGraph.objects.filter(scenario=scenario, is_active=True)
                .order_by("-created_at")
                .first()
            )

            if scenario_graph:
                graph_config = scenario_graph.graph_config or {}
                graph_config["graph_data"] = params.graph
                scenario_graph.graph_config = graph_config
                scenario_graph.save()
            else:
                ScenarioGraph.objects.create(
                    scenario=scenario,
                    name=f"{scenario.name} - Graph",
                    description=f"Graph for {scenario.name}",
                    organization=scenario.organization,
                    graph_config={
                        "graph_data": params.graph,
                        "source": "user_provided",
                    },
                )
            updated_fields.append("graph")

        # Handle prompt update through simulator agent
        if params.prompt is not None:
            if scenario.simulator_agent and not scenario.simulator_agent.deleted:
                scenario.simulator_agent.prompt = params.prompt
                scenario.simulator_agent.save()
                updated_fields.append("prompt")
            else:
                return ToolResult.error(
                    "Scenario does not have a simulator agent to update prompt.",
                    error_code="VALIDATION_ERROR",
                )

        if not updated_fields:
            return ToolResult.error(
                "No fields provided to update.",
                error_code="VALIDATION_ERROR",
            )

        scenario.save(
            update_fields=[
                f for f in updated_fields if f in ("name", "description", "source")
            ]
            + ["updated_at"]
        )

        info = key_value_block(
            [
                ("ID", f"`{scenario.id}`"),
                ("Name", scenario.name),
                ("Type", scenario.scenario_type),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Updated At", format_datetime(scenario.updated_at)),
            ]
        )

        content = section("Scenario Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(scenario.id),
                "name": scenario.name,
                "updated_fields": updated_fields,
            },
        )
