from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateRunTestInput(PydanticBaseModel):
    name: str = Field(description="Name of the test suite")
    agent_id: UUID = Field(description="The UUID of the agent definition to test")
    scenario_ids: List[UUID] = Field(
        description="List of scenario UUIDs to include (at least one required)",
        min_length=1,
    )
    simulator_agent_id: Optional[UUID] = Field(
        default=None, description="UUID of the simulator agent to use"
    )
    description: Optional[str] = Field(
        default=None, description="Description of the test suite"
    )
    agent_version_id: Optional[UUID] = Field(
        default=None, description="UUID of a specific agent version to use"
    )
    evaluations_config: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "List of evaluation config dicts. Each dict should contain "
            "'template_id' (UUID of EvalTemplate) and optionally 'name', "
            "'config', 'mapping', 'filters', 'error_localizer', 'model', 'eval_group'."
        ),
    )
    eval_config_ids: Optional[List[UUID]] = Field(
        default=None,
        description="List of existing SimulateEvalConfig UUIDs to link to this test",
    )
    enable_tool_evaluation: bool = Field(
        default=False,
        description="Enable automatic tool evaluation for this test suite",
    )


@register_tool
class CreateRunTestTool(BaseTool):
    name = "create_run_test"
    description = (
        "Creates a new test suite (RunTest) that links an agent definition "
        "with scenarios and an optional simulator agent."
    )
    category = "simulation"
    input_model = CreateRunTestInput

    def execute(self, params: CreateRunTestInput, context: ToolContext) -> ToolResult:

        from django.db import transaction

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion
        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.run_test import RunTest
        from simulate.models.scenarios import Scenarios

        try:
            agent = AgentDefinition.objects.get(
                id=params.agent_id, organization=context.organization
            )
        except AgentDefinition.DoesNotExist:
            return ToolResult.not_found("Agent", str(params.agent_id))

        # Validate scenarios exist
        scenarios = Scenarios.objects.filter(
            id__in=params.scenario_ids, organization=context.organization
        )
        if scenarios.count() != len(params.scenario_ids):
            found_ids = set(str(s.id) for s in scenarios)
            missing = [
                str(sid) for sid in params.scenario_ids if str(sid) not in found_ids
            ]
            return ToolResult.error(
                f"Scenarios not found: {', '.join(missing)}",
                error_code="NOT_FOUND",
            )

        # Optional simulator agent
        simulator_agent = None
        if params.simulator_agent_id:
            from simulate.models.simulator_agent import SimulatorAgent

            try:
                simulator_agent = SimulatorAgent.objects.get(
                    id=params.simulator_agent_id,
                    organization=context.organization,
                )
            except SimulatorAgent.DoesNotExist:
                return ToolResult.not_found(
                    "Simulator Agent", str(params.simulator_agent_id)
                )

        # Resolve agent version: use provided, or auto-resolve to active/latest
        agent_version = None
        if params.agent_version_id:
            try:
                agent_version = AgentVersion.objects.get(
                    id=params.agent_version_id,
                    deleted=False,
                    organization=context.organization,
                )
            except AgentVersion.DoesNotExist:
                return ToolResult.not_found(
                    "Agent Version", str(params.agent_version_id)
                )
        else:
            # Auto-resolve: prefer active version, fall back to latest
            agent_version = (
                AgentVersion.objects.filter(
                    agent_definition=agent,
                    deleted=False,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                .order_by("-version_number")
                .first()
            )
            if not agent_version:
                agent_version = (
                    AgentVersion.objects.filter(
                        agent_definition=agent,
                        deleted=False,
                    )
                    .order_by("-version_number")
                    .first()
                )

        # The eval config name is unique per run test at the DB level, so a
        # payload with two entries resolving to the same name would otherwise
        # fail partway through the loop below, after the RunTest already exists.
        seen_eval_names = set()
        for eval_config_data in params.evaluations_config or []:
            template_id = eval_config_data.get("template_id")
            eval_name = eval_config_data.get("name", f"Eval-{template_id}")
            if eval_name in seen_eval_names:
                return ToolResult.validation_error(
                    f"An evaluation config with the name '{eval_name}' already exists in this run test. Please use a different name."
                )
            seen_eval_names.add(eval_name)

        with transaction.atomic():
            run_test = RunTest.objects.create(
                name=params.name,
                description=params.description or "",
                agent_definition=agent,
                agent_version=agent_version,
                simulator_agent=simulator_agent,
                organization=context.organization,
                workspace=context.workspace,
                enable_tool_evaluation=params.enable_tool_evaluation,
            )

            # Add scenarios (M2M)
            run_test.scenarios.set(scenarios)

            # Create SimulateEvalConfig instances from evaluations_config
            eval_count = 0
            if params.evaluations_config:
                from ai_tools.tools.simulation.list_eval_mapping_options import (
                    auto_assign_mapping,
                    get_valid_fields,
                )
                from model_hub.models.evals_metric import EvalTemplate
                from model_hub.utils.function_eval_params import (
                    normalize_eval_runtime_config,
                )

                agent_type = agent.agent_type or "voice"

                for eval_config_data in params.evaluations_config:
                    template_id = eval_config_data.get("template_id")
                    if not template_id:
                        continue
                    try:
                        eval_template = EvalTemplate.no_workspace_objects.get(
                            id=template_id
                        )

                        # Get required keys from the eval template config
                        template_config = eval_template.config or {}
                        required_keys = (
                            template_config.get("required_keys", [])
                            if isinstance(template_config, dict)
                            else []
                        )

                        mapping = eval_config_data.get("mapping") or {}

                        # Validate provided mapping values
                        if mapping:
                            valid_fields = get_valid_fields(agent_type)
                            invalid_values = [
                                v
                                for v in mapping.values()
                                if v and v not in valid_fields
                            ]
                            if invalid_values:
                                # Skip dataset column IDs (UUIDs) from validation
                                invalid_values = [
                                    v
                                    for v in invalid_values
                                    if len(v) < 36 or "-" not in v
                                ]
                            if invalid_values:
                                return ToolResult.validation_error(
                                    f"Invalid mapping values for eval '{eval_template.name}': "
                                    f"{', '.join(invalid_values)}. "
                                    f"Valid options: {', '.join(sorted(get_valid_fields(agent_type)))}"
                                )

                        # Auto-assign mapping if not provided but required keys exist
                        if not mapping and required_keys:
                            mapping = auto_assign_mapping(required_keys, agent_type)

                        SimulateEvalConfig.objects.create(
                            eval_template=eval_template,
                            name=eval_config_data.get("name", f"Eval-{template_id}"),
                            config=normalize_eval_runtime_config(
                                eval_template.config,
                                eval_config_data.get("config", {}),
                            ),
                            mapping=mapping,
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
                        continue

            # Link existing eval configs by ID
            if params.eval_config_ids:
                existing_configs = SimulateEvalConfig.objects.filter(
                    id__in=params.eval_config_ids,
                    run_test__organization=context.organization,
                )
                eval_count += existing_configs.count()

        info = key_value_block(
            [
                ("ID", f"`{run_test.id}`"),
                ("Name", run_test.name),
                ("Agent", agent.agent_name),
                (
                    "Agent Version",
                    str(agent_version.version_number) if agent_version else "—",
                ),
                ("Scenarios", str(scenarios.count())),
                ("Eval Configs", str(eval_count)),
                ("Simulator Agent", simulator_agent.name if simulator_agent else "—"),
                ("Created", format_datetime(run_test.created_at)),
            ]
        )

        content = section("Test Suite Created", info)

        return ToolResult(
            content=content,
            data={
                "id": str(run_test.id),
                "name": run_test.name,
                "agent_id": str(agent.id),
                "scenario_count": scenarios.count(),
                "eval_config_count": eval_count,
            },
        )
