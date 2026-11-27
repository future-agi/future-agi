"""A failed reconcile has no entry to log; the task itself owns the reason."""

import dataclasses
from unittest.mock import AsyncMock, Mock

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

GENERIC_REASON = (
    "The evaluation task could not select rows. Check the task filters and retry."
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
            GENERIC_REASON,
        ),
        (
            RunType.HISTORICAL,
            ApplicationError("private query text", type="UnknownReconcileFailure"),
            GENERIC_REASON,
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
            # A workflow receives the decoded dict, never the output dataclass
            # (the historical start reads the acknowledgement as a mapping).
            ack = await env.run(activities.set_eval_task_status_activity, arg)
            return dataclasses.asdict(ack)
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
    with pytest.raises(ActivityError) as caught:
        if run_type == RunType.HISTORICAL:
            async_to_sync(workflows.HistoricalEvalTaskWorkflow().run)(
                EvalTaskWorkflowInput(task_id)
            )
        else:
            async_to_sync(workflows.ContinuousEvalTaskWorkflow().run)(
                ContinuousDrainState(task_id)
            )
    assert isinstance(caught.value.cause, ApplicationError)
    assert caught.value.cause.type == (
        error.type if isinstance(error, ApplicationError) else type(error).__name__
    )

    def logs():
        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/", {"eval_task_id": task_id}
        )
        assert response.status_code == 200
        return response.json()["result"]

    failed = logs()
    assert failed["status"] == "failed" and failed["total_count"] == 0
    assert failed["failure_reason"] == expected
    page = auth_client.get(
        "/tracer/eval-task/list_eval_tasks/", {"project_id": str(eval_task.project_id)}
    )
    assert page.status_code == 200
    row = next(row for row in page.json()["result"]["table"] if row["id"] == task_id)
    assert row["status"] == "failed" and row["failure_reason"] == expected

    # On a subsequent empty run the previous failure must not travel with
    # successful results, nor be resurrected by an unrelated failure.
    activities._set_task_status_sync(task_id, EvalTaskStatus.RUNNING)
    activities._finalize_task_sync(task_id)
    completed = logs()
    assert completed["status"] == "completed" and completed["total_count"] == 0
    assert completed["failure_reason"] is None
    activities._set_task_status_sync(task_id, EvalTaskStatus.FAILED)
    assert logs()["failure_reason"] is None


@pytest.mark.django_db(transaction=True)
def test_resume_hides_reason_until_run_starts(monkeypatch, auth_client, eval_task):
    eval_task.status = EvalTaskStatus.FAILED
    eval_task.failure_reason = BUDGET_REASON
    eval_task.save(update_fields=["status", "failure_reason"])

    start = Mock(return_value=str(eval_task.pk))
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", start)
    response = auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={eval_task.pk}",
        {},
        format="json",
    )
    assert response.status_code == 200
    start.assert_called_once_with(eval_task, replace_existing=True)
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.PENDING
    assert eval_task.failure_reason == BUDGET_REASON

    response = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/", {"eval_task_id": str(eval_task.pk)}
    )
    assert response.status_code == 200
    logs = response.json()["result"]
    assert logs["status"] == EvalTaskStatus.PENDING
    assert logs["failure_reason"] is None

    activities._set_task_status_sync(str(eval_task.pk), EvalTaskStatus.RUNNING)
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.RUNNING
    assert eval_task.failure_reason is None
