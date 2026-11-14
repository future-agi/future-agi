"""Continuous coverage begins at acceptance, not the first healthy worker poll."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tfc.temporal.eval_tasks.activities import _reconcile_sync, _set_task_status_sync
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("converted", [False, True])
def test_delayed_start_keeps_acceptance_floor_and_pending_arrivals(
    eval_task, monkeypatch, converted
):
    now = timezone.now()
    activation = now - timedelta(hours=2)
    created = activation - timedelta(days=30) if converted else activation
    EvalTask.objects.filter(pk=eval_task.pk).update(
        created_at=created,
        run_type=RunType.CONTINUOUS,
        start_time=activation if converted else None,
        continuous_cursor=None,
    )
    spans = []
    for label, arrived in (
        ("before-activation", activation - timedelta(minutes=1)),
        ("pending-during-outage", activation + timedelta(minutes=1)),
        ("pending-after-restoration", now - timedelta(minutes=1)),
    ):
        trace = Trace.objects.create(project=eval_task.project, name=label)
        span = ObservationSpan.objects.create(
            id=f"{label}-{eval_task.pk}",
            project=eval_task.project,
            trace=trace,
            name=label,
            observation_type="llm",
            parent_span_id="",
            start_time=arrived,
            end_time=arrived + timedelta(seconds=1),
            input={"input": "synthetic"},
            output={"output": "synthetic"},
        )
        ObservationSpan.all_objects.filter(pk=span.pk).update(
            created_at=arrived, updated_at=arrived
        )
        span.refresh_from_db()
        spans.append(span)
    seed_ch_spans(spans, version_from_created_at=True)
    monkeypatch.setattr(timezone, "now", lambda: now)
    try:
        result = _set_task_status_sync(
            str(eval_task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
        )
        assert result["changed"]
        eval_task.refresh_from_db()
        assert eval_task.start_time == activation
        _reconcile_sync(str(eval_task.pk))
        assert set(
            EvalLogger.objects.filter(eval_task_id=str(eval_task.pk)).values_list(
                "observation_span_id", flat=True
            )
        ) == {span.pk for span in spans[1:]}
        eval_task.refresh_from_db()
        assert eval_task.continuous_cursor >= activation
    finally:
        delete_ch_spans(spans)


@pytest.mark.django_db(transaction=True)
def test_continuous_restart_preserves_floor_and_partial_cursor(eval_task):
    activation = timezone.now() - timedelta(days=2)
    cursor = activation + timedelta(minutes=30)
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.CONTINUOUS,
        start_time=activation,
        continuous_cursor=cursor,
    )
    _set_task_status_sync(
        str(eval_task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )
    eval_task.refresh_from_db()
    assert eval_task.start_time == activation
    assert eval_task.continuous_cursor == cursor


@pytest.mark.django_db(transaction=True)
def test_historical_start_still_records_execution_time(eval_task, monkeypatch):
    now = timezone.now() + timedelta(hours=1)
    monkeypatch.setattr(timezone, "now", lambda: now)
    _set_task_status_sync(
        str(eval_task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )
    eval_task.refresh_from_db()
    assert eval_task.start_time == now


@pytest.mark.django_db(transaction=True)
def test_paused_start_does_not_change_floor(eval_task):
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PAUSED,
    )
    result = _set_task_status_sync(
        str(eval_task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )
    eval_task.refresh_from_db()
    assert not result["changed"]
    assert eval_task.status == EvalTaskStatus.PAUSED
    assert eval_task.start_time is None
