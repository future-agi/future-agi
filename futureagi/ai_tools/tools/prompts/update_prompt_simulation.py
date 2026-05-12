from typing import List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_simulation,
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)
from ai_tools.tools.simulation._utils import resolve_scenarios


class UpdatePromptSimulationInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    simulation_id: str = Field(
        default="",
        description="Simulation UUID or exact name. Omit to list candidates.",
    )
    name: Optional[str] = Field(
        default=None,
        description="New name (1-255 characters)",
        max_length=255,
    )
    description: Optional[str] = Field(default=None, description="New description")
    prompt_version_id: Optional[str] = Field(
        default=None,
        description="New prompt version ID, version string (e.g. 'v1'), default, or latest",
    )
    scenario_ids: Optional[List[str]] = Field(
        default=None,
        description="New list of scenario UUIDs or names. Replaces existing scenarios.",
    )
    enable_tool_evaluation: Optional[bool] = Field(
        default=None,
        description="Enable or disable automatic tool evaluation",
    )

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Name cannot be empty or whitespace only.")
            return stripped
        return v


@register_tool
class UpdatePromptSimulationTool(BaseTool):
    name = "update_prompt_simulation"
    description = (
        "Updates an existing prompt simulation run. "
        "Can change name, description, prompt version, scenarios, or tool evaluation setting."
    )
    category = "prompts"
    input_model = UpdatePromptSimulationInput

    def execute(
        self, params: UpdatePromptSimulationInput, context: ToolContext
    ) -> ToolResult:

        from django.db import transaction

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        run_test, simulation_result = resolve_prompt_simulation(
            template,
            params.simulation_id,
            "Prompt Simulation Required",
        )
        if simulation_result:
            return simulation_result

        updated_fields = []

        with transaction.atomic():
            if params.name is not None:
                run_test.name = params.name
                updated_fields.append("name")

            if params.description is not None:
                run_test.description = params.description
                updated_fields.append("description")

            if params.prompt_version_id is not None:
                prompt_version, version_result = resolve_prompt_version(
                    template,
                    params.prompt_version_id,
                    "Prompt Version Required",
                )
                if version_result:
                    return version_result
                run_test.prompt_version = prompt_version
                updated_fields.append("prompt_version")

            if params.scenario_ids is not None:
                scenarios, scenario_result = resolve_scenarios(
                    params.scenario_ids,
                    context,
                    title="Scenarios Required For Prompt Simulation",
                )
                if scenario_result:
                    return scenario_result
                if not scenarios:
                    return ToolResult.validation_error(
                        "At least one scenario is required for a prompt simulation."
                    )
                run_test.scenarios.set(scenarios)
                updated_fields.append("scenarios")

            if params.enable_tool_evaluation is not None:
                run_test.enable_tool_evaluation = params.enable_tool_evaluation
                updated_fields.append("enable_tool_evaluation")

            if not updated_fields:
                return ToolResult.error(
                    "No fields provided to update.",
                    error_code="VALIDATION_ERROR",
                )

            run_test.save()

        info = key_value_block(
            [
                ("ID", f"`{run_test.id}`"),
                ("Name", run_test.name),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Updated At", format_datetime(run_test.updated_at)),
            ]
        )

        content = section("Prompt Simulation Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(run_test.id),
                "name": run_test.name,
                "updated_fields": updated_fields,
            },
        )
