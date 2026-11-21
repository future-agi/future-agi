"""Continuous recovery never restarts coverage before its latest acceptance.

Rows saved before conversions and cursor-resetting reruns persisted their own
floor can hold a NULL cursor beside an older ``start_time``: the historical
run's, or the activation a Delete & rerun replaced. No migration rewrites
them. If their first dispatch failed, the idle sweep is what starts them, and
it must floor them at the acceptance ``last_run`` records, not at the older
time and not at the recovery itself. Synthetic data only.
"""

import asyncio
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from temporalio.testing import ActivityEnvironment

from tfc.temporal.eval_tasks import client as workflow_client
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.services.eval_tasks.recovery import recover_stranded_tasks, recover_task

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

_SWEEP_POSITION_KEY = "eval-task-sweep:position:v1"


@pytest.fixture
def clock(monkeypatch):
    now = {"value": timezone.now().replace(microsecond=0)}
    monkeypatch.setattr(timezone, "now", lambda: now["value"])
    return now


@pytest.fixture
def restarts(monkeypatch):
    """Nothing owns the task; record what the restarted workflow would read."""
    started = []

    def record(task, **kwargs):
        row = EvalTask.no_workspace_objects.using("default").get(pk=task.pk)
        started.append((row.status, row.start_time, row.continuous_cursor))

    monkeypatch.setattr(
        workflow_client,
        "describe_eval_task_workflow_sync",
        lambda _: workflow_client.WF_CLOSED,
    )
    monkeypatch.setattr(workflow_client, "start_eval_task_workflow_sync", record)
    return started


def stored_task(project, eval_config, *, status=EvalTaskStatus.PENDING, **fields):
    """A continuous row as it already exists in the database at deploy time."""
    task = EvalTask.objects.create(
        project=project,
        name="synthetic continuous task",
        filters={},
        sampling_rate=100.0,
        spans_limit=1,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.COMPLETED,
        row_type=RowType.SPANS,
    )
    task.evals.add(eval_config)
    EvalTask.no_workspace_objects.filter(pk=task.pk).update(
        run_type=RunType.CONTINUOUS, status=status, **fields
    )
    task.refresh_from_db()
    return task


@pytest.mark.parametrize("status", [EvalTaskStatus.PENDING, EvalTaskStatus.FAILED])
def test_sweep_floors_pre_deploy_conversion_at_the_conversion(
    project, custom_eval_config, clock, restarts, settings, status
):
    # update_eval_task before this deploy: PENDING, cursor cleared, last_run
    # stamped, historical start_time kept; the post-commit start failed (or,
    # for FAILED, the workflow died before its RUNNING transition).
    settings.EVAL_TASK_SWEEP_RECOVER_FAILED = status == EvalTaskStatus.FAILED
    conversion = clock["value"] - timedelta(days=2)
    task = stored_task(
        project,
        custom_eval_config,
        status=status,
        start_time=conversion - timedelta(days=30),
        end_time=conversion - timedelta(days=29),
        continuous_cursor=None,
        last_run=conversion,
    )
    cache.delete(_SWEEP_POSITION_KEY)

    outcome = recover_stranded_tasks(limit=100, stale_running_seconds=7200)

    assert outcome["idle_continuous_candidates"] == 1, outcome
    assert outcome["restarted"] == 1 and outcome["errors"] == 0, outcome
    # Dispatched RUNNING, so the workflow's guarded PENDING -> RUNNING step
    # cannot restamp: this start_time is the cursor-less reconcile's floor.
    assert restarts == [(EvalTaskStatus.RUNNING, conversion, None)]
    task.refresh_from_db()
    assert (task.status, task.start_time, task.continuous_cursor) == (
        EvalTaskStatus.RUNNING,
        conversion,
        None,
    )


def test_recovery_floors_pre_deploy_rerun_at_the_rerun(
    project, custom_eval_config, clock, restarts
):
    # A Delete & rerun before this deploy cleared the cursor and stamped
    # last_run but kept the start_time of the activation it replaced.
    activated = clock["value"] - timedelta(days=60)
    rerun = clock["value"] - timedelta(days=2)
    task = stored_task(
        project,
        custom_eval_config,
        start_time=activated,
        continuous_cursor=None,
        last_run=rerun,
    )

    outcome = recover_task(task, stale_running_seconds=7200)

    assert outcome["restarted"], outcome
    assert restarts == [(EvalTaskStatus.RUNNING, rerun, None)]


