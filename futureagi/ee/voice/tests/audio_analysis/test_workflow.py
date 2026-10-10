"""R11/R12, AC11: real Temporal history, bounded retries and finalization."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.common import RawValue, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ee.voice.temporal.activities.audio_analysis import (
    AnalyzeCallAudioInput,
    AudioAnalysisInput,
    FinalizeFailureInput,
)
from ee.voice.temporal.workflows.audio_analysis_workflow import AudioAnalysisWorkflow
from simulate.temporal.constants import QUEUE_AUDIO, QUEUE_S


class Activities:
    def __init__(self, error=None, fail_count=0):
        self.attempts = []
        self.error = error
        self.fail_count = fail_count
        self.finalizations = []
        self.commits = 0

    @activity.defn(name="analyze_call_audio")
    async def analyze(self, input: AnalyzeCallAudioInput):
        self.attempts.append(activity.info().started_time)
        if len(self.attempts) <= self.fail_count:
            raise ApplicationError("sanitized failure", type=self.error)
        self.commits += 1

    @activity.defn(name="finalize_audio_analysis_failure")
    async def finalize(self, input: FinalizeFailureInput):
        self.finalizations.append(input)


def analysis_input():
    return AudioAnalysisInput(
        "call",
        str(uuid.uuid4()),
        0,
        (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "org",
        "ws",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,fail_count,expected_attempts,reason",
    [
        ("ReadTimeoutError", 2, 3, None),
        ("ReadTimeoutError", 3, 3, "analysis_error"),
        ("AudioDeterministicError", 3, 1, "analysis_error"),
        ("AudioProvenanceError", 3, 1, "analysis_error"),
        ("ProviderAuthenticationError", 3, 1, "analysis_error"),
    ],
)
async def test_bounded_retries_and_finalizer(
    error, fail_count, expected_attempts, reason
):
    activities = Activities(error, fail_count)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue=QUEUE_AUDIO,
                workflows=[AudioAnalysisWorkflow],
                activities=[activities.analyze],
            ),
            Worker(env.client, task_queue=QUEUE_S, activities=[activities.finalize]),
        ):
            handle = await env.client.start_workflow(
                AudioAnalysisWorkflow.run,
                analysis_input(),
                id=str(uuid.uuid4()),
                task_queue=QUEUE_AUDIO,
                execution_timeout=timedelta(minutes=30),
            )
            await handle.result()
            history = await handle.fetch_history()
    assert len(activities.attempts) == expected_attempts
    if reason:
        assert activities.finalizations[0].reason == reason
        assert activities.commits == 0
    else:
        assert activities.commits == 1
        assert not activities.finalizations
        waits = [
            (b - a).total_seconds()
            for a, b in zip(activities.attempts, activities.attempts[1:], strict=False)
        ]
        assert waits[0] == pytest.approx(30, abs=2)
        assert waits[1] == pytest.approx(120, abs=2)
    payloads = history.to_json()
    assert "call-recordings/" not in payloads
    assert "https://" not in payloads
    assert "encoded audio" not in payloads


@pytest.mark.asyncio
async def test_duplicate_child_rejected_same_identity():
    activities = Activities()
    input = analysis_input()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_AUDIO,
            workflows=[AudioAnalysisWorkflow],
            activities=[activities.analyze],
        ):
            kwargs = {
                "id": f"audio-analysis-{input.analysis_id}",
                "task_queue": QUEUE_AUDIO,
                "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
                "execution_timeout": timedelta(minutes=30),
            }
            await env.client.execute_workflow(
                AudioAnalysisWorkflow.run, input, **kwargs
            )
            with pytest.raises(WorkflowAlreadyStartedError):
                await env.client.execute_workflow(
                    AudioAnalysisWorkflow.run, input, **kwargs
                )
    assert activities.commits == 1


@pytest.mark.asyncio
async def test_deadline_writes_failed_timeout():
    activities = Activities()
    input = analysis_input()
    input.deadline_at = "2000-01-01T00:00:00+00:00"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue=QUEUE_AUDIO,
                workflows=[AudioAnalysisWorkflow],
                activities=[activities.analyze],
            ),
            Worker(env.client, task_queue=QUEUE_S, activities=[activities.finalize]),
        ):
            await env.client.execute_workflow(
                AudioAnalysisWorkflow.run,
                input,
                id=str(uuid.uuid4()),
                task_queue=QUEUE_AUDIO,
                execution_timeout=timedelta(minutes=30),
            )
    assert activities.finalizations[0].reason == "timeout"
    assert not activities.attempts


class LostWorkerActivities(Activities):
    def __init__(self, after_persist):
        super().__init__()
        self.after_persist = after_persist

    @activity.defn(name="analyze_call_audio")
    async def analyze(self, input: AnalyzeCallAudioInput):
        self.attempts.append(activity.info().started_time)
        if self.commits:
            return
        if self.after_persist or len(self.attempts) > 1:
            self.commits += 1
        if len(self.attempts) == 1:
            raise ApplicationError("Worker lost", type="WorkerShuttingDownError")


@pytest.mark.asyncio
@pytest.mark.parametrize("after_persist", [False, True])
async def test_worker_kill_one_commit(after_persist):
    activities = LostWorkerActivities(after_persist)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_AUDIO,
            workflows=[AudioAnalysisWorkflow],
            activities=[activities.analyze],
        ):
            await env.client.execute_workflow(
                AudioAnalysisWorkflow.run,
                analysis_input(),
                id=str(uuid.uuid4()),
                task_queue=QUEUE_AUDIO,
                execution_timeout=timedelta(minutes=30),
            )
    assert len(activities.attempts) == 2
    assert activities.commits == 1


# Full call workflow with only its I/O boundaries stubbed. This also proves
# scheduling cannot race the late client-data activity in Phase 5.


class CallActivities:
    def __init__(self, client, schedule_fails):
        self.client = client
        self.schedule_fails = schedule_fails
        self.client_data_persisted = False
        self.cost_deductions = 0
        self.statuses = []
        self.analysis_id = str(uuid.uuid4())

    @activity.defn(dynamic=True)
    async def run(self, args: Sequence[RawValue]):
        import asyncio

        input = activity.payload_converter().from_payload(args[0].payload, dict)
        name = activity.info().activity_type
        if name == "check_call_balance":
            return {"sufficient": True}
        if name == "prepare_call":
            return {
                "is_outbound": True,
                "provider": "vapi",
                "connection_type": "web_bridge",
                "enable_tool_evaluation": True,
            }
        if name == "request_call_slot":
            await self.client.get_workflow_handle(activity.info().workflow_id).signal(
                "slot_granted"
            )
        elif name == "initiate_call":
            return {"success": True, "provider_call_id": "provider-call"}
        elif name == "monitor_call_until_complete":
            return {"success": True, "status": "completed", "duration_seconds": 10}
        elif name == "fetch_and_persist_call_result":
            return {
                "success": True,
                "message_count": 4,
                "has_agent_message": True,
                "has_customer_message": True,
            }
        elif name == "fetch_client_call_data":
            await asyncio.sleep(0.01)
            self.client_data_persisted = True
            return {"success": True}
        elif name == "deduct_call_cost":
            self.cost_deductions += 1
        elif name == "schedule_audio_analysis":
            assert self.client_data_persisted
            if self.schedule_fails:
                raise ApplicationError("Scheduling unavailable", non_retryable=True)
            return {
                "scheduled": True,
                "analysis_id": self.analysis_id,
                "generation": 0,
                "deadline_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            }
        elif name == "analyze_call_audio":
            raise ApplicationError("Invalid audio", type="AudioDeterministicError")
        elif name == "update_call_status":
            self.statuses.append(input["status"])


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule_fails", [False, True])
async def test_late_client_data_and_call_cost_unchanged_on_failure(schedule_fails):
    from contextlib import AsyncExitStack

    from temporalio.worker import UnsandboxedWorkflowRunner

    from ee.voice.temporal.workflows.call_execution_workflow import (
        CallExecutionWorkflow,
    )
    from simulate.temporal.constants import QUEUE_L, QUEUE_XL
    from simulate.temporal.types.call_execution import CallExecutionInput

    async with await WorkflowEnvironment.start_time_skipping() as env:
        activities = CallActivities(env.client, schedule_fails)
        async with AsyncExitStack() as stack:
            for queue in (QUEUE_S, QUEUE_L, QUEUE_XL, QUEUE_AUDIO):
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[CallExecutionWorkflow, AudioAnalysisWorkflow],
                        activities=[activities.run],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
            result = await env.client.execute_workflow(
                CallExecutionWorkflow.run,
                CallExecutionInput(
                    "call", "org", "ws", "test-workflow", "test-execution"
                ),
                id=str(uuid.uuid4()),
                task_queue=QUEUE_L,
                execution_timeout=timedelta(minutes=5),
            )
            if not schedule_fails:
                await env.client.get_workflow_handle(
                    f"audio-analysis-{activities.analysis_id}"
                ).result()
    assert result.status == "completed"
    assert activities.statuses[-1] == "completed"
    assert activities.cost_deductions == 1
    assert activities.client_data_persisted
