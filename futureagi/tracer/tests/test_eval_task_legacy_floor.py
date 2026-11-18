"""Rows saved before continuous floors were persisted never start earlier than intended.

The previous code restamped a continuous task's ``start_time`` every time the
task started, so a conversion or a Delete & rerun recorded only the edit in
``last_run`` and reset the cursor; the floor followed at the next start. When
the dispatch after such an edit failed, the row was left with
``run_type=continuous``, a NULL ``continuous_cursor``, ``last_run`` at the
edit and ``start_time`` still at the historical run's start, or at the
previous activation. These tests write those rows exactly so, with
``.update()``, then drive today's Resume, re-save and start paths over them.

Timestamps are anchored a few hours in the real past so that no reconcile
ceiling read from the wall clock can exclude the post-edit arrival.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from tfc.temporal.eval_tasks.activities import _reconcile_sync, _set_task_status_sync
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.configuration import continuous_coverage_floor
from tracer.services.eval_tasks.entries import soft_delete_live
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

UPDATE = "/tracer/eval-task/update_eval_task/"
UNPAUSE = "/tracer/eval-task/unpause_eval_task/"


def _span(project, label, arrived):
    trace = Trace.objects.create(project=project, name=label)
    span = ObservationSpan.objects.create(
        id=f"{label}-{trace.pk}",
        project=project,
        trace=trace,
        name=label,
        observation_type="llm",
        parent_span_id="",
        start_time=arrived,
        end_time=arrived + timedelta(seconds=1),
        input={"input": "synthetic input"},
        output={"output": "synthetic output"},
    )
    ObservationSpan.all_objects.filter(pk=span.pk).update(
        created_at=arrived, updated_at=arrived
    )
    span.refresh_from_db()
    return span


def _live_span_ids(task):
    return set(
        EvalLogger.objects.filter(eval_task_id=str(task.pk)).values_list(
            "observation_span_id", flat=True
        )
    )


def _start(task):
    """The workflow's guarded PENDING -> RUNNING transition."""
    result = _set_task_status_sync(
        str(task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )
    assert result["changed"]
    task.refresh_from_db()
    return task.start_time


def _dispatch_recorder(monkeypatch):
    started = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda task, **kwargs: started.append(task.pk),
    )
    return started


@pytest.mark.parametrize("route", ["resume_failed", "start_pending"])
def test_legacy_conversion_starts_at_the_conversion(
    auth_client, eval_task, monkeypatch, route
):
    converted = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    historical_start = converted - timedelta(days=2)
    project = eval_task.project
    pre = _span(project, "pre-conversion", historical_start + timedelta(hours=1))
    post = _span(project, "post-conversion", converted + timedelta(minutes=5))
    # The previous update_eval_task's conversion, committed before its dispatch
    # failed. resume_failed: its workflow then failed, and the user clicks
    # Resume. start_pending: a sweep restart, or a start queued before deploy.
    EvalTask.objects.filter(pk=eval_task.pk).update(
        created_at=historical_start - timedelta(minutes=1),
        run_type=RunType.CONTINUOUS,
        status=(
            EvalTaskStatus.FAILED
            if route == "resume_failed"
            else EvalTaskStatus.PENDING
        ),
        start_time=historical_start,
        end_time=historical_start + timedelta(minutes=2),
        last_run=converted,
        continuous_cursor=None,
        sampling_rate=100,
        spans_limit=None,
    )
    dispatched = _dispatch_recorder(monkeypatch)
    seed_ch_spans([pre, post], version_from_created_at=True)
    clock = {"now": converted + timedelta(minutes=30)}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    try:
        if route == "resume_failed":
            response = auth_client.post(
                f"{UNPAUSE}?eval_task_id={eval_task.pk}", {}, format="json"
            )
            assert response.status_code == 200, response.content
            assert dispatched == [eval_task.pk]
        clock["now"] = converted + timedelta(minutes=31)
        floor = _start(eval_task)
        clock["now"] = converted + timedelta(hours=1)
        _reconcile_sync(str(eval_task.pk))

        assert _live_span_ids(eval_task) == {post.pk}, (
            f"pre-conversion span backfilled from floor {floor}"
        )
        assert floor == converted
    finally:
        delete_ch_spans([pre, post])


