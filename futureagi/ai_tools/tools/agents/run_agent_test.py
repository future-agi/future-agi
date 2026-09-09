from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class RunAgentTestInput(PydanticBaseModel):
    run_test_id: UUID = Field(
        description="The UUID of the RunTest (test definition) to execute"
    )
    scenario_ids: Optional[List[UUID]] = Field(
        default=None,
        description=(
            "Optional list of specific scenario UUIDs to execute. "
            "If omitted, all scenarios configured in the test suite are used."
        ),
    )


@register_tool
class RunAgentTestTool(BaseTool):
    name = "run_agent_test"
    description = (
        "Triggers execution of an agent test. Creates a TestExecution record "
        "and starts the test workflow. Returns the execution ID for tracking."
    )
    category = "agents"
    input_model = RunAgentTestInput

    def execute(self, params: RunAgentTestInput, context: ToolContext) -> ToolResult:
        from django.utils import timezone

        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution

        # Validate run test exists
        try:
            run_test = RunTest.objects.select_related(
                "agent_definition", "simulator_agent"
            ).get(id=params.run_test_id, organization=context.organization)
        except RunTest.DoesNotExist:
            return ToolResult.error(
                f"RunTest `{params.run_test_id}` not found.",
                error_code="NOT_FOUND",
            )

        agent_name = (
            run_test.agent_definition.agent_name if run_test.agent_definition else "—"
        )

        # Resolve agent_version: use run_test's version, or auto-resolve
        agent_version = run_test.agent_version
        if not agent_version and run_test.agent_definition:
            from simulate.models.agent_version import AgentVersion

            # Prefer active version, fall back to latest
            agent_version = (
                AgentVersion.objects.filter(
                    agent_definition=run_test.agent_definition,
                    deleted=False,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                .order_by("-version_number")
                .first()
            )
            if not agent_version:
                agent_version = (
                    AgentVersion.objects.filter(
                        agent_definition=run_test.agent_definition,
                        deleted=False,
                    )
                    .order_by("-version_number")
                    .first()
                )

        if not agent_version:
            return ToolResult.error(
                "No agent version found. Create an agent version before running tests.",
                error_code="VALIDATION_ERROR",
            )

        # Get scenarios - use provided scenario_ids or fall back to all configured
        if params.scenario_ids:
            scenario_ids = [sid for sid in params.scenario_ids]
        else:
            scenario_ids = list(
                run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
            )
        if not scenario_ids:
            return ToolResult.error(
                "At least one scenario is required to execute the test. "
                "Either provide scenario_ids or configure scenarios on the test suite.",
                error_code="VALIDATION_ERROR",
            )

        # Validate simulator agent is still available (not soft-deleted)
        simulator_agent = run_test.simulator_agent
        if simulator_agent and simulator_agent.deleted:
            return ToolResult.validation_error(
                "The simulator agent assigned to this test has been deleted. "
                "Please assign a new simulator agent before running."
            )

        # Apply the same deployment and plan gate as the HTTP execute view
        # before persisting or dispatching anything.
        from simulate.models.agent_definition import AgentDefinition

        if (
            run_test.agent_definition is not None
            and run_test.agent_definition.agent_type
            == AgentDefinition.AgentTypeChoices.VOICE
        ):
            from tfc.ee_gates import voice_sim_gate_response

            denial = voice_sim_gate_response(context.organization)
            if denial is not None:
                return ToolResult.error(
                    denial.data["message"],
                    data=denial.data,
                    error_code=denial.data["code"],
                )

        # Create test execution using ExecutionStatus enum
        execution = TestExecution(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            started_at=timezone.now(),
            total_scenarios=len(scenario_ids),
            scenario_ids=[str(sid) for sid in scenario_ids],
            total_calls=0,
            completed_calls=0,
            failed_calls=0,
            agent_definition=run_test.agent_definition,
            agent_version=agent_version,
            simulator_agent=simulator_agent,
        )
        execution.save()

        # Try to start via Temporal
        workflow_started = False
        try:
            from simulate.temporal.client import start_test_execution_workflow

            start_test_execution_workflow(
                test_execution_id=str(execution.id),
                run_test_id=str(run_test.id),
                org_id=str(context.organization.id),
                scenario_ids=[str(sid) for sid in scenario_ids],
                simulator_id=(str(simulator_agent.id) if simulator_agent else None),
            )
            execution.status = TestExecution.ExecutionStatus.RUNNING
            execution.save(update_fields=["status"])
            workflow_started = True
        except Exception as e:
            # Set status to FAILED so it doesn't stay stuck in PENDING
            execution.status = TestExecution.ExecutionStatus.FAILED
            execution.save(update_fields=["status"])
            return ToolResult.error(
                f"Failed to start test workflow: {str(e)}",
                error_code="WORKFLOW_ERROR",
            )

        info = key_value_block(
            [
                ("Execution ID", f"`{execution.id}`"),
                ("Test", run_test.name),
                ("Agent", agent_name),
                ("Scenarios", str(len(scenario_ids))),
                ("Status", format_status(execution.status)),
                ("Workflow", "Started" if workflow_started else "Queued"),
            ]
        )

        content = section("Agent Test Started", info)
        content += "\n\n_Test is running asynchronously. Use `get_agent` to check the test history._"

        return ToolResult(
            content=content,
            data={
                "execution_id": str(execution.id),
                "run_test_id": str(run_test.id),
                "agent": agent_name,
                "scenarios": len(scenario_ids),
                "status": execution.status,
            },
        )
