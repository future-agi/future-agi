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


class UpdateRunTestInput(PydanticBaseModel):
    run_test_id: UUID | None = Field(
        default=None, description="The UUID of the run test to update"
    )
    name: str | None = Field(default=None, description="New name")
    description: str | None = Field(default=None, description="New description")
    scenario_ids: list[UUID] | None = Field(
        default=None,
        description="New list of scenario UUIDs (replaces existing, at least one required)",
        min_length=1,
    )
    simulator_agent_id: UUID | None = Field(
        default=None, description="New simulator agent UUID"
    )
    agent_version_id: UUID | None = Field(
        default=None,
        description="UUID of the agent version to use for this test suite",
    )
    enable_tool_evaluation: bool | None = Field(
        default=None,
        description="Enable or disable automatic tool evaluation for this test suite",
    )


@register_tool
class UpdateRunTestTool(BaseTool):
    name = "update_run_test"
    description = (
        "Updates an existing test suite (RunTest). "
        "Can change name, scenarios, or simulator agent."
    )
    category = "simulation"
    input_model = UpdateRunTestInput

    def _candidate_run_tests_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from simulate.models.run_test import RunTest

        run_tests = RunTest.objects.filter(
            organization=context.organization, deleted=False
        ).order_by("-created_at")[:10]
        rows = [f"- `{run_test.id}` — {run_test.name}" for run_test in run_tests]
        return ToolResult(
            content=section(
                "Run Test Required",
                (
                    (message + "\n\n" if message else "")
                    + "Provide `run_test_id` and at least one field to update.\n\n"
                    + ("\n".join(rows) if rows else "No run tests found.")
                ),
            ),
            data={
                "requires_run_test_id": True,
                "run_tests": [
                    {"id": str(run_test.id), "name": run_test.name}
                    for run_test in run_tests
                ],
            },
        )

    def execute(self, params: UpdateRunTestInput, context: ToolContext) -> ToolResult:

        from simulate.models.run_test import RunTest

        if params.run_test_id is None:
            return self._candidate_run_tests_result(context)

        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return self._candidate_run_tests_result(
                context, f"Run test `{params.run_test_id}` was not found."
            )

        updated_fields = []

        if params.name is not None:
            run_test.name = params.name
            updated_fields.append("name")

        if params.description is not None:
            run_test.description = params.description
            updated_fields.append("description")

        if params.simulator_agent_id is not None:
            from simulate.models.simulator_agent import SimulatorAgent

            try:
                simulator_agent = SimulatorAgent.objects.get(
                    id=params.simulator_agent_id,
                    organization=context.organization,
                )
                run_test.simulator_agent = simulator_agent
                updated_fields.append("simulator_agent")
            except SimulatorAgent.DoesNotExist:
                return ToolResult.not_found(
                    "Simulator Agent", str(params.simulator_agent_id)
                )

        if params.agent_version_id is not None:
            from simulate.models.agent_version import AgentVersion

            try:
                agent_version = AgentVersion.objects.get(
                    id=params.agent_version_id,
                    deleted=False,
                    organization=context.organization,
                )
                run_test.agent_version = agent_version
                updated_fields.append("agent_version")
            except AgentVersion.DoesNotExist:
                return ToolResult.not_found(
                    "Agent Version", str(params.agent_version_id)
                )

        if params.enable_tool_evaluation is not None:
            run_test.enable_tool_evaluation = params.enable_tool_evaluation
            updated_fields.append("enable_tool_evaluation")

        if updated_fields:
            save_fields = [
                f
                for f in updated_fields
                if f not in ("simulator_agent", "agent_version")
            ]
            if "simulator_agent" in updated_fields:
                save_fields.append("simulator_agent_id")
            if "agent_version" in updated_fields:
                save_fields.append("agent_version_id")
            run_test.save(update_fields=save_fields + ["updated_at"])

        if params.scenario_ids is not None:
            from simulate.models.scenarios import Scenarios

            scenarios = Scenarios.objects.filter(
                id__in=params.scenario_ids, organization=context.organization
            )
            if scenarios.count() != len(params.scenario_ids):
                found_ids = {str(s.id) for s in scenarios}
                missing = [
                    str(sid) for sid in params.scenario_ids if str(sid) not in found_ids
                ]
                return ToolResult.error(
                    f"Scenarios not found: {', '.join(missing)}",
                    error_code="NOT_FOUND",
                )
            run_test.scenarios.set(scenarios)
            updated_fields.append("scenarios")

        if not updated_fields:
            return ToolResult(
                content=section(
                    "Run Test Update Requirements",
                    (
                        f"Run test `{run_test.id}` was found. Provide at least one "
                        "field to update: `name`, `description`, `scenario_ids`, "
                        "`simulator_agent_id`, `agent_version_id`, or "
                        "`enable_tool_evaluation`."
                    ),
                ),
                data={"run_test_id": str(run_test.id), "requires_update_fields": True},
            )

        info = key_value_block(
            [
                ("ID", f"`{run_test.id}`"),
                ("Name", run_test.name),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Updated At", format_datetime(run_test.updated_at)),
            ]
        )

        content = section("Test Suite Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(run_test.id),
                "name": run_test.name,
                "updated_fields": updated_fields,
            },
        )
