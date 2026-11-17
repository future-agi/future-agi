"""An Instant eval run is not edited or rerun through the task editor.

Its task carries RUN_MARKER and reads the rows its Ask page sampled. The task
editor rebuilds plain filters without the marker, so a Save or Re-run there
would delete the run's answers and evaluate an ordinary window instead.
"""

import pytest
from rest_framework import status

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.services.instant_eval_rows import RUN_MARKER

_UPDATE = "/tracer/eval-task/update_eval_task/"
_REFUSAL = "This is an Instant eval run. Ask it again from its Ask page."


@pytest.fixture
def spy(monkeypatch):
    calls = {"soft_delete": [], "start": []}
    monkeypatch.setattr(
        "tracer.views.eval_task.soft_delete_live",
        lambda task: (calls["soft_delete"].append(str(task.id)), 0)[1],
    )
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda task, **kw: (calls["start"].append(str(task.id)), "wf")[1],
    )
    # pytest-django's outer transaction never commits, so run the dispatch
    # callback inline; the commit-order contract has its own tests.
    monkeypatch.setattr(
        "tracer.views.eval_task.transaction.on_commit", lambda callback: callback()
    )
    return calls


def _task(project, eval_config, filters):
    task = EvalTask.objects.create(
        project=project,
        name="Instant eval · Did the agent answer?",
        filters=filters,
        sampling_rate=100.0,
        spans_limit=500,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.COMPLETED,
        row_type=RowType.TRACES,
    )
    task.evals.add(eval_config)
    return task


@pytest.fixture
def instant_run_task(db, project, custom_eval_config):
    return _task(
        project,
        custom_eval_config,
        {
            "project_id": str(project.id),
            RUN_MARKER: True,
            "instant_config_ids": [str(custom_eval_config.id)],
        },
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("edit_type", ["fresh_run", "edit_rerun"])
def test_update_refuses_an_instant_eval_run(
    auth_client, instant_run_task, spy, edit_type
):
    saved_filters = dict(instant_run_task.filters)

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(instant_run_task.id),
            "name": "rerun from the task page",
            "edit_type": edit_type,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _REFUSAL in response.content.decode()
    assert spy == {"soft_delete": [], "start": []}
    instant_run_task.refresh_from_db()
    assert instant_run_task.filters == saved_filters
    assert instant_run_task.status == EvalTaskStatus.COMPLETED
    assert instant_run_task.name == "Instant eval · Did the agent answer?"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_update_with_task_page_filters_keeps_the_run_marker(
    auth_client, project, instant_run_task, spy
):
    # The task page sends filters rebuilt without the marker and a fresh
    # six-month window; none of it may replace the run's filters.
    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(instant_run_task.id),
            "filters": {"project_id": str(project.id)},
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _REFUSAL in response.content.decode()
    assert spy == {"soft_delete": [], "start": []}
    instant_run_task.refresh_from_db()
    assert instant_run_task.filters.get(RUN_MARKER) is True


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_update_still_reruns_a_task_without_the_run_marker(
    auth_client, project, custom_eval_config, spy
):
    task = _task(project, custom_eval_config, {"project_id": str(project.id)})

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(task.id),
            "name": "plain rerun",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert spy == {"soft_delete": [str(task.id)], "start": [str(task.id)]}
