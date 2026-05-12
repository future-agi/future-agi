from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

VALID_RERUN_TYPES = ["eval_only", "call_and_eval"]


class RerunCallExecutionInput(PydanticBaseModel):
    call_execution_id: str = Field(
        default="", description="The UUID of the call execution to rerun"
    )
    rerun_type: str = Field(
        default="call_and_eval",
        description="Type of rerun: 'eval_only' (re-evaluate only) or 'call_and_eval' (full re-execution + evaluation). "
        "Chat/text agents only support 'eval_only'.",
    )

    @field_validator("rerun_type")
    @classmethod
    def validate_rerun_type(cls, v):
        if v not in VALID_RERUN_TYPES:
            raise ValueError(
                f"Invalid rerun_type: '{v}'. Must be one of: {VALID_RERUN_TYPES}"
            )
        return v


@register_tool
class RerunCallExecutionTool(BaseTool):
    name = "rerun_call_execution"
    description = (
        "Reruns a specific call execution. Resets the call to pending state "
        "and triggers re-execution. The previous state is preserved as a snapshot."
    )
    category = "simulation"
    input_model = RerunCallExecutionInput

    def _candidate_calls_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from simulate.models.test_execution import CallExecution

        calls = (
            CallExecution.objects.select_related("test_execution", "scenario")
            .filter(test_execution__run_test__organization=context.organization)
            .order_by("-created_at")[:10]
        )
        rows = [
            (
                f"- `{call.id}` — {call.status} "
                f"(scenario: {call.scenario.name if call.scenario else '—'})"
            )
            for call in calls
        ]
        return ToolResult(
            content=section(
                "Call Execution Required",
                (
                    (message + "\n\n" if message else "")
                    + "Provide `call_execution_id` to rerun a specific call execution.\n\n"
                    + ("\n".join(rows) if rows else "No call executions found.")
                ),
            ),
            data={
                "requires_call_execution_id": True,
                "call_executions": [
                    {
                        "id": str(call.id),
                        "status": call.status,
                        "test_execution_id": (
                            str(call.test_execution_id)
                            if call.test_execution_id
                            else None
                        ),
                    }
                    for call in calls
                ],
            },
        )

    def execute(
        self, params: RerunCallExecutionInput, context: ToolContext
    ) -> ToolResult:

        from django.core.exceptions import ValidationError
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.test_execution import (
            CallExecution,
            CallExecutionSnapshot,
            TestExecution,
        )

        from simulate.temporal.client import rerun_call_executions

        if not params.call_execution_id:
            return self._candidate_calls_result(context)

        try:
            call = CallExecution.objects.select_related(
                "test_execution",
                "test_execution__run_test",
                "test_execution__run_test__agent_definition",
                "scenario",
            ).get(
                id=params.call_execution_id,
                test_execution__run_test__organization=context.organization,
            )
        except CallExecution.DoesNotExist:
            return self._candidate_calls_result(
                context,
                f"Call execution `{params.call_execution_id}` was not found.",
            )
        except (TypeError, ValueError, ValidationError):
            return self._candidate_calls_result(
                context,
                f"Call execution `{params.call_execution_id}` was not a valid UUID.",
            )

        # Check if the parent test execution is in a non-rerunnable status
        if call.test_execution:
            te_status = call.test_execution.status
            non_rerunnable = [
                TestExecution.ExecutionStatus.PENDING,
                TestExecution.ExecutionStatus.RUNNING,
                TestExecution.ExecutionStatus.CANCELLING,
            ]
            if te_status in non_rerunnable:
                return ToolResult.error(
                    f"Cannot rerun call while test execution is in '{te_status}' status. "
                    "Wait for it to complete or cancel it first.",
                    error_code="VALIDATION_ERROR",
                )

        # Check agent type: CHAT/TEXT agents can only do eval_only
        if params.rerun_type != "eval_only":
            agent_def = None
            if call.test_execution and call.test_execution.run_test:
                agent_def = call.test_execution.run_test.agent_definition
            if (
                agent_def
                and agent_def.agent_type == AgentDefinition.AgentTypeChoices.TEXT
            ):
                return ToolResult.error(
                    "Text/Chat agents only support 'eval_only' rerun type. "
                    "Use rerun_type='eval_only' instead.",
                    error_code="VALIDATION_ERROR",
                )

        scenario_name = call.scenario.name if call.scenario else "—"
        previous_status = call.status

        if params.rerun_type == "eval_only":
            # Snapshot must match the backend's eval-only rerun logic.
            CallExecutionSnapshot.objects.create(
                call_execution=call,
                rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
                eval_outputs=call.eval_outputs,
                provider_call_data=call.provider_call_data,
                overall_score=call.overall_score,
                tool_outputs=call.tool_outputs,
            )

            # Clear only eval data
            call.eval_outputs = {}
            call.call_metadata = call.call_metadata or {}
            call.call_metadata["eval_started"] = False
            call.call_metadata["eval_completed"] = False
            call.save()
            new_status = call.status  # Keep current status
        else:
            # Snapshot must match the backend's full call+eval rerun logic.
            CallExecutionSnapshot.objects.create(
                call_execution=call,
                rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
                service_provider_call_id=call.service_provider_call_id,
                status=call.status,
                started_at=call.started_at,
                completed_at=call.completed_at,
                ended_at=call.ended_at,
                duration_seconds=call.duration_seconds,
                recording_url=call.recording_url,
                cost_cents=call.cost_cents,
                stt_cost_cents=call.stt_cost_cents,
                llm_cost_cents=call.llm_cost_cents,
                tts_cost_cents=call.tts_cost_cents,
                vapi_cost_cents=call.vapi_cost_cents,
                call_summary=call.call_summary,
                ended_reason=call.ended_reason,
                overall_score=call.overall_score,
                response_time_ms=call.response_time_ms,
                assistant_id=call.assistant_id,
                customer_number=call.customer_number,
                call_type=call.call_type,
                analysis_data=call.analysis_data,
                evaluation_data=call.evaluation_data,
                message_count=call.message_count,
                transcript_available=call.transcript_available,
                recording_available=call.recording_available,
                eval_outputs=call.eval_outputs,
                tool_outputs=call.tool_outputs,
                provider_call_data=call.provider_call_data,
                avg_agent_latency_ms=call.avg_agent_latency_ms,
                user_interruption_count=call.user_interruption_count,
                user_interruption_rate=call.user_interruption_rate,
                user_wpm=call.user_wpm,
                bot_wpm=call.bot_wpm,
                talk_ratio=call.talk_ratio,
                ai_interruption_count=call.ai_interruption_count,
                ai_interruption_rate=call.ai_interruption_rate,
                avg_stop_time_after_interruption_ms=call.avg_stop_time_after_interruption_ms,
                conversation_metrics_data=call.conversation_metrics_data,
                transcripts=list(
                    call.transcripts.values(
                        "speaker_role",
                        "content",
                        "start_time_ms",
                        "end_time_ms",
                        "confidence_score",
                    )
                ),
            )

            # Reset the full call execution
            call.transcripts.all().delete()
            call.reset_to_default()
            call.save()
            new_status = call.status

        # Queue the rerun via Temporal, matching CallExecutionRerunView's behavior.
        # (CallExecutionRerunView resets/clears first, then starts RerunCoordinatorWorkflow.)
        test_execution = call.test_execution
        active_workflow_id = None
        if test_execution and test_execution.execution_metadata:
            active_workflow_id = test_execution.execution_metadata.get(
                "active_rerun_workflow_id"
            )

        workspace_id = test_execution.run_test.workspace_id if test_execution else None
        workspace_id_str = str(workspace_id) if workspace_id else ""

        rerun_result = rerun_call_executions(
            test_execution_id=str(test_execution.id),
            call_execution_ids=[str(call.id)],
            org_id=str(context.organization.id),
            workspace_id=workspace_id_str,
            eval_only=(params.rerun_type == "eval_only"),
            active_workflow_id=active_workflow_id,
        )

        if params.rerun_type == "eval_only":
            test_execution.status = TestExecution.ExecutionStatus.EVALUATING
        else:
            test_execution.status = TestExecution.ExecutionStatus.RUNNING

        test_execution.picked_up_by_executor = True
        if not test_execution.execution_metadata:
            test_execution.execution_metadata = {}
        if not rerun_result.get("merged"):
            test_execution.execution_metadata["active_rerun_workflow_id"] = (
                rerun_result.get("workflow_id")
            )
        test_execution.save()

        info = key_value_block(
            [
                ("Call ID", f"`{call.id}`"),
                ("Scenario", scenario_name),
                ("Rerun Type", params.rerun_type),
                ("Previous Status", format_status(previous_status)),
                ("New Status", format_status(new_status)),
                (
                    "Test Execution",
                    f"`{call.test_execution.id}`" if call.test_execution else "—",
                ),
            ]
        )

        content = section("Call Execution Rerun", info)
        if params.rerun_type == "eval_only":
            content += (
                "\n\n_Evaluation data has been cleared and will be re-evaluated._"
            )
        else:
            content += (
                "\n\n_Call has been reset and marked pending for re-execution. "
                "A snapshot of the previous state was saved._"
            )

        return ToolResult(
            content=content,
            data={
                "id": str(call.id),
                "previous_status": previous_status,
                "status": new_status,
                "rerun_type": params.rerun_type,
                "scenario": scenario_name,
            },
        )
