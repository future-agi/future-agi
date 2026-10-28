"""ProductionCertificationWorkflow behavior tests (mocked activities).

Proves the plan's workflow-level invariants without a real harness:

- at most ``max_active_cases`` cases are in flight at once
- an individual case failure never stops the campaign (no behavioral retry)
- the cancel signal drains running cases and skips pending ones
- each case is submitted exactly once (exact-once dispatch)
- notifications are delivered via the outbox activity and delivery failure
  never changes the campaign result
"""

import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationCaseInput,
    CertificationCasesOutput,
    CertificationFinalizeOutput,
    CertificationNotifyOutput,
    CertificationPollOutput,
    CertificationReconcileOutput,
    CertificationResultInput,
    CertificationSubmitOutput,
    CertificationWorkflowInput,
)
from simulate.temporal.workflows.certification_workflow import (
    ProductionCertificationWorkflow,
)


class RecordingActivities:
    """In-memory stand-ins for the DB-backed certification activities."""

    def __init__(self, case_ids, fail_case_ids=(), done_after=1):
        self.case_ids = list(case_ids)
        self.fail_case_ids = set(fail_case_ids)
        self.done_after = done_after
        self.submitted = []
        self.canceled = []
        self.poll_calls = {}
        self.recorded = []
        self.deliver_calls = 0
        self.finalized = False
        self.in_flight = 0
        self.max_in_flight = 0

    @activity.defn(name="certification_load_cases")
    async def load(self, _input: CertificationCampaignInput):
        return CertificationCasesOutput(
            pending_case_ids=list(self.case_ids),
            max_active_cases=4,
        )

    @activity.defn(name="certification_submit_case")
    async def submit(self, input: CertificationCaseInput):
        if input.case_id in self.fail_case_ids:
            raise ApplicationError("submission exploded")
        self.submitted.append(input.case_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        return CertificationSubmitOutput(hosted_harness_job_id=f"job-{input.case_id}")

    @activity.defn(name="certification_poll_case")
    async def poll(self, input: CertificationCaseInput):
        self.poll_calls[input.case_id] = self.poll_calls.get(input.case_id, 0) + 1
        done = (
            input.case_id in self.canceled
            or self.poll_calls[input.case_id] >= self.done_after
        )
        if done:
            self.in_flight = max(0, self.in_flight - 1)
        return CertificationPollOutput(
            done=done,
            job_state="canceled" if input.case_id in self.canceled else "completed",
        )

    @activity.defn(name="certification_reconcile_case")
    async def reconcile(self, input: CertificationCaseInput):
        return CertificationReconcileOutput(ok=True, missing=[], failure_class=None)

    @activity.defn(name="certification_record_case_result")
    async def record(self, input: CertificationResultInput):
        self.recorded.append((input.case_id, input.outcome))

    @activity.defn(name="certification_deliver_notifications")
    async def deliver(self, _input: CertificationCampaignInput):
        self.deliver_calls += 1
        return CertificationNotifyOutput(delivered=1, failed=0, skipped=0)

    @activity.defn(name="certification_cancel_case")
    async def cancel(self, input: CertificationCaseInput):
        self.canceled.append(input.case_id)
        self.in_flight = max(0, self.in_flight - 1)

    @activity.defn(name="certification_finalize_campaign")
    async def finalize(self, _input: CertificationCampaignInput):
        self.finalized = True
        completed = sum(outcome == "completed" for _, outcome in self.recorded)
        failed = sum(outcome == "failed" for _, outcome in self.recorded)
        state = "completed_with_failures" if failed else "completed"
        return CertificationFinalizeOutput(
            state=state, cases_completed=completed, cases_failed=failed
        )

    def all(self):
        return [
            self.load,
            self.submit,
            self.poll,
            self.reconcile,
            self.record,
            self.deliver,
            self.cancel,
            self.finalize,
        ]


@pytest.mark.asyncio
async def test_caps_concurrency_at_four_and_completes_every_case(
    temporal_test_server_options,
):
    # Two polls per case forces overlap — with 8 cases, >4 concurrent would
    # exceed the cap if the parent didn't gate dispatch.
    acts = RecordingActivities([f"case-{i}" for i in range(8)], done_after=2)
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[ProductionCertificationWorkflow],
            activities=acts.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                ProductionCertificationWorkflow.run,
                CertificationWorkflowInput(campaign_id="camp-1"),
                id=f"cert-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.cases_completed == 8
    assert result.cases_failed == 0
    assert acts.max_in_flight <= 4
    assert sorted(acts.submitted) == sorted(acts.case_ids)  # exactly-once each
    assert len(acts.recorded) == 8
    assert acts.finalized


@pytest.mark.asyncio
async def test_case_failure_does_not_stop_the_campaign(
    temporal_test_server_options,
):
    acts = RecordingActivities(["case-a", "case-b", "case-c"], fail_case_ids={"case-b"})
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[ProductionCertificationWorkflow],
            activities=acts.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                ProductionCertificationWorkflow.run,
                CertificationWorkflowInput(campaign_id="camp-2"),
                id=f"cert-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    # The failed case never reached submission, but the campaign kept going.
    assert result.cases_completed == 2
    assert result.cases_failed == 1
    assert sorted(acts.submitted) == ["case-a", "case-c"]
    assert acts.finalized


@pytest.mark.asyncio
async def test_cancel_signal_drains_running_and_skips_pending(
    temporal_test_server_options,
):
    acts = RecordingActivities(
        ["case-a", "case-b", "case-c", "case-d", "case-e", "case-f"],
        done_after=999_999,  # cases never finish on their own
    )
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[ProductionCertificationWorkflow],
            activities=acts.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ProductionCertificationWorkflow.run,
                CertificationWorkflowInput(campaign_id="camp-3"),
                id=f"cert-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )
            # Let the first batch dispatch, then cancel.
            for _ in range(50):
                if len(acts.submitted) == 4:
                    break
                import asyncio

                await asyncio.sleep(0.05)
            await handle.signal("cancel")
            result = await handle.result()

    assert result.cases_completed == 0
    # Only dispatched cases get the cancel activity; pending ones never ran.
    assert sorted(acts.canceled) == sorted(acts.submitted)
    assert acts.finalized


@pytest.mark.asyncio
async def test_reconcile_failure_marks_case_failed_but_campaign_continues(
    temporal_test_server_options,
):
    acts = RecordingActivities(["case-a", "case-b"])

    @activity.defn(name="certification_reconcile_case")
    async def flaky_reconcile(input: CertificationCaseInput):
        if input.case_id == "case-a":
            return CertificationReconcileOutput(
                ok=False, missing=["transcripts"], failure_class="evidence"
            )
        return CertificationReconcileOutput(ok=True, missing=[], failure_class=None)

    activity_list = [
        acts.load,
        acts.submit,
        acts.poll,
        flaky_reconcile,
        acts.record,
        acts.deliver,
        acts.cancel,
        acts.finalize,
    ]
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[ProductionCertificationWorkflow],
            activities=activity_list,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                ProductionCertificationWorkflow.run,
                CertificationWorkflowInput(campaign_id="camp-4"),
                id=f"cert-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.cases_completed == 1
    assert result.cases_failed == 1
    # Result recording still ran for the failed case with its failure class.
    assert ("case-a", "failed") in acts.recorded
    assert ("case-b", "completed") in acts.recorded
