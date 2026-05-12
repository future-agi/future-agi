from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_simulation,
    resolve_prompt_template_for_tool,
)


class DeletePromptSimulationInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    simulation_id: str = Field(
        default="",
        description="Simulation UUID or exact name. Omit to list candidates.",
    )
    dry_run: bool = Field(default=True, description="Preview delete impact only.")
    confirm_delete: bool = Field(
        default=False,
        description="Must be true with dry_run=false to perform the delete.",
    )


@register_tool
class DeletePromptSimulationTool(BaseTool):
    name = "delete_prompt_simulation"
    description = (
        "Soft-deletes a prompt simulation run. "
        "The simulation and its executions will no longer appear in listings."
    )
    category = "prompts"
    input_model = DeletePromptSimulationInput

    def execute(
        self, params: DeletePromptSimulationInput, context: ToolContext
    ) -> ToolResult:
        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        sim, simulation_result = resolve_prompt_simulation(
            template,
            params.simulation_id,
            "Prompt Simulation Required",
        )
        if simulation_result:
            return simulation_result

        if params.dry_run or not params.confirm_delete:
            info = key_value_block(
                [
                    ("Simulation", sim.name),
                    ("Simulation ID", f"`{sim.id}`"),
                    ("Template", template.name),
                    (
                        "Required To Delete",
                        "`dry_run=false` and `confirm_delete=true`",
                    ),
                ]
            )
            return ToolResult(
                content=section("Prompt Simulation Delete Preview", info),
                data={
                    "simulation_id": str(sim.id),
                    "name": sim.name,
                    "dry_run": True,
                    "requires_confirm_delete": True,
                },
            )

        sim.delete()

        return ToolResult(
            content=section(
                "Simulation Deleted",
                f"Prompt simulation **{sim.name}** has been deleted.",
            ),
            data={"simulation_id": str(sim.id), "name": sim.name},
        )
