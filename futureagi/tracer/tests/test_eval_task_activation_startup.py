"""Converted route floors survive delayed startup and resumed reconciliation."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tfc.temporal.eval_tasks.activities import _reconcile_sync, _set_task_status_sync
from tracer.models.eval_task import EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans
from tracer.tests.test_eval_task_activation_paths import ROUTES, convert, historical

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


@pytest.mark.parametrize("route", ROUTES)
def test_converted_route_resume_excludes_pre_conversion_spans(
    auth_client, eval_task, custom_eval_config, monkeypatch, route
):
    activation = timezone.now().replace(microsecond=0)
    historical(eval_task, activation)
    spans = []
    for label, arrived in (
        ("historical-result", activation - timedelta(days=30)),
        ("pre-conversion", activation - timedelta(days=1)),
        ("pending-after-conversion", activation + timedelta(minutes=1)),
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
            input={"input": "synthetic input"},
            output={"output": "synthetic output"},
        )
        ObservationSpan.all_objects.filter(pk=span.pk).update(
            created_at=arrived, updated_at=arrived
        )
        span.refresh_from_db()
        spans.append(span)
    old = EvalLogger.objects.create(
        observation_span=spans[0],
        trace=spans[0].trace,
        custom_eval_config=custom_eval_config,
        eval_task_id=str(eval_task.pk),
        status=EvalEntryStatus.COMPLETED,
        config_hash=resolved_config_hash(custom_eval_config),
        error=False,
    )
    old_snapshot = EvalLogger.all_objects.filter(pk=old.pk).values().get()
    seed_ch_spans(spans, version_from_created_at=True)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    try:
        # Dispatch is delayed: the accepted mode change must own the floor.
        response = convert(auth_client, eval_task, route)
        assert response.status_code == 200, response.content
        clock["now"] += timedelta(hours=1)

        def start(task, **kwargs):
            _set_task_status_sync(
                str(task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
            )

        monkeypatch.setattr(
            "tracer.views.eval_task.start_eval_task_workflow_sync", start
        )
        if route == "edit_rerun":
            start(eval_task)
        else:
            response = auth_client.post(
                f"/tracer/eval-task/unpause_eval_task/?eval_task_id={eval_task.pk}",
                {},
                format="json",
            )
            assert response.status_code == 200, response.content
        _reconcile_sync(str(eval_task.pk))
        eval_task.refresh_from_db()
        assert eval_task.start_time == activation
        assert EvalLogger.all_objects.filter(pk=old.pk).values().get() == old_snapshot
        assert set(
            EvalLogger.all_objects.filter(eval_task_id=str(eval_task.pk)).values_list(
                "observation_span_id", flat=True
            )
        ) == {spans[0].pk, spans[2].pk}
    finally:
        delete_ch_spans(spans)
