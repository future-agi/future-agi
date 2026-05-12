from typing import Any, Dict, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)
from ai_tools.tools.simulation._utils import resolve_scenarios


class CreatePromptSimulationInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    name: str = Field(
        default="",
        description="Name of the simulation run",
        max_length=255,
    )
    prompt_version_id: str = Field(
        default="",
        description="Prompt version ID, version string (e.g. 'v1'), default, or latest",
    )
    scenario_ids: List[str] = Field(
        default_factory=list,
        description="List of scenario UUIDs or names to include. Omit to list candidates.",
    )
    description: Optional[str] = Field(default=None, description="Optional description")
    evaluations_config: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "List of evaluation config dicts. Each dict should contain "
            "'template_id' (UUID of EvalTemplate) and optionally 'name', "
            "'config', 'mapping', 'filters', 'error_localizer', 'model', 'eval_group'."
        ),
    )
    enable_tool_evaluation: bool = Field(
        default=False,
        description="Enable automatic tool evaluation for this simulation run",
    )


@register_tool
class CreatePromptSimulationTool(BaseTool):
    name = "create_prompt_simulation"
    description = (
        "Creates a new prompt simulation run for a prompt template. "
        "Links the template with a specific version and scenarios for testing."
    )
    category = "prompts"
    input_model = CreatePromptSimulationInput

    def execute(
        self, params: CreatePromptSimulationInput, context: ToolContext
    ) -> ToolResult:

        from django.core.exceptions import ValidationError
        from django.db import transaction

        from model_hub.models.evals_metric import EvalTemplate
        from simulate.models import RunTest, SimulateEvalConfig

        if not params.name.strip():
            return ToolResult.needs_input(
                "Simulation name is required before a prompt simulation can be created.",
                missing_fields=["name"],
            )

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        prompt_version, version_result = resolve_prompt_version(
            template,
            params.prompt_version_id,
            "Prompt Version Required",
        )
        if version_result:
            return version_result

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

        with transaction.atomic():
            workspace = template.workspace

            run_test = RunTest.objects.create(
                name=params.name,
                description=params.description or "",
                source_type="prompt",
                prompt_template=template,
                prompt_version=prompt_version,
                agent_definition=None,
                agent_version=None,
                simulator_agent=None,
                organization=context.organization,
                workspace=workspace,
                enable_tool_evaluation=params.enable_tool_evaluation,
            )

            run_test.scenarios.set(scenarios)

            # Handle evaluations config
            eval_count = 0
            skipped_evals = []
            if params.evaluations_config:
                for eval_config_data in params.evaluations_config:
                    template_id = eval_config_data.get("template_id")
                    if not template_id:
                        continue
                    try:
                        eval_template = EvalTemplate.no_workspace_objects.get(
                            id=template_id
                        )
                        SimulateEvalConfig.objects.create(
                            eval_template=eval_template,
                            name=eval_config_data.get("name", f"Eval-{template_id}"),
                            config=eval_config_data.get("config", {}),
                            mapping=eval_config_data.get("mapping", {}),
                            run_test=run_test,
                            filters=eval_config_data.get("filters", {}),
                            error_localizer=eval_config_data.get(
                                "error_localizer", False
                            ),
                            model=eval_config_data.get("model", None),
                            eval_group_id=eval_config_data.get("eval_group", None),
                        )
                        eval_count += 1
                    except EvalTemplate.DoesNotExist:
                        skipped_evals.append(str(template_id))

        info = key_value_block(
            [
                ("ID", f"`{run_test.id}`"),
                ("Name", run_test.name),
                ("Template", template.name),
                ("Version", prompt_version.template_version),
                ("Scenarios", str(len(scenarios))),
                ("Eval Configs", str(eval_count)),
                ("Created", format_datetime(run_test.created_at)),
            ]
        )

        content = section("Prompt Simulation Created", info)

        if skipped_evals:
            content += (
                f"\n\n_Skipped {len(skipped_evals)} eval template(s) "
                f"not found: {', '.join(skipped_evals)}_"
            )

        return ToolResult(
            content=content,
            data={
                "id": str(run_test.id),
                "name": run_test.name,
                "template_id": str(template.id),
                "version": prompt_version.template_version,
                "scenario_count": len(scenarios),
                "eval_config_count": eval_count,
                "skipped_evals": skipped_evals,
            },
        )