def test_resave_after_legacy_failed_delete_and_rerun_does_not_rebill(
    auth_client, eval_task, custom_eval_config, monkeypatch
):
    rerun = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    activation = rerun - timedelta(days=30)
    project = eval_task.project
    old = _span(project, "billed-before-rerun", rerun - timedelta(days=1))
    after = _span(project, "after-rerun", rerun + timedelta(minutes=5))
    # A paused monitor that already evaluated (and billed) OLD.
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PAUSED,
        created_at=activation,
        start_time=activation,
        continuous_cursor=rerun - timedelta(minutes=10),
        last_run=rerun - timedelta(minutes=10),
        sampling_rate=100,
        spans_limit=None,
    )
    EvalLogger.objects.create(
        observation_span=old,
        trace=old.trace,
        custom_eval_config=custom_eval_config,
        eval_task_id=str(eval_task.pk),
        status=EvalEntryStatus.COMPLETED,
        config_hash=resolved_config_hash(custom_eval_config),
        error=False,
    )
    # The previous update_eval_task(fresh_run), committed before its dispatch
    # failed: entries soft-deleted, cursor reset, last_run at the rerun, and
    # start_time left for the next start to restamp.
    eval_task.refresh_from_db()
    soft_delete_live(eval_task)
    EvalTask.objects.filter(pk=eval_task.pk).update(
        status=EvalTaskStatus.PENDING, last_run=rerun, continuous_cursor=None
    )
    eval_task.refresh_from_db()
    dispatched = _dispatch_recorder(monkeypatch)
    seed_ch_spans([old, after], version_from_created_at=True)
    clock = {"now": rerun + timedelta(minutes=20)}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    try:
        # Details -> Save without changing anything -> "Edit & Re-run".
        response = auth_client.patch(
            UPDATE,
            {
                "eval_task_id": str(eval_task.pk),
                "edit_type": "edit_rerun",
                "name": eval_task.name,
                "run_type": RunType.CONTINUOUS,
                "sampling_rate": 100,
                "evals": [str(custom_eval_config.pk)],
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        assert dispatched == [eval_task.pk]
        eval_task.refresh_from_db()
        after_edit = (
            eval_task.start_time,
            eval_task.last_run,
            eval_task.continuous_cursor,
        )
        clock["now"] = rerun + timedelta(minutes=21)
        floor = _start(eval_task)
        clock["now"] = rerun + timedelta(hours=1)
        _reconcile_sync(str(eval_task.pk))

        assert _live_span_ids(eval_task) == {after.pk}, (
            f"span billed before the Delete & rerun evaluated again (floor {floor})"
        )
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(eval_task.pk), observation_span_id=old.pk
            ).count()
            == 1
        )
        assert after_edit == (rerun, rerun, None)
        assert floor == rerun
    finally:
        delete_ch_spans([old, after])


def test_retry_after_failed_conversion_dispatch_keeps_the_conversion_floor(
    auth_client, eval_task, monkeypatch
):
    # Control for the rule above: today's conversion persists its floor, and a
    # retried save must not turn into a newer acceptance at the next start.
    activation = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    project = eval_task.project
    before = _span(project, "before-conversion", activation - timedelta(hours=1))
    between = _span(project, "before-retry", activation + timedelta(minutes=10))
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.COMPLETED,
        created_at=activation - timedelta(days=30),
        start_time=activation - timedelta(days=30),
        end_time=activation - timedelta(days=30),
        last_run=activation - timedelta(days=30),
        continuous_cursor=None,
        sampling_rate=100,
    )

    def unavailable(task, **kwargs):
        raise RuntimeError("synthetic dispatch outage")

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", unavailable
    )
    seed_ch_spans([before, between], version_from_created_at=True)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    payload = {
        "eval_task_id": str(eval_task.pk),
        "edit_type": "edit_rerun",
        "run_type": RunType.CONTINUOUS,
    }
    try:
        for at in (activation, activation + timedelta(hours=1)):
            clock["now"] = at
            response = auth_client.patch(UPDATE, payload, format="json")
            # The dispatch failure surfaces; the edit itself is committed.
            assert response.status_code == 400, response.content
        eval_task.refresh_from_db()
        after_retry = (
            eval_task.start_time,
            eval_task.last_run,
            eval_task.continuous_cursor,
        )
        clock["now"] = activation + timedelta(hours=1, minutes=1)
        floor = _start(eval_task)
        clock["now"] = activation + timedelta(hours=2)
        _reconcile_sync(str(eval_task.pk))

        assert _live_span_ids(eval_task) == {between.pk}
        assert floor == activation
        assert after_retry == (activation, activation, None)
    finally:
        delete_ch_spans([before, between])


@pytest.mark.parametrize(
    "shape",
    [
        "never-started",
        "no-last-run",
        "accepted-at-floor",
        "legacy-accepted-after-floor",
        "legacy-never-started",
        "future-last-run",
        "with-cursor",
    ],
)
def test_start_transition_applies_the_coverage_floor_rule(
    eval_task, monkeypatch, shape
):
    now = (timezone.now() - timedelta(hours=1)).replace(microsecond=0)
    created = now - timedelta(days=1)
    older = created - timedelta(days=30)
    rows = {
        # Creation stamps last_run just before the row's created_at.
        "never-started": (None, created - timedelta(seconds=1), None, created),
        "no-last-run": (created, None, None, created),
        "accepted-at-floor": (created, created, None, created),
        "legacy-accepted-after-floor": (older, created, None, created),
        "legacy-never-started": (
            None,
            created + timedelta(hours=1),
            None,
            created + timedelta(hours=1),
        ),
        "future-last-run": (created, now + timedelta(days=1), None, now),
        # The reconciler starts at the cursor; the stored floor stands.
        "with-cursor": (older, created, older + timedelta(days=1), older),
    }
    start_time, last_run, cursor, expected = rows[shape]
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        created_at=created,
        start_time=start_time,
        last_run=last_run,
        continuous_cursor=cursor,
    )
    before = EvalTask.objects.get(pk=eval_task.pk)
    monkeypatch.setattr(timezone, "now", lambda: now)
    assert _start(eval_task) == expected
    # The edit path's Python rule and the start transition's SQL agree.
    assert continuous_coverage_floor(before, now=now) == expected
