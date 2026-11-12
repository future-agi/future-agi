"""Continuous restarts retain their history; mode conversion begins a new one."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from tfc.temporal.eval_tasks import activities
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

_UPDATE = "/tracer/eval-task/update_eval_task/"


@pytest.fixture(autouse=True)
def keep_test_transaction(monkeypatch):
    # The sync activity normally owns its connection. These tests call it inside
    # pytest-django's rollback-only transaction, which must remain open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)


@pytest.fixture
def continuous_task(project, custom_eval_config):
    now = timezone.now()
    task = EvalTask.objects.create(
        project=project,
        name="continuous boundary",
        filters={},
        sampling_rate=100.0,
        spans_limit=None,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PAUSED,
        start_time=now - timedelta(hours=2),
        continuous_cursor=now - timedelta(minutes=20),
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.fixture
def workflow_starts(monkeypatch):
    starts = []

    def record_start(task, **kwargs):
        saved = EvalTask.objects.get(id=task.id)
        starts.append(
            {
                "status": saved.status,
                "start_time": saved.start_time,
                "cursor": saved.continuous_cursor,
                "instance_start_time": task.start_time,
                "replace_existing": kwargs.get("replace_existing"),
            }
        )
        return "boundary-workflow"

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", record_start
    )
    return starts


def _start(task, expected_status=EvalTaskStatus.PENDING):
    return activities._set_task_status_sync(
        str(task.id), EvalTaskStatus.RUNNING, expected_status=expected_status
    )


@pytest.mark.parametrize("guarded", [True, False], ids=["guarded", "unguarded"])
def test_continuous_restart_and_retry_keep_original_start(
    continuous_task, monkeypatch, guarded
):
    task = continuous_task
    original_start = task.start_time
    original_cursor = task.continuous_cursor
    task.status = EvalTaskStatus.PENDING
    task.save(update_fields=["status"])
    restarted_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: restarted_at)

    result = _start(task, EvalTaskStatus.PENDING if guarded else None)
    task.refresh_from_db()

    assert result["changed"] is True
    assert task.status == EvalTaskStatus.RUNNING
    assert task.start_time == original_start
    assert task.continuous_cursor == original_cursor

    monkeypatch.setattr(timezone, "now", lambda: restarted_at + timedelta(minutes=5))
    retried = _start(task, EvalTaskStatus.PENDING if guarded else None)
    task.refresh_from_db()
    assert retried["changed"] is (not guarded)
    assert task.start_time == original_start


@pytest.mark.parametrize("run_type", [RunType.CONTINUOUS, RunType.HISTORICAL])
def test_first_start_initializes_missing_boundary(
    continuous_task, monkeypatch, run_type
):
    task = continuous_task
    task.run_type = run_type
    task.status = EvalTaskStatus.PENDING
    task.start_time = None
    task.save(update_fields=["run_type", "status", "start_time"])
    started_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: started_at)

    assert _start(task)["changed"] is True
    task.refresh_from_db()
    assert task.start_time == started_at


def test_historical_restart_still_stamps_each_run(continuous_task, monkeypatch):
    task = continuous_task
    task.run_type = RunType.HISTORICAL
    task.status = EvalTaskStatus.PENDING
    task.save(update_fields=["run_type", "status"])
    restarted_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: restarted_at)

    assert _start(task)["changed"] is True
    task.refresh_from_db()
    assert task.start_time == restarted_at


@pytest.mark.parametrize("task_status", [EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED])
def test_guard_mismatch_changes_neither_status_nor_boundary(
    continuous_task, task_status
):
    task = continuous_task
    original_start = task.start_time
    task.status = task_status
    task.save(update_fields=["status"])

    assert _start(task)["changed"] is False
    task.refresh_from_db()
    assert task.status == task_status
    assert task.start_time == original_start


@pytest.mark.parametrize("task_status", [EvalTaskStatus.PAUSED, EvalTaskStatus.FAILED])
def test_resume_keeps_continuous_start_and_cursor(
    auth_client, continuous_task, workflow_starts, task_status
):
    task = continuous_task
    original_start = task.start_time
    original_cursor = task.continuous_cursor
    task.status = task_status
    task.save(update_fields=["status"])

    response = auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={task.id}",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(workflow_starts) == 1
    assert _start(task)["changed"] is True
    task.refresh_from_db()
    assert task.start_time == original_start
    assert task.continuous_cursor == original_cursor


@pytest.mark.parametrize(
    "had_historical_start", [True, False], ids=["started", "unstarted"]
)
def test_historical_conversion_sets_boundary_before_workflow_start(
    auth_client,
    continuous_task,
    workflow_starts,
    django_capture_on_commit_callbacks,
    monkeypatch,
    had_historical_start,
):
    task = continuous_task
    task.run_type = RunType.HISTORICAL
    task.spans_limit = 100
    task.status = (
        EvalTaskStatus.COMPLETED if had_historical_start else EvalTaskStatus.PENDING
    )
    if not had_historical_start:
        task.start_time = None
    task.save(update_fields=["run_type", "spans_limit", "status", "start_time"])
    converted_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: converted_at)

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(task.id),
                "run_type": "continuous",
                "edit_type": "edit_rerun",
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    task.refresh_from_db()
    assert task.start_time == converted_at
    assert task.continuous_cursor is None
    assert workflow_starts == [
        {
            "status": EvalTaskStatus.PENDING,
            "start_time": converted_at,
            "cursor": None,
            "instance_start_time": converted_at,
            "replace_existing": True,
        }
    ]

    # A delayed worker must not move the conversion boundary again.
    monkeypatch.setattr(timezone, "now", lambda: converted_at + timedelta(minutes=10))
    assert _start(task)["changed"] is True
    task.refresh_from_db()
    assert task.start_time == converted_at


@pytest.mark.parametrize("edit_type", ["edit_rerun", "fresh_run"])
def test_rerun_materializes_added_eval_on_prior_matching_span(
    auth_client,
    continuous_task,
    custom_eval_config,
    workflow_starts,
    django_capture_on_commit_callbacks,
    ch_seed,
    edit_type,
):
    # ch_seed supplies CH cleanup; explicit versions model arrivals before edit.
    task = continuous_task
    original_start = task.start_time
    spans = []
    for name, arrived_at in (
        ("before-task", original_start - timedelta(minutes=10)),
        ("during-task", original_start + timedelta(minutes=10)),
    ):
        trace = Trace.objects.create(project=task.project, name=name)
        span = ObservationSpan.objects.create(
            id=f"boundary-{uuid.uuid4().hex}",
            project=task.project,
            trace=trace,
            name=name,
            observation_type="llm",
            start_time=arrived_at,
        )
        ObservationSpan.objects.filter(id=span.id).update(created_at=arrived_at)
        span.refresh_from_db()
        spans.append(span)
    seed_ch_spans(spans, version_from_created_at=True)
    before_task, during_task = spans

    task.status = EvalTaskStatus.RUNNING
    task.continuous_cursor = None
    task.save(update_fields=["status", "continuous_cursor"])
    assert reconcile(task).created == 1
    entries = EvalLogger.objects.filter(eval_task_id=str(task.id))
    original_entry = entries.get(observation_span_id=during_task.id)
    original_entry.status = EvalEntryStatus.COMPLETED
    original_entry.save(update_fields=["status"])
    task.refresh_from_db()
    assert task.continuous_cursor is not None
    task.status = EvalTaskStatus.PAUSED
    task.save(update_fields=["status"])
    added_eval = CustomEvalConfig.objects.create(
        name="Added evaluator",
        project=task.project,
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.5},
        mapping={"input": "input"},
        filters={},
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(task.id),
                "evals": [str(custom_eval_config.id), str(added_eval.id)],
                "edit_type": edit_type,
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    assert len(workflow_starts) == 1
    task.refresh_from_db()
    assert task.continuous_cursor is None
    assert _start(task)["changed"] is True
    task.refresh_from_db()
    reconcile(task)

    assert (
        entries.filter(
            observation_span_id=during_task.id,
            custom_eval_config=added_eval,
            status=EvalEntryStatus.PENDING,
        ).count()
        == 1
    )
    assert not entries.filter(observation_span_id=before_task.id).exists()
    assert task.start_time == original_start
    assert entries.count() == 2
    if edit_type == "edit_rerun":
        original_entry.refresh_from_db()
        assert original_entry.status == EvalEntryStatus.COMPLETED
    else:
        assert entries.filter(status=EvalEntryStatus.PENDING).count() == 2
    task.refresh_from_db()
    assert reconcile(task).created == 0