@pytest.mark.parametrize(
    "case",
    ["started_after_acceptance", "cursor_kept", "future_last_run"],
)
def test_recovery_keeps_coverage_already_accepted(
    project, custom_eval_config, clock, restarts, case
):
    now = clock["value"]
    task = stored_task(project, custom_eval_config)
    created = task.created_at
    fields, expected = {
        # The workflow's RUNNING transition followed the acceptance.
        "started_after_acceptance": (
            {"start_time": created + timedelta(seconds=5), "last_run": created},
            created + timedelta(seconds=5),
        ),
        # With a cursor, productive passes keep stamping last_run; the floor
        # under the cursor is not an acceptance and must not move.
        "cursor_kept": (
            {
                "start_time": now - timedelta(days=60),
                "continuous_cursor": now - timedelta(minutes=10),
                "last_run": now - timedelta(minutes=5),
            },
            now - timedelta(days=60),
        ),
        # last_run is client-writable; never floor in the future.
        "future_last_run": (
            {
                "start_time": now - timedelta(days=30),
                "last_run": now + timedelta(days=1),
            },
            now,
        ),
    }[case]
    EvalTask.no_workspace_objects.filter(pk=task.pk).update(**fields)
    task.refresh_from_db()

    outcome = recover_task(task, stale_running_seconds=7200)

    assert outcome["restarted"], outcome
    assert restarts == [
        (EvalTaskStatus.RUNNING, expected, fields.get("continuous_cursor"))
    ]


@pytest.mark.parametrize(
    "edit",
    ["fresh_run", "selection_edit_rerun", "eval_set_edit_rerun"],
)
def test_cursor_resetting_rerun_persists_its_floor_before_dispatch(
    auth_client, project, custom_eval_config, clock, restarts, monkeypatch, edit
):
    activated = clock["value"] - timedelta(days=60)
    task = stored_task(
        project,
        custom_eval_config,
        status=EvalTaskStatus.PAUSED,
        start_time=activated,
        continuous_cursor=clock["value"] - timedelta(minutes=10),
        last_run=clock["value"] - timedelta(minutes=5),
    )
    payload = {"eval_task_id": str(task.pk), "edit_type": "edit_rerun"}
    if edit == "fresh_run":
        payload["edit_type"] = "fresh_run"
    elif edit == "selection_edit_rerun":
        payload["sampling_rate"] = 50
    else:
        added = CustomEvalConfig.objects.create(
            name="synthetic added eval",
            project=project,
            eval_template=custom_eval_config.eval_template,
            config={},
            mapping={"input": "input"},
            filters={},
        )
        payload["evals"] = [str(custom_eval_config.pk), str(added.pk)]
    rerun = clock["value"]
    persisted = []

    def unavailable(current, **kwargs):
        assert not connection.in_atomic_block
        row = EvalTask.no_workspace_objects.get(pk=current.pk)
        persisted.append((row.status, row.start_time, row.continuous_cursor))
        raise RuntimeError("synthetic dispatch outage")

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", unavailable
    )
    response = auth_client.patch(
        "/tracer/eval-task/update_eval_task/", payload, format="json"
    )

    assert response.status_code == 400, response.content
    # The rerun re-activates coverage in its own transaction, so whoever
    # dispatches it later floors at the rerun, not at the first activation.
    assert persisted == [(EvalTaskStatus.PENDING, rerun, None)]
    clock["value"] = rerun + timedelta(hours=1)
    assert recover_task(task, stale_running_seconds=7200)["restarted"]
    assert restarts == [(EvalTaskStatus.RUNNING, rerun, None)]


@pytest.mark.parametrize("with_cursor", [False, True])
def test_never_started_rows_are_skipped_not_given_a_recovery_activation(
    project, custom_eval_config, clock, restarts, with_cursor
):
    old = clock["value"] - timedelta(days=60)
    task = stored_task(
        project,
        custom_eval_config,
        created_at=old,
        start_time=None,
        last_run=old + timedelta(days=1),
        continuous_cursor=old if with_cursor else None,
    )
    before = EvalTask.all_objects.filter(pk=task.pk).values().get()

    outcome = recover_task(task, stale_running_seconds=7200)

    assert outcome["skipped"] and not outcome["restarted"], outcome
    assert restarts == []
    assert EvalTask.all_objects.filter(pk=task.pk).values().get() == before


