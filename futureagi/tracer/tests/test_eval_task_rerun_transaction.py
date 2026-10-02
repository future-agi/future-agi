"""Transaction boundary contracts for eval-task edit/rerun dispatch."""

import pytest
from django.db import connection
from rest_framework import status

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType

_UPDATE = "/tracer/eval-task/update_eval_task/"


@pytest.fixture
def historical_task(db, project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="rerun transaction task",
        filters={},
        sampling_rate=100.0,
        spans_limit=100,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.FAILED,
        row_type=RowType.SPANS,
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_update_dispatches_only_after_pending_state_commits(
    auth_client, historical_task, monkeypatch
):
    observed = {}

    def _start(task, *, replace_existing=False):
        observed["in_atomic_block"] = connection.in_atomic_block
        observed["status"] = EvalTask.objects.get(id=task.id).status
        observed["replace_existing"] = replace_existing
        return f"eval-task-{task.id}"

    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", _start)

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "committed rerun",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert observed == {
        "in_atomic_block": False,
        "status": EvalTaskStatus.PENDING,
        "replace_existing": True,
    }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_post_commit_dispatch_failure_is_returned_and_pending_state_is_retryable(
    auth_client, historical_task, monkeypatch
):
    def _fail(_task, *, replace_existing=False):
        assert connection.in_atomic_block is False
        assert replace_existing is True
        raise RuntimeError("temporal dispatch unavailable")

    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", _fail)

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "committed despite dispatch failure",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Evaluation task could not be updated" in response.content.decode()

    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING
    assert historical_task.name == "committed despite dispatch failure"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_duplicate_pending_rerun_replaces_once_and_remains_recoverable(
    auth_client, historical_task, monkeypatch
):
    active_runs = []
    starts = []

    def _replace(task, *, replace_existing=False):
        assert connection.in_atomic_block is False
        assert replace_existing is True
        if active_runs:
            active_runs.pop()
        run_id = f"run-{len(starts) + 1}"
        active_runs.append(run_id)
        starts.append((str(task.id), run_id))
        return f"eval-task-{task.id}"

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", _replace
    )

    payload = {
        "eval_task_id": str(historical_task.id),
        "name": "same pending rerun",
        "edit_type": "fresh_run",
    }
    first = auth_client.patch(_UPDATE, payload, format="json")
    second = auth_client.patch(_UPDATE, payload, format="json")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert len(starts) == 2
    assert active_runs == ["run-2"]
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_running_task_rejects_rerun_without_replacing_workflow(
    auth_client, historical_task, monkeypatch
):
    historical_task.status = EvalTaskStatus.RUNNING
    historical_task.save(update_fields=["status"])
    starts = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "must not rerun",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert starts == []
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.RUNNING


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_unpause_replaces_a_still_closing_paused_workflow(
    auth_client, historical_task, monkeypatch
):
    historical_task.status = EvalTaskStatus.PAUSED
    historical_task.save(update_fields=["status"])
    starts = []

    def _replace(task, *, replace_existing=False):
        starts.append((str(task.id), replace_existing))
        return f"eval-task-{task.id}"

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", _replace
    )

    response = auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={historical_task.id}",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert starts == [(str(historical_task.id), True)]
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING
