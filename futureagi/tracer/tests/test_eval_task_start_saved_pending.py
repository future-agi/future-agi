"""Resume is never an alternative initial dispatch for a saved Pending task."""

from unittest.mock import AsyncMock, Mock

import pytest
from django.utils import timezone

from tfc.temporal.eval_tasks import client
from tracer.models.eval_task import EvalTaskLogger, EvalTaskStatus
from tracer.models.observation_span import EvalLogger

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]


@pytest.fixture
def starter(monkeypatch):
    start = Mock()
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", start)
    monkeypatch.setattr(client, "start_eval_task_workflow_sync", start)
    return start


def resume(auth_client, task, body):
    return auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={task.pk}",
        body,
        format="json",
    )


@pytest.mark.parametrize("body", [{}, {"start": True}, {"start": False}, {"other": 1}])
def test_resume_cannot_start_a_never_started_pending_task(
    auth_client, eval_task, starter, monkeypatch, body, no_new_usage
):
    # Match a saved create; Temporal has never seen this task either.
    eval_task.last_run = timezone.now()
    eval_task.save(update_fields=["last_run"])
    describe = AsyncMock(return_value=client.WF_ABSENT)
    monkeypatch.setattr(client, "describe_eval_task_workflow_async", describe)

    response = resume(auth_client, eval_task, body)

    assert response.status_code == 400, response.content
    if not body:
        assert response.json()["result"] == (
            "Cannot unpause eval task with status 'pending'. "
            "Only paused or failed tasks can be resumed."
        )
    starter.assert_not_called()
    assert not EvalTaskLogger.all_objects.filter(eval_task=eval_task).exists()
    assert not EvalLogger.all_objects.filter(eval_task_id=str(eval_task.pk)).exists()
    describe.assert_not_awaited()
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.PENDING


@pytest.mark.parametrize("previous", [EvalTaskStatus.PAUSED, EvalTaskStatus.FAILED])
@pytest.mark.django_db(transaction=True)
def test_existing_resume_still_replaces_the_workflow(
    auth_client, eval_task, starter, previous
):
    eval_task.status = previous
    eval_task.continuous_cursor = timezone.now()
    eval_task.save(update_fields=["status", "continuous_cursor"])

    response = resume(auth_client, eval_task, {})

    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"message": "Eval task unpaused successfully"}
    starter.assert_called_once_with(eval_task, replace_existing=True)
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.PENDING
