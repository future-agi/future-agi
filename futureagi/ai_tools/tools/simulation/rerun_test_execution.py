from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class RerunType(StrEnum):
    EVAL_ONLY = "eval_only"
    CALL_AND_EVAL = "call_and_eval"


class RerunTestExecutionInput(PydanticBaseModel):
    run_test_id: UUID | None = Field(
        default=None,
        description="The UUID of the RunTest containing the test executions to rerun.",
    )
    test_execution_ids: list[UUID] | None = Field(
        default=None,
        description="List of test execution IDs to rerun. If not provided, use select_all=True.",
    )
    select_all: bool = Field(
        default=False,
        description=(
            "If True, rerun all test executions in the run test. "
            "When combined with test_execution_ids, those IDs are excluded."
        ),
    )
    rerun_type: RerunType = Field(
        default=RerunType.CALL_AND_EVAL,
        description=(
            "Type of rerun: 'eval_only' reruns only evaluations on existing call data, "
            "'call_and_eval' reruns both the calls and evaluations. "
            "Text/Chat agents only support 'eval_only'."
        ),
    )


@register_tool
class RerunTestExecutionTool(BaseTool):
    name = "rerun_test_execution"
    description = (
        "Reruns test executions within a run test. Supports eval-only or full call+eval reruns. "
        "Use select_all=True to rerun all executions, or provide specific test_execution_ids. "
        "Text/Chat agents only support 'eval_only' rerun type. "
        "The original execution data is preserved as a snapshot."
    )
    category = "simulation"
    input_model = RerunTestExecutionInput

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
                    + "Provide `run_test_id` and either `test_execution_ids` or `select_all=true`.\n\n"
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

    def execute(
        self, params: RerunTestExecutionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution

        if params.run_test_id is None:
            return self._candidate_run_tests_result(context)

        # Fetch run_test with organization scoping (matches view's get_object_or_404)
        try:
            run_test = RunTest.objects.select_related("agent_definition").get(
                id=params.run_test_id,
                organization=context.organization,
            )
        except RunTest.DoesNotExist:
            return self._candidate_run_tests_result(
                context, f"Run test `{params.run_test_id}` was not found."
            )

        rerun_type = params.rerun_type.value
        select_all = params.select_all
        test_execution_ids = params.test_execution_ids

        if not select_all and not test_execution_ids:
            executions = TestExecution.objects.filter(run_test=run_test).order_by(
                "-created_at"
            )[:10]
            rows = [
                f"- `{execution.id}` — {execution.status}" for execution in executions
            ]
            return ToolResult(
                content=section(
                    "Test Executions Required",
                    (
                        "Either provide `test_execution_ids` or set `select_all=true`.\n\n"
                        + ("\n".join(rows) if rows else "No test executions found.")
                    ),
                ),
                data={
                    "run_test_id": str(run_test.id),
                    "requires_test_execution_ids": True,
                    "test_executions": [
                        {"id": str(execution.id), "status": execution.status}
                        for execution in executions
                    ],
                },
            )

        # Validate CHAT/TEXT agents can only use eval_only
        if rerun_type != "eval_only" and run_test.agent_definition:
            if (
                run_test.agent_definition.agent_type
                == AgentDefinition.AgentTypeChoices.TEXT
            ):
                return ToolResult.error(
                    "Text/Chat agents only support 'eval_only' rerun type.",
                    error_code="VALIDATION_ERROR",
                )

        # Get test executions, excluding those in non-terminal statuses
        non_rerunnable_statuses = [
            TestExecution.ExecutionStatus.PENDING,
            TestExecution.ExecutionStatus.RUNNING,
            TestExecution.ExecutionStatus.CANCELLING,
        ]
        if select_all:
            test_executions = TestExecution.objects.filter(run_test=run_test).exclude(
                status__in=non_rerunnable_statuses
            )
            if test_execution_ids:
                test_executions = test_executions.exclude(id__in=test_execution_ids)
        else:
            test_executions = TestExecution.objects.filter(
                id__in=test_execution_ids, run_test=run_test
            ).exclude(status__in=non_rerunnable_statuses)

        if not test_executions.exists():
            return ToolResult.error(
                "No test executions found that can be rerun. "
                "Executions in pending, running, or cancelling status cannot be rerun.",
                error_code="NOT_FOUND",
            )

        return self._rerun_bulk(
            run_test=run_test,
            test_executions=test_executions,
            rerun_type=rerun_type,
            context=context,
        )

    def _rerun_bulk(self, run_test, test_executions, rerun_type, context):
        """Rerun multiple test executions using the backend's bulk rerun logic."""
        import structlog
        from django.db import transaction
        from simulate.models.test_execution import (
            CallExecution,
            CallExecutionSnapshot,
            TestExecution,
        )

        logger = structlog.get_logger(__name__)

        results = []
        overall_success = 0
        overall_failure = 0

        for test_execution in test_executions:
            call_executions = CallExecution.objects.filter(
                test_execution=test_execution
            )

            if not call_executions.exists():
                results.append(
                    {
                        "test_execution_id": str(test_execution.id),
                        "skipped": True,
                        "reason": "No call executions found",
                    }
                )
                continue

            successful_reruns = []
            failed_reruns = []

            for call_execution in call_executions:
                try:
                    if rerun_type == "eval_only":
                        # Save eval snapshot
                        with transaction.atomic():
                            CallExecutionSnapshot.objects.create(
                                call_execution=call_execution,
                                rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
                                eval_outputs=call_execution.eval_outputs,
                                provider_call_data=call_execution.provider_call_data,
                                overall_score=call_execution.overall_score,
                                tool_outputs=call_execution.tool_outputs,
                            )
                            call_execution.eval_outputs = {}
                            call_execution.call_metadata = (
                                call_execution.call_metadata or {}
                            )
                            call_execution.call_metadata["eval_started"] = False
                            call_execution.call_metadata["eval_completed"] = False
                            call_execution.save(
                                update_fields=["eval_outputs", "call_metadata"]
                            )
                    else:
                        # Full rerun - capture full snapshot (including transcripts),
                        # clear transcripts, then reset fields (bulk view semantics).
                        transcript_data = list(
                            call_execution.transcripts.values(
                                "speaker_role",
                                "content",
                                "start_time_ms",
                                "end_time_ms",
                                "confidence_score",
                            )
                        )

                        with transaction.atomic():
                            # Create snapshot first so failures don't delete transcripts.
                            CallExecutionSnapshot.objects.create(
                                call_execution=call_execution,
                                rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
                                service_provider_call_id=call_execution.service_provider_call_id,
                                status=call_execution.status,
                                started_at=call_execution.started_at,
                                completed_at=call_execution.completed_at,
                                ended_at=call_execution.ended_at,
                                duration_seconds=call_execution.duration_seconds,
                                recording_url=call_execution.recording_url,
                                stereo_recording_url=call_execution.stereo_recording_url,
                                cost_cents=call_execution.cost_cents,
                                stt_cost_cents=call_execution.stt_cost_cents,
                                llm_cost_cents=call_execution.llm_cost_cents,
                                tts_cost_cents=call_execution.tts_cost_cents,
                                vapi_cost_cents=call_execution.vapi_cost_cents,
                                call_summary=call_execution.call_summary,
                                ended_reason=call_execution.ended_reason,
                                overall_score=call_execution.overall_score,
                                response_time_ms=call_execution.response_time_ms,
                                assistant_id=call_execution.assistant_id,
                                customer_number=call_execution.customer_number,
                                call_type=call_execution.call_type,
                                analysis_data=call_execution.analysis_data,
                                evaluation_data=call_execution.evaluation_data,
                                message_count=call_execution.message_count,
                                transcript_available=call_execution.transcript_available,
                                recording_available=call_execution.recording_available,
                                eval_outputs=call_execution.eval_outputs,
                                tool_outputs=call_execution.tool_outputs,
                                provider_call_data=call_execution.provider_call_data,
                                monitor_call_data=call_execution.monitor_call_data,
                                avg_agent_latency_ms=call_execution.avg_agent_latency_ms,
                                user_interruption_count=call_execution.user_interruption_count,
                                user_interruption_rate=call_execution.user_interruption_rate,
                                user_wpm=call_execution.user_wpm,
                                bot_wpm=call_execution.bot_wpm,
                                talk_ratio=call_execution.talk_ratio,
                                ai_interruption_count=call_execution.ai_interruption_count,
                                ai_interruption_rate=call_execution.ai_interruption_rate,
                                avg_stop_time_after_interruption_ms=call_execution.avg_stop_time_after_interruption_ms,
                                conversation_metrics_data=call_execution.conversation_metrics_data,
                                transcripts=transcript_data,
                            )

                            # Match bulk view: delete transcripts and reset fields without
                            # persisting call_metadata changes (bulk_update uses RESET_FIELDS).
                            call_execution.transcripts.all().delete()
                            call_execution.reset_to_default(save=False)
                            call_execution.save(
                                update_fields=CallExecution.RESET_FIELDS
                            )
                    successful_reruns.append(str(call_execution.id))
                except Exception as e:
                    logger.error(
                        "rerun_call_execution_failed",
                        call_execution_id=str(call_execution.id),
                        error=str(e),
                    )
                    failed_reruns.append(str(call_execution.id))

            # Start Temporal workflow
            if successful_reruns:
                from simulate.temporal.client import rerun_call_executions

                active_workflow_id = None
                if test_execution.execution_metadata:
                    active_workflow_id = test_execution.execution_metadata.get(
                        "active_rerun_workflow_id"
                    )

                workspace_id = test_execution.run_test.workspace_id
                workspace_id_str = str(workspace_id) if workspace_id else ""

                rerun_result = rerun_call_executions(
                    test_execution_id=str(test_execution.id),
                    call_execution_ids=successful_reruns,
                    org_id=str(context.organization.id),
                    workspace_id=workspace_id_str,
                    eval_only=(rerun_type == "eval_only"),
                    active_workflow_id=active_workflow_id,
                )

                if rerun_type == "eval_only":
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

            overall_success += len(successful_reruns)
            overall_failure += len(failed_reruns)
            results.append(
                {
                    "test_execution_id": str(test_execution.id),
                    "success_count": len(successful_reruns),
                    "failure_count": len(failed_reruns),
                }
            )

        info = key_value_block(
            [
                ("Test", run_test.name),
                ("Rerun Type", rerun_type),
                ("Executions Processed", str(len(results))),
                ("Calls Rerun", str(overall_success)),
                ("Calls Failed", str(overall_failure)),
            ]
        )

        content = section("Bulk Test Execution Rerun", info)
        content += "\n\n_Use `get_test_execution` to track progress._"

        return ToolResult(
            content=content,
            data={
                "run_test_id": str(run_test.id),
                "rerun_type": rerun_type,
                "total_executions": len(results),
                "overall_success_count": overall_success,
                "overall_failure_count": overall_failure,
                "results": results,
            },
        )
