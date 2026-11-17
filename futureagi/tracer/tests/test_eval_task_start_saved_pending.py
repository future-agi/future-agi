"""Retry a saved task's initial start without turning the retry into a re-run.

Temporal acceptance is mocked here; the client tests pin the server policies
that arbitrate starts racing after describe. No worker or CH reads are needed.
"""

from unittest.mock import Mock

import pytest
from django.utils import timezone
from temporalio.exceptions import WorkflowAlreadyStartedError

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.models.project import Project

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]


@pytest.fixture
def saved_task(
    auth_client,
    project,
    custom_eval_config,
    temporal,
    django_capture_on_commit_callbacks,
):
    # Use create's persisted state, including its pre-dispatch last_run stamp.
    describe, start = temporal
    start.side_effect = RuntimeError("synthetic initial acknowledgement lost")
    with django_capture_on_commit_callbacks(execute=True):
        created = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.pk),
                "evals": [str(custom_eval_config.pk)],
                "name": "Saved with unconfirmed start",
                "run_type": RunType.HISTORICAL,
                "spans_limit": 100,
                "sampling_rate": 100,
                "filters": {},
            },
            format="json",
        )
    assert created.status_code == 200, created.content
    task = EvalTask.objects.get(pk=created.json()["result"]["id"])
    assert task.status == EvalTaskStatus.PENDING
    assert task.last_run is not None
    assert task.start_time is None
    assert task.end_time is None
    assert task.continuous_cursor is None
    start.assert_called_once_with(task, start_once=True)
    start.reset_mock(side_effect=True)
    describe.reset_mock()
    return task


@pytest.fixture
def temporal(monkeypatch):
    describe = Mock(return_value="absent")
    start = Mock(return_value="synthetic-workflow")
    monkeypatch.setattr(
        "tracer.views.eval_task.describe_eval_task_workflow_sync",
        describe,
        raising=False,
    )
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", start)
    return describe, start


def resume(auth_client, task, *, start=None):
    return auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={task.pk}",
        {} if start is None else {"start": start},
        format="json",
    )


def details(auth_client, task):
    response = auth_client.get(
        f"/tracer/eval-task/get_eval_details/?eval_id={task.pk}"
    )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def snapshot(task):
    return EvalTask.all_objects.using("default").filter(pk=task.pk).values().get()


@pytest.mark.parametrize("accepted_before_failure", [False, True])
def test_saved_create_can_retry_an_unconfirmed_initial_start(
    auth_client, saved_task, temporal, accepted_before_failure
):
    describe, start = temporal
    before = snapshot(saved_task)
    assert saved_task.last_run is not None
    assert details(auth_client, saved_task)["can_start"] is True
    describe.return_value = "progressing" if accepted_before_failure else "absent"

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 200
    if accepted_before_failure:
        start.assert_not_called()
        assert response.json()["result"]["message"] == "Task start already accepted"
    else:
        start.assert_called_once_with(
            saved_task, start_once=True, workflow_confirmed_stopped=True
        )
    assert snapshot(saved_task) == before


def test_never_started_pending_task_starts_once(auth_client, saved_task, temporal):
    describe, start = temporal
    before = snapshot(saved_task)
    assert details(auth_client, saved_task)["can_start"] is True
    describe.side_effect = ["absent", "progressing"]

    first = resume(auth_client, saved_task, start=True)
    second = resume(auth_client, saved_task, start=True)

    assert first.status_code == second.status_code == 200
    assert first.json()["result"]["message"] == "Task start accepted"
    assert second.json()["result"]["message"] == "Task start already accepted"
    start.assert_called_once_with(
        saved_task, start_once=True, workflow_confirmed_stopped=True
    )
    assert snapshot(saved_task) == before
    assert not EvalTaskLogger.all_objects.filter(eval_task=saved_task).exists()
    assert not EvalLogger.all_objects.filter(eval_task_id=str(saved_task.pk)).exists()


def test_accepted_start_with_lost_ack_is_success_without_another_start(
    auth_client, saved_task, temporal
):
    describe, start = temporal
    describe.return_value = "progressing"
    before = snapshot(saved_task)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 200
    assert response.json()["result"]["message"] == "Task start already accepted"
    start.assert_not_called()
    assert snapshot(saved_task) == before


def test_closed_workflow_is_a_conflict_not_a_rerun(auth_client, saved_task, temporal):
    describe, start = temporal
    describe.return_value = "closed"

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 409
    assert response.json()["status"] is False
    assert response.json()["code"] == "eval_task_already_started"
    start.assert_not_called()


def test_start_that_closes_after_describe_is_a_conflict(
    auth_client, saved_task, temporal
):
    _, start = temporal
    start.side_effect = WorkflowAlreadyStartedError(
        f"eval-task-{saved_task.pk}", "HistoricalEvalTaskWorkflow"
    )
    before = snapshot(saved_task)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 409
    assert response.json()["code"] == "eval_task_already_started"
    start.assert_called_once_with(
        saved_task, start_once=True, workflow_confirmed_stopped=True
    )
    assert snapshot(saved_task) == before


