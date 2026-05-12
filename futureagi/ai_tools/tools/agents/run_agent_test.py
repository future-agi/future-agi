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
from ai_tools.tools.agents._utils import candidate_run_tests_result, resolve_run_test
from ai_tools.tools.annotation_queues._utils import uuid_text


class RunAgentTestInput(PydanticBaseModel):
    run_test_id: Optional[str] = Field(
        default=None,
        description="The UUID or exact name of the RunTest (test definition) to execute",
    )
    scenario_ids: Optional[List[str]] = Field(
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

        from simulate.models.test_execution import TestExecution

        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Agent Test Required For Run",
        )
        if unresolved:
            return unresolved

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
        configured_scenario_ids = [
            str(sid)
            for sid in run_test.scenarios.filter(deleted=False).values_list(
                "id",
                flat=True,
            )
        ]
        if params.scenario_ids:
            invalid_scenario_ids = [
                str(sid) for sid in params.scenario_ids if not uuid_text(sid)
            ]
            if invalid_scenario_ids:
                return candidate_run_tests_result(
                    context,
                    "Invalid Scenario IDs",
                    "Scenario IDs must be UUIDs. Omit `scenario_ids` to run all "
                    "scenarios configured on the selected test.",
                )
            scenario_ids = [uuid_text(sid) for sid in params.scenario_ids]
            deduped_scenario_ids = []
            seen_scenario_ids = set()
            for scenario_id in scenario_ids:
                if scenario_id not in seen_scenario_ids:
                    deduped_scenario_ids.append(scenario_id)
                    seen_scenario_ids.add(scenario_id)
            scenario_ids = deduped_scenario_ids
            configured_set = set(configured_scenario_ids)
            unrelated_scenario_ids = [
                sid for sid in scenario_ids if sid not in configured_set
            ]
            if unrelated_scenario_ids:
                return candidate_run_tests_result(
                    context,
                    "Scenario IDs Not In Selected Test",
                    (
                        "Only scenarios configured on the selected test can be run. "
                        "Omit `scenario_ids` to run all configured scenarios, or use "
                        "one of the scenario IDs already attached to this test. "
                        "Rejected IDs: "
                        + ", ".join(f"`{sid}`" for sid in unrelated_scenario_ids)
                    ),
                )
        else:
            scenario_ids = configured_scenario_ids
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
