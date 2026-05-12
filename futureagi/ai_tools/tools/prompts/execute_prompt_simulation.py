from typing import List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_simulation,
    resolve_prompt_template_for_tool,
)
from ai_tools.tools.simulation._utils import resolve_scenarios


class ExecutePromptSimulationInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    simulation_id: str = Field(
        default="",
        description="Simulation UUID or exact name. Omit to list candidates.",
    )
    scenario_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of specific scenario UUIDs or names to execute. "
            "If omitted, all scenarios configured in the simulation are used."
        ),
    )
    select_all: bool = Field(
        default=False,
        description=(
            "If true with scenario_ids, run all scenarios EXCEPT those IDs. "
            "If true without scenario_ids, run all scenarios."
        ),
    )


@register_tool
class ExecutePromptSimulationTool(BaseTool):
    name = "execute_prompt_simulation"
    description = (
        "Triggers execution of a prompt simulation run. "
        "Creates a TestExecution and starts the simulation workflow. "
        "Returns the execution ID for tracking."
    )
    category = "prompts"
    input_model = ExecutePromptSimulationInput

    def execute(
        self, params: ExecutePromptSimulationInput, context: ToolContext
    ) -> ToolResult:
        from simulate.services.test_executor import TestExecutor

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

        # Validate prompt version still exists and is not deleted
        if not run_test.prompt_version or run_test.prompt_version.deleted:
            return ToolResult.validation_error(
                "Prompt version has been deleted. "
                "Please update the simulation with a valid version."
            )

        # Get all available scenario IDs linked to this run test (not deleted)
        all_scenario_ids = list(
            run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
        )
        requested_scenario_ids = None
        if params.scenario_ids:
            scenarios, scenario_result = resolve_scenarios(
                params.scenario_ids,
                context,
                title="Scenarios Required For Prompt Simulation",
            )
            if scenario_result:
                return scenario_result
            requested_scenario_ids = [str(scenario.id) for scenario in scenarios]

        # Determine which scenarios to execute
        if params.select_all:
            if requested_scenario_ids:
                exclude_set = set(requested_scenario_ids)
                final_scenario_ids = [
                    str(sid) for sid in all_scenario_ids if str(sid) not in exclude_set
                ]
            else:
                final_scenario_ids = [str(sid) for sid in all_scenario_ids]
        else:
            if requested_scenario_ids:
                available = {str(sid) for sid in all_scenario_ids}
                final_scenario_ids = [
                    sid for sid in requested_scenario_ids if sid in available
                ]
            else:
                final_scenario_ids = [str(sid) for sid in all_scenario_ids]

        if not final_scenario_ids:
            return ToolResult.validation_error(
                "No valid scenarios available for execution. "
                "Please add at least one scenario to the simulation."
            )

        test_executor = TestExecutor()
        result = test_executor.execute_test(
            run_test_id=str(run_test.id),
            user_id=str(context.user_id),
            scenario_ids=final_scenario_ids,
            simulator_id=None,
        )

        if not result["success"]:
            return ToolResult.error(
                result.get("error", "Failed to start simulation execution"),
                error_code="EXECUTION_ERROR",
            )

        info = key_value_block(
            [
                ("Execution ID", f"`{result['execution_id']}`"),
                ("Simulation", run_test.name),
                ("Template", template.name),
                (
                    "Scenarios",
                    str(result.get("total_scenarios", len(final_scenario_ids))),
                ),
                ("Status", format_status(result.get("status", "pending"))),
            ]
        )

        content = section("Prompt Simulation Started", info)
        content += "\n\n_Simulation is running asynchronously. Use `get_prompt_simulation` to check status._"

        return ToolResult(
            content=content,
            data={
                "execution_id": result["execution_id"],
                "run_test_id": str(run_test.id),
                "template_id": str(template.id),
                "scenarios": len(final_scenario_ids),
                "status": result.get("status", "pending"),
            },
        )
