from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_simulation,
    resolve_prompt_template_for_tool,
)


class GetPromptSimulationInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    simulation_id: str = Field(
        default="",
        description="Simulation UUID or exact name. Omit to list candidates.",
    )


@register_tool
class GetPromptSimulationTool(BaseTool):
    name = "get_prompt_simulation"
    description = (
        "Returns detailed information about a specific prompt simulation run "
        "including its scenarios, eval configs, and recent execution results."
    )
    category = "prompts"
    input_model = GetPromptSimulationInput

    def execute(
        self, params: GetPromptSimulationInput, context: ToolContext
    ) -> ToolResult:
        from simulate.models import SimulateEvalConfig
        from simulate.models.test_execution import TestExecution

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

        version_label = (
            sim.prompt_version.template_version if sim.prompt_version else "—"
        )

        info = key_value_block(
            [
                ("ID", f"`{sim.id}`"),
                ("Name", sim.name),
                ("Description", sim.description or "—"),
                ("Template", template.name),
                ("Version", version_label),
                (
                    "Version ID",
                    f"`{sim.prompt_version_id}`" if sim.prompt_version_id else "—",
                ),
                (
                    "Tool Evaluation",
                    "Enabled" if sim.enable_tool_evaluation else "Disabled",
                ),
                ("Created", format_datetime(sim.created_at)),
            ]
        )
        content = section(f"Simulation: {sim.name}", info)

        # Scenarios
        scenarios = sim.scenarios.filter(deleted=False)
        if scenarios.exists():
            scen_rows = []
            for s in scenarios:
                scen_rows.append(
                    [
                        f"`{str(s.id)}`",
                        truncate(s.name, 40),
                        s.scenario_type,
                        str(s.dataset_id) if s.dataset_id else "—",
                    ]
                )
            content += "\n\n### Scenarios\n\n"
            content += markdown_table(["ID", "Name", "Type", "Dataset"], scen_rows)

        # Eval configs
        eval_configs = SimulateEvalConfig.objects.filter(
            run_test=sim, deleted=False
        ).select_related("eval_template")
        if eval_configs.exists():
            eval_rows = []
            for ec in eval_configs:
                eval_name = ec.eval_template.name if ec.eval_template else "—"
                eval_rows.append(
                    [
                        ec.name or eval_name,
                        eval_name,
                        ec.model or "—",
                        ec.status or "—",
                    ]
                )
            content += "\n\n### Eval Configs\n\n"
            content += markdown_table(
                ["Name", "Template", "Model", "Status"], eval_rows
            )

        # Recent executions
        executions = TestExecution.objects.filter(run_test=sim, deleted=False).order_by(
            "-created_at"
        )[:5]

        if executions:
            exec_rows = []
            for ex in executions:
                duration = "—"
                if ex.started_at and ex.completed_at:
                    dur_sec = (ex.completed_at - ex.started_at).total_seconds()
                    duration = (
                        f"{dur_sec:.0f}s" if dur_sec < 60 else f"{dur_sec / 60:.1f}m"
                    )

                success_rate = "—"
                if ex.total_calls and ex.total_calls > 0:
                    rate = (ex.completed_calls / ex.total_calls) * 100
                    success_rate = f"{rate:.0f}%"

                exec_rows.append(
                    [
                        f"`{str(ex.id)}`",
                        format_status(ex.status),
                        f"{ex.completed_calls}/{ex.total_calls}",
                        success_rate,
                        duration,
                        format_datetime(ex.created_at),
                    ]
                )
            content += "\n\n### Recent Executions\n\n"
            content += markdown_table(
                ["ID", "Status", "Calls", "Success", "Duration", "Created"],
                exec_rows,
            )

        return ToolResult(
            content=content,
            data={
                "id": str(sim.id),
                "name": sim.name,
                "template_id": str(template.id),
                "version": version_label,
                "scenario_count": scenarios.count(),
            },
        )
