"""All accepted mode-change routes share one transactional activation policy.

Writers audited: detail PATCH/PUT and update_eval_task use EvalTaskSerializer;
the MCP update_eval_task tool delegates to that named PATCH action. Creation
uses this serializer (instant evaluation creates historical tasks directly).
No EvalTask admin is registered, and bulk deletion/recovery/reconciliation
write lifecycle/coverage fields only, never run_type. Pause and the legacy
failure handler must save only lifecycle fields, not a stale copy of the mode.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.serializers.eval_task import EvalTaskSerializer

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]
ROUTES = ("detail_patch", "detail_put", "edit_rerun")


def convert(client, task, route, **extra):
    payload = {"run_type": RunType.CONTINUOUS, **extra}
    if route == "edit_rerun":
        return client.patch(
            "/tracer/eval-task/update_eval_task/",
            {**payload, "eval_task_id": str(task.pk), "edit_type": "edit_rerun"},
            format="json",
        )
    if route == "detail_put":
        payload = {
            "project": str(task.project_id),
            "evals": [str(pk) for pk in task.evals.values_list("pk", flat=True)],
            "name": task.name,
            "sampling_rate": 100,
            **payload,
        }
    method = client.put if route == "detail_put" else client.patch
    return method(f"/tracer/eval-task/{task.pk}/", payload, format="json")


def historical(task, activation):
    EvalTask.objects.filter(pk=task.pk).update(
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PAUSED,
        start_time=activation - timedelta(days=30),
        continuous_cursor=activation - timedelta(days=29),
        sampling_rate=100,
    )
    task.refresh_from_db()


@pytest.mark.parametrize("route", ROUTES)
def test_every_conversion_persists_floor_before_dispatch(
    auth_client, eval_task, monkeypatch, route
):
    activation = timezone.now()
    historical(eval_task, activation)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    dispatched = []

    def unavailable(task, **kwargs):
        assert not connection.in_atomic_block
        saved = EvalTask.no_workspace_objects.get(pk=task.pk)
        dispatched.append((saved.run_type, saved.start_time, saved.continuous_cursor))
        raise RuntimeError("synthetic dispatch outage")

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", unavailable
    )
    for elapsed in (timedelta(), timedelta(hours=1)):
        clock["now"] = activation + elapsed
        response = convert(auth_client, eval_task, route)
        assert response.status_code == (400 if route == "edit_rerun" else 200), (
            response.content
        )
        eval_task.refresh_from_db()
        assert eval_task.run_type == RunType.CONTINUOUS
        assert eval_task.start_time == activation
        assert eval_task.continuous_cursor is None
    assert dispatched == (
        [(RunType.CONTINUOUS, activation, None)] * 2 if route == "edit_rerun" else []
    )


def test_serializer_uses_locked_primary_state_not_stale_instance(
    eval_task, monkeypatch
):
    activation = timezone.now()
    historical(eval_task, activation)
    stale = EvalTask.objects.get(pk=eval_task.pk)
    monkeypatch.setattr(timezone, "now", lambda: activation)
    first = EvalTaskSerializer(
        eval_task, data={"run_type": RunType.CONTINUOUS}, partial=True
    )
    assert first.is_valid(), first.errors
    first.save()
    cursor = activation + timedelta(minutes=5)
    EvalTask.objects.filter(pk=eval_task.pk).update(continuous_cursor=cursor)
    monkeypatch.setattr(timezone, "now", lambda: activation + timedelta(hours=1))
    second = EvalTaskSerializer(
        stale, data={"run_type": RunType.CONTINUOUS}, partial=True
    )
    assert second.is_valid(), second.errors
    # A replica read here may see the historical version and activate again.
    with patch(
        "django.db.router.db_for_read", side_effect=AssertionError("replica read")
    ):
        with CaptureQueriesContext(connection) as queries:
            saved = second.save()
    assert any("FOR UPDATE" in q["sql"] for q in queries)
    assert saved.start_time == activation
    assert saved.continuous_cursor == cursor
    eval_task.refresh_from_db()
    assert eval_task.start_time == activation
    assert eval_task.continuous_cursor == cursor


def test_serializer_conversion_rolls_back_mode_floor_and_cursor(eval_task, monkeypatch):
    activation = timezone.now()
    historical(eval_task, activation)
    before = EvalTask.objects.filter(pk=eval_task.pk).values().get()
    original_save = EvalTask.save

    def fail_after_write(task, *args, **kwargs):
        original_save(task, *args, **kwargs)
        raise RuntimeError("synthetic failure after task write")

    monkeypatch.setattr(EvalTask, "save", fail_after_write)
    serializer = EvalTaskSerializer(
        eval_task, data={"run_type": RunType.CONTINUOUS}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(RuntimeError, match="synthetic failure"):
        serializer.save()
    assert EvalTask.objects.filter(pk=eval_task.pk).values().get() == before


@pytest.mark.parametrize("route", ("detail_patch", "detail_put"))
def test_continuous_metadata_edit_cannot_replace_floor(
    auth_client, eval_task, monkeypatch, route
):
    activation = timezone.now()
    historical(eval_task, activation)
    monkeypatch.setattr(timezone, "now", lambda: activation)
    assert convert(auth_client, eval_task, route).status_code == 200
    cursor = activation + timedelta(minutes=10)
    EvalTask.objects.filter(pk=eval_task.pk).update(continuous_cursor=cursor)
    later = activation + timedelta(hours=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    if route == "detail_patch":
        response = auth_client.patch(
            f"/tracer/eval-task/{eval_task.pk}/",
            {"name": "renamed synthetic task", "start_time": later.isoformat()},
            format="json",
        )
    else:
        response = convert(
            auth_client,
            eval_task,
            route,
            name="renamed synthetic task",
            start_time=later.isoformat(),
        )
    assert response.status_code == 200, response.content
    eval_task.refresh_from_db()
    assert eval_task.start_time == activation
    assert eval_task.continuous_cursor == cursor


@pytest.mark.parametrize("route", ROUTES)
def test_each_new_transition_renews_floor(auth_client, eval_task, monkeypatch, route):
    activation = timezone.now()
    historical(eval_task, activation)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    assert convert(auth_client, eval_task, route).status_code == 200
    response = auth_client.patch(
        f"/tracer/eval-task/{eval_task.pk}/",
        {"run_type": RunType.HISTORICAL, "spans_limit": 10},
        format="json",
    )
    assert response.status_code == 200, response.content
    clock["now"] += timedelta(hours=2)
    response = convert(auth_client, eval_task, route)
    assert response.status_code == 200, response.content
    eval_task.refresh_from_db()
    assert eval_task.start_time == clock["now"]
    assert eval_task.continuous_cursor is None


@pytest.mark.parametrize("writer", ("pause", "legacy_failure"))
@pytest.mark.parametrize("new_mode", (RunType.CONTINUOUS, RunType.HISTORICAL))
def test_lifecycle_writers_do_not_restore_stale_mode_or_floor(
    auth_client, eval_task, monkeypatch, writer, new_mode
):
    activation = timezone.now()
    old_mode = (
        RunType.HISTORICAL if new_mode == RunType.CONTINUOUS else RunType.CONTINUOUS
    )
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=old_mode,
        status=EvalTaskStatus.RUNNING,
        start_time=activation - timedelta(days=30),
        continuous_cursor=activation - timedelta(days=29),
    )
    original_save = EvalTask.save
    writes = []

    def concurrent_mode_change(task, *args, **kwargs):
        # Another accepted edit commits between the lifecycle read and write.
        EvalTask.objects.filter(pk=task.pk).update(
            run_type=new_mode, start_time=activation, continuous_cursor=None
        )
        writes.append(task.pk)
        return original_save(task, *args, **kwargs)

    monkeypatch.setattr(EvalTask, "save", concurrent_mode_change)
    if writer == "pause":
        response = auth_client.post(
            f"/tracer/eval-task/pause_eval_task/?eval_task_id={eval_task.pk}",
            {},
            format="json",
        )
        assert response.status_code == 200, response.content
        expected_status = EvalTaskStatus.PAUSED
    else:
        from tracer.utils.eval_tasks import process_eval_task

        with patch(
            "tracer.utils.eval_tasks._bridge_retired_dispatcher",
            side_effect=RuntimeError("synthetic initialization failure"),
        ):
            process_eval_task._original_func(str(eval_task.pk))
        expected_status = EvalTaskStatus.FAILED
    assert writes == [eval_task.pk]
    eval_task.refresh_from_db()
    assert eval_task.status == expected_status
    assert eval_task.run_type == new_mode
    assert eval_task.start_time == activation
    assert eval_task.continuous_cursor is None


@pytest.mark.parametrize("route", ROUTES)
def test_rejected_conversion_leaves_coverage_unchanged(auth_client, eval_task, route):
    historical(eval_task, timezone.now())
    before = EvalTask.objects.filter(pk=eval_task.pk).values().get()
    response = convert(auth_client, eval_task, route, sampling_rate=0)
    assert response.status_code == 400, response.content
    assert EvalTask.objects.filter(pk=eval_task.pk).values().get() == before