@pytest.mark.parametrize("failure_at", ["describe", "start"])
def test_unconfirmed_retry_leaves_saved_task_untouched(
    auth_client, saved_task, temporal, failure_at
):
    describe, start = temporal
    failing = describe if failure_at == "describe" else start
    failing.side_effect = RuntimeError("synthetic unavailable transport")
    before = snapshot(saved_task)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 400
    assert response.json()["status"] is False
    assert "synthetic unavailable transport" not in response.content.decode()
    failing.assert_called_once()
    if failure_at == "describe":
        start.assert_not_called()
    assert snapshot(saved_task) == before
    assert details(auth_client, saved_task)["can_start"] is True


@pytest.mark.parametrize(
    "marker",
    [
        "start_time",
        "end_time",
        "continuous_cursor",
        "task_logger",
        "retired_task_logger",
        "eval_logger",
        "retired_eval_logger",
    ],
)
def test_pending_task_with_execution_evidence_is_not_startable(
    auth_client, saved_task, temporal, marker, trace, custom_eval_config
):
    describe, start = temporal
    if marker in {"task_logger", "retired_task_logger"}:
        EvalTaskLogger.objects.create(
            eval_task=saved_task,
            status=EvalTaskStatus.RUNNING,
            deleted=marker == "retired_task_logger",
        )
    elif marker in {"eval_logger", "retired_eval_logger"}:
        EvalLogger.objects.create(
            trace=trace,
            observation_span_id="synthetic-span",
            custom_eval_config=custom_eval_config,
            eval_task_id=str(saved_task.pk),
            status=EvalEntryStatus.PENDING,
            deleted=marker == "retired_eval_logger",
        )
    else:
        EvalTask.objects.filter(pk=saved_task.pk).update(**{marker: timezone.now()})
    before = snapshot(saved_task)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 409
    assert response.json()["status"] is False
    assert response.json()["code"] == "eval_task_already_started"
    assert details(auth_client, saved_task)["can_start"] is False
    describe.assert_not_called()
    start.assert_not_called()
    assert snapshot(saved_task) == before


@pytest.mark.parametrize(
    "previous",
    [
        EvalTaskStatus.PAUSED,
        EvalTaskStatus.FAILED,
        EvalTaskStatus.RUNNING,
        EvalTaskStatus.COMPLETED,
        EvalTaskStatus.DELETED,
    ],
)
def test_start_only_request_never_resumes_a_non_pending_task(
    auth_client, saved_task, temporal, previous
):
    describe, start = temporal
    # Keep the Pending button's intent even after the task's state changes.
    saved_task.status = previous
    saved_task.save(update_fields=["status"])
    before = snapshot(saved_task)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 409
    assert response.json()["status"] is False
    assert response.json()["code"] == "eval_task_already_started"
    describe.assert_not_called()
    start.assert_not_called()
    assert snapshot(saved_task) == before


@pytest.mark.parametrize("previous", [EvalTaskStatus.PAUSED, EvalTaskStatus.FAILED])
@pytest.mark.parametrize("start_only", [None, False])
def test_existing_resume_still_replaces_the_workflow(
    auth_client, saved_task, temporal, previous, start_only
):
    describe, start = temporal
    saved_task.status = previous
    saved_task.save(update_fields=["status"])

    response = resume(auth_client, saved_task, start=start_only)

    assert response.status_code == 200
    start.assert_called_once_with(saved_task, replace_existing=True)
    describe.assert_not_called()
    saved_task.refresh_from_db()
    assert saved_task.status == EvalTaskStatus.PENDING


@pytest.mark.parametrize(
    "previous",
    [EvalTaskStatus.RUNNING, EvalTaskStatus.COMPLETED, EvalTaskStatus.DELETED],
)
def test_other_statuses_are_still_refused(auth_client, saved_task, temporal, previous):
    describe, start = temporal
    saved_task.status = previous
    saved_task.save(update_fields=["status"])
    before = snapshot(saved_task)

    assert resume(auth_client, saved_task).status_code == 400
    describe.assert_not_called()
    start.assert_not_called()
    assert snapshot(saved_task) == before


@pytest.mark.parametrize("scope", ["workspace", "organization", "deleted_project"])
def test_saved_pending_start_keeps_resume_scope(
    auth_client, saved_task, temporal, scope, user, organization
):
    describe, start = temporal
    project = saved_task.project
    if scope == "workspace":
        other = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            created_by=user,
            is_default=False,
            is_active=True,
        )
        Project.objects.filter(pk=project.pk).update(workspace=other)
    elif scope == "organization":
        other = Organization.objects.create(name="Other organization")
        Project.objects.filter(pk=project.pk).update(organization=other)
    else:
        Project.objects.filter(pk=project.pk).update(deleted=True)

    response = resume(auth_client, saved_task, start=True)

    assert response.status_code == 400
    assert response.json()["result"] == "Eval task not found"
    describe.assert_not_called()
    start.assert_not_called()
