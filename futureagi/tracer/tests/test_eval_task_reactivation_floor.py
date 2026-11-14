"""An explicit continuous rerun restarts coverage; creation ignores a client floor.

A continuous task's first, cursor-less pass starts at its ``start_time``
floor, and only the system sets that floor. An explicit rerun that resets the
cursor (Delete & rerun, or an edit that changes the evaluated rows or evals)
starts coverage again at the rerun, so rerunning a months-old monitor
evaluates only arrivals after the rerun. A client-supplied ``start_time`` or
``end_time`` on create is ignored: it can neither backfill history from before
the task existed nor suppress arrivals until a future time.

Timestamps are anchored a few hours in the real past so that no reconcile
ceiling read from the wall clock can exclude the post-(re)activation arrival.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from tfc.temporal.eval_tasks.activities import _reconcile_sync, _set_task_status_sync
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

UPDATE = "/tracer/eval-task/update_eval_task/"


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


def _long_lived_monitor(task, rerun):
    """A paused 30-day-old continuous task whose cursor is near ``rerun``."""
    activation = rerun - timedelta(days=30)
    EvalTask.objects.filter(pk=task.pk).update(
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PAUSED,
        created_at=activation,
        start_time=activation,
        continuous_cursor=rerun - timedelta(minutes=10),
        sampling_rate=100,
        spans_limit=None,
    )
    task.refresh_from_db()
    return activation


def _rerun_payload(edit, task, custom_eval_config):
    if edit == "fresh_run":
        # The pre-selected "Delete Past Data & Start Fresh" option, no edits.
        return {"eval_task_id": str(task.pk), "edit_type": "fresh_run"}
    second = CustomEvalConfig.objects.create(
        name="Second synthetic eval",
        project=task.project,
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.5},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    return {
        "eval_task_id": str(task.pk),
        "edit_type": "edit_rerun",
        "evals": [str(custom_eval_config.pk), str(second.pk)],
    }


@pytest.mark.parametrize("edit", ["fresh_run", "edit_rerun_adds_eval"])
def test_continuous_rerun_evaluates_only_arrivals_after_rerun(
    auth_client, eval_task, custom_eval_config, monkeypatch, edit
):
    rerun = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    activation = _long_lived_monitor(eval_task, rerun)
    project = eval_task.project
    old_20d = _span(project, "old-20d", rerun - timedelta(days=20))
    old_1d = _span(project, "old-1d", rerun - timedelta(days=1))
    new = _span(project, "new-after-rerun", rerun + timedelta(minutes=2))

    # The monitor already evaluated (and billed) both old spans with eval #1.
    for span in (old_20d, old_1d):
        EvalLogger.objects.create(
            observation_span=span,
            trace=span.trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.pk),
            status=EvalEntryStatus.COMPLETED,
            config_hash=resolved_config_hash(custom_eval_config),
            error=False,
        )
    before = set(
        EvalLogger.objects.filter(eval_task_id=str(eval_task.pk)).values_list(
            "id", flat=True
        )
    )
    payload = _rerun_payload(edit, eval_task, custom_eval_config)

    seed_ch_spans([old_20d, old_1d, new], version_from_created_at=True)
    clock = {"now": rerun}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    try:
        response = auth_client.patch(UPDATE, payload, format="json")
        assert response.status_code == 200, response.content
        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.PENDING
        assert eval_task.continuous_cursor is None
        floor_after_rerun = eval_task.start_time

        # The workflow picks the rerun up promptly.
        clock["now"] = running_at = rerun + timedelta(minutes=1)
        result = _set_task_status_sync(
            str(eval_task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
        )
        assert result["changed"]
        eval_task.refresh_from_db()
        floor_after_running = eval_task.start_time

        clock["now"] = rerun + timedelta(hours=1)
        _reconcile_sync(str(eval_task.pk))

        created = set(
            EvalLogger.objects.filter(eval_task_id=str(eval_task.pk))
            .exclude(id__in=before)
            .values_list("observation_span_id", flat=True)
        )
        assert created == {new.pk}, (
            f"rerun re-materialized history since activation {activation}: "
            f"{sorted(created)} (floor {floor_after_running})"
        )
        assert floor_after_rerun == rerun
        assert rerun <= floor_after_running <= running_at
    finally:
        delete_ch_spans([old_20d, old_1d, new])


def test_continuous_metadata_rerun_keeps_floor_and_cursor(auth_client, eval_task):
    # Control: an edit that resets nothing is not a re-activation.
    rerun = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    activation = _long_lived_monitor(eval_task, rerun)
    cursor = eval_task.continuous_cursor
    response = auth_client.patch(
        UPDATE,
        {
            "eval_task_id": str(eval_task.pk),
            "edit_type": "edit_rerun",
            "name": "Renamed synthetic monitor",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    eval_task.refresh_from_db()
    assert eval_task.name == "Renamed synthetic monitor"
    assert eval_task.start_time == activation
    assert eval_task.continuous_cursor == cursor


@pytest.mark.parametrize(
    "case,client_offset",
    [("past", -timedelta(days=30)), ("future", timedelta(days=1))],
    ids=["past", "future"],
)
def test_create_ignores_client_start_time_as_continuous_floor(
    auth_client, project, custom_eval_config, monkeypatch, case, client_offset
):
    created = (timezone.now() - timedelta(hours=3)).replace(microsecond=0)
    clock = {"now": created}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])

    pre_creation = _span(project, "pre-creation-10d", created - timedelta(days=10))
    post_running = _span(project, "post-running", created + timedelta(minutes=2))
    seed_ch_spans([pre_creation, post_running], version_from_created_at=True)
    try:
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": f"Synthetic continuous task ({case} start_time)",
                "run_type": "continuous",
                "sampling_rate": 100,
                "evals": [str(custom_eval_config.id)],
                "start_time": (created + client_offset).isoformat(),
                "end_time": (created + client_offset).isoformat(),
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        task_id = (body.get("result") or body)["id"]
        task = EvalTask.objects.get(pk=task_id)
        assert task.run_type == RunType.CONTINUOUS
        assert task.status == EvalTaskStatus.PENDING
        stored = (task.start_time, task.end_time)

        clock["now"] = running_at = created + timedelta(minutes=1)
        result = _set_task_status_sync(
            str(task_id), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
        )
        assert result["changed"]
        task.refresh_from_db()
        floor_after_running = task.start_time

        clock["now"] = created + timedelta(hours=1)
        _reconcile_sync(str(task_id))
        live = set(
            EvalLogger.objects.filter(eval_task_id=str(task_id)).values_list(
                "observation_span_id", flat=True
            )
        )
        assert live == {post_running.pk}, f"{case}: {sorted(live)}"
        assert stored == (None, None)
        assert task.created_at <= floor_after_running <= running_at
    finally:
        delete_ch_spans([pre_creation, post_running])
