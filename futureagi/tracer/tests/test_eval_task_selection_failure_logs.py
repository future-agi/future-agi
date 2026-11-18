"""A failed reconcile has no entry to log; the task itself owns the reason."""

from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import ActivityEnvironment

from tfc.temporal.eval_tasks import activities, workflows
from tfc.temporal.eval_tasks.types import ContinuousDrainState, EvalTaskWorkflowInput
from tracer.models.eval_task import EvalTaskStatus, RunType
from tracer.selectors.eval_tasks.row_resolver import (
    EvalTaskReadBudgetExceeded,
    EvalTaskSelectionRejected,
)

BUDGET_REASON = (
    "Evaluation task row selection exceeded its read budget. "
    "Narrow the time range and retry."
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("run_type", "error", "expected"),
    [
        (RunType.HISTORICAL, EvalTaskSelectionRejected(BUDGET_REASON), BUDGET_REASON),
        (RunType.CONTINUOUS, EvalTaskSelectionRejected(BUDGET_REASON), BUDGET_REASON),
        (RunType.HISTORICAL, EvalTaskReadBudgetExceeded(BUDGET_REASON), BUDGET_REASON),
        (
            RunType.HISTORICAL,
            EvalTaskSelectionRejected("private filter value"),
            "The evaluation task could not select rows. Check the task filters and retry.",
        ),
    ],
)
def test_failed_selection_reason_in_empty_logs(
    monkeypatch, eval_task, auth_client, run_type, error, expected
):
    task_id = str(eval_task.id)
    eval_task.run_type = run_type
    eval_task.save(update_fields=["run_type"])

    def reject(_task_id):
        raise error

    # Route the two real activities through the SDK's local environment. Only
    # the server's ActivityError envelope and workflow metadata are simulated.
    env = ActivityEnvironment()

    async def execute(name, arg, **options):
        if name == "set_eval_task_status_activity":
            return await env.run(activities.set_eval_task_status_activity, arg)
        assert name == "reconcile_eval_task_activity"
        try:
            return await env.run(activities.reconcile_eval_task_activity, arg)
        except ApplicationError as exc:
            raise ActivityError(
                "Activity failed",
                scheduled_event_id=1,
                started_event_id=2,
                identity="worker",
                activity_type=name,
                activity_id="reconcile",
                retry_state=None,
            ) from exc

    monkeypatch.setattr(activities, "_reconcile_sync", reject)
    monkeypatch.setattr(activities, "notify_eval_task_failed", AsyncMock())
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(workflows.workflow, "patched", lambda _: True)
    monkeypatch.setattr(workflows, "_apply_labels", AsyncMock())
    monkeypatch.setattr(workflows, "_set_status", lambda _: None)
    monkeypatch.setattr(
        workflows, "_task_state", AsyncMock(return_value={"active": True})
    )
    if run_type == RunType.HISTORICAL:
        workflow, arg = (
            workflows.HistoricalEvalTaskWorkflow(),
            EvalTaskWorkflowInput(task_id),
        )
    else:
        workflow, arg = (
            workflows.ContinuousEvalTaskWorkflow(),
            ContinuousDrainState(task_id),
        )
    with pytest.raises(ActivityError) as caught:
        async_to_sync(workflow.run)(arg)
    assert caught.value.cause.type == type(error).__name__

    def logs():
        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/", {"eval_task_id": task_id}
        )
        assert response.status_code == 200
        return response.json()["result"]

    failed = logs()
    assert failed["status"] == "failed" and failed["total_count"] == 0
    assert failed["failure_reason"] == expected

    # On a subsequent empty run the previous failure must not travel with
    # successful results, nor be resurrected by an unrelated failure.
    activities._set_task_status_sync(task_id, EvalTaskStatus.RUNNING)
    activities._finalize_task_sync(task_id)
    completed = logs()
    assert completed["status"] == "completed" and completed["total_count"] == 0
    assert completed["failure_reason"] is None
    activities._set_task_status_sync(task_id, EvalTaskStatus.FAILED)
    assert logs()["failure_reason"] is None
