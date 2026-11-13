"""The mode-change transaction, not Temporal dispatch, owns activation time."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_conversion_persists_activation_before_dispatch_and_preserves_on_retry(
    auth_client, eval_task, monkeypatch
):
    activation = timezone.now()
    old_start = activation - timedelta(days=30)
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.COMPLETED,
        start_time=old_start,
    )
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    snapshots = []

    def unavailable(task, **kwargs):
        assert not connection.in_atomic_block
        saved = EvalTask.no_workspace_objects.get(pk=task.pk)
        snapshots.append((saved.run_type, saved.start_time, saved.continuous_cursor))
        raise RuntimeError("synthetic dispatch outage")

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", unavailable
    )
    payload = {
        "eval_task_id": str(eval_task.pk),
        "run_type": "continuous",
        "edit_type": "edit_rerun",
        "name": "synthetic conversion",
    }
    for elapsed in (timedelta(), timedelta(hours=1)):
        clock["now"] = activation + elapsed
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/", payload, format="json"
        )
        assert response.status_code == 400
        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.PENDING
        assert eval_task.start_time == activation
    assert snapshots == [(RunType.CONTINUOUS, activation, None)] * 2
