"""SimulationRunnerWorkflow — hosted execution of the released SDK (plan §9).

Instead of the native per-call ``CallExecutionWorkflow``, this workflow hands a
``StartRunnerJob`` to the simulation-runner worker, which spawns the SDK as a
child process. Results flow back through the existing ALK ingestion API, so the
workflow only builds the job, supervises the child, and finalizes status.

All steps run on the ``simulation_runner`` queue.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import CancelledError

from simulate.temporal.constants import (
    HOSTED_RUNNER_MAX_DURATION_SECONDS,
    QUEUE_RUNNER,
)
from simulate.temporal.retry_policies import DB_RETRY_POLICY, NO_RETRY_POLICY
from simulate.temporal.types.hosted_runner import (
    BuildRunnerJobInput,
    BuildRunnerJobOutput,
    FinalizeRunnerInput,
    RunHostedJobInput,
    RunHostedJobOutput,
    SimulationRunnerInput,
    SimulationRunnerOutput,
)


@workflow.defn
class SimulationRunnerWorkflow:
    @workflow.run
    async def run(self, input: SimulationRunnerInput) -> SimulationRunnerOutput:
        try:
            job = await workflow.execute_activity(
                "build_runner_job",
                BuildRunnerJobInput(
                    test_execution_id=input.test_execution_id,
                    run_test_id=input.run_test_id,
                    scenario_ids=input.scenario_ids,
                    mode=input.mode,
                    simulator_id=input.simulator_id,
                    call_execution_ids=input.call_execution_ids,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_RUNNER,
                result_type=BuildRunnerJobOutput,
            )

            outcome = await workflow.execute_activity(
                "run_hosted_sdk_job",
                RunHostedJobInput(
                    job_id=job.job_id,
                    run_id=job.run_id,
                    mode=job.mode,
                    job_json=job.job_json,
                ),
                start_to_close_timeout=timedelta(
                    seconds=HOSTED_RUNNER_MAX_DURATION_SECONDS
                ),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=NO_RETRY_POLICY,
                task_queue=QUEUE_RUNNER,
                result_type=RunHostedJobOutput,
            )

            # A completed simulation whose results never landed is NOT a pass —
            # gate on the submission too, and drive finalize off the derived
            # phase so a failed upload doesn't roll up to a zero-call COMPLETED.
            completed = (
                outcome.phase == "completed"
                and outcome.submission_status == "submitted"
            )
            finalize_phase = "completed" if completed else "failed"

            await workflow.execute_activity(
                "finalize_hosted_execution",
                FinalizeRunnerInput(
                    test_execution_id=input.test_execution_id,
                    job_phase=finalize_phase,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_RUNNER,
                result_type=str,
            )

            return SimulationRunnerOutput(
                status="COMPLETED" if completed else "FAILED",
                job_phase=outcome.phase,
                submission_status=outcome.submission_status,
                report_hash=outcome.report_hash,
                error=None if completed else outcome.detail,
            )
        except CancelledError:
            await workflow.execute_activity(
                "finalize_hosted_execution",
                FinalizeRunnerInput(
                    test_execution_id=input.test_execution_id,
                    job_phase="cancelled",
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_RUNNER,
                result_type=str,
            )
            return SimulationRunnerOutput(status="CANCELLED", error="cancelled")
        except Exception as exc:  # noqa: BLE001
            # Cancelling a running activity is surfaced by Temporal as an
            # ActivityError whose cause is CancelledError, not always as a
            # bare workflow CancelledError.
            if isinstance(getattr(exc, "cause", None), CancelledError):
                await workflow.execute_activity(
                    "finalize_hosted_execution",
                    FinalizeRunnerInput(
                        test_execution_id=input.test_execution_id,
                        job_phase="cancelled",
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_RUNNER,
                    result_type=str,
                )
                return SimulationRunnerOutput(status="CANCELLED", error="cancelled")
            workflow.logger.error(f"SimulationRunnerWorkflow failed: {exc}")
            try:
                await workflow.execute_activity(
                    "finalize_hosted_execution",
                    FinalizeRunnerInput(
                        test_execution_id=input.test_execution_id,
                        job_phase="failed",
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_RUNNER,
                    result_type=str,
                )
            except Exception:  # noqa: BLE001
                pass
            return SimulationRunnerOutput(status="FAILED", error=str(exc))