@pytest.mark.parametrize(
    "acceptance", ["missing", "before_creation", "later_acceptance", "future"]
)
def test_legacy_unstamped_row_is_activated_by_the_actual_status_activity(
    project, custom_eval_config, clock, restarts, acceptance
):
    from tfc.temporal.eval_tasks.activities import set_eval_task_status_activity
    from tfc.temporal.eval_tasks.types import SetStatusInput

    now = clock["value"]
    created = now - timedelta(days=30)
    last_run, expected = {
        "missing": (None, created),
        "before_creation": (created - timedelta(seconds=1), created),
        "later_acceptance": (now - timedelta(days=2), now - timedelta(days=2)),
        "future": (now + timedelta(days=1), now),
    }[acceptance]
    task = stored_task(
        project,
        custom_eval_config,
        created_at=created,
        start_time=None,
        last_run=last_run,
        continuous_cursor=None,
    )
    assert recover_task(task, stale_running_seconds=7200)["skipped"]
    assert restarts == []

    # This is the actual workflow activity, including its thread bridge and
    # _set_task_status_sync's close_old_connections calls. The module's
    # transaction=True is required: a TestCase transaction is not visible on
    # that connection. No mocked status writer or inferred backfill marker.
    result = asyncio.run(
        ActivityEnvironment().run(
            set_eval_task_status_activity,
            SetStatusInput(
                task_id=str(task.pk),
                status=EvalTaskStatus.RUNNING,
                expected_status=EvalTaskStatus.PENDING,
            ),
        )
    )

    assert result.changed and result.status == EvalTaskStatus.RUNNING
    task.refresh_from_db()
    assert task.start_time == expected
    assert task.continuous_cursor is None
    assert recover_task(task, stale_running_seconds=7200)["restarted"]
    assert restarts == [(EvalTaskStatus.RUNNING, expected, None)]


@pytest.mark.parametrize("status", [EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED])
def test_actual_startup_activity_cannot_activate_a_stopped_unstamped_row(
    project, custom_eval_config, clock, restarts, status
):
    from tfc.temporal.eval_tasks.activities import set_eval_task_status_activity
    from tfc.temporal.eval_tasks.types import SetStatusInput

    task = stored_task(project, custom_eval_config, status=status, start_time=None)
    result = asyncio.run(
        ActivityEnvironment().run(
            set_eval_task_status_activity,
            SetStatusInput(
                task_id=str(task.pk),
                status=EvalTaskStatus.RUNNING,
                expected_status=EvalTaskStatus.PENDING,
            ),
        )
    )

    assert not result.changed and result.status == status
    task.refresh_from_db()
    assert task.start_time is None
    assert recover_task(task, stale_running_seconds=7200)["skipped"]
    assert restarts == []


def test_metadata_only_rerun_can_leave_a_legacy_row_unstamped(
    auth_client, project, custom_eval_config, clock, restarts, monkeypatch
):
    old = clock["value"] - timedelta(days=30)
    task = stored_task(
        project, custom_eval_config, created_at=old, start_time=None, last_run=old
    )
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda current, **kwargs: None,
    )
    response = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.pk),
            "edit_type": "edit_rerun",
            "name": "renamed unstamped synthetic task",
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    task.refresh_from_db()
    assert task.start_time is None
    assert recover_task(task, stale_running_seconds=7200)["skipped"]
    assert restarts == []


def test_rerun_that_keeps_the_cursor_keeps_the_floor(
    auth_client, project, custom_eval_config, clock, monkeypatch
):
    activated = clock["value"] - timedelta(days=60)
    cursor = clock["value"] - timedelta(minutes=10)
    task = stored_task(
        project,
        custom_eval_config,
        status=EvalTaskStatus.PAUSED,
        start_time=activated,
        continuous_cursor=cursor,
    )
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda current, **kwargs: None,
    )
    response = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.pk),
            "edit_type": "edit_rerun",
            "name": "renamed synthetic task",
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    task.refresh_from_db()
    assert (task.start_time, task.continuous_cursor) == (activated, cursor)
