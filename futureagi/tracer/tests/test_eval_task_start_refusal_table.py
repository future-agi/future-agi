"""One start decision as observed through all five public/worker surfaces.

Every task names one unanswered display row. Ask therefore offers Resume for
`stalled`/`paused`, its existing new-question action for `failed`, and neither
for a create still pending/running. A Logs reason selects the standard page's
existing permission/status-gated Re-run hint; it is never an eval failure.
"""

from datetime import timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from tfc.temporal.eval_tasks import client
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStartRequest
from tracer.models.instant_eval import InstantEvalRun
from tracer.models.observation_span import EvalLogger
from tracer.tests.test_instant_eval_scale import (
    _answer,
    _control,
    _summary,
    fake_view,  # noqa: F401
    instant_eval_enabled,  # noqa: F401
    started,  # noqa: F401
    yes_no,  # noqa: F401
)
from tracer.utils.eval_tasks import _bridge_retired_dispatcher
from tracer.views.instant_eval_scale import _prepared_run

BASE = "/tracer/eval-task/"
FAILED = "The run couldn't start after an automatic retry. Your task is saved."
CLOSED = "The workflow ended before evaluation began. Your task is saved."


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state,admitted,artifact,event,status,plain,instant,bridge,logs,ask",
    [
        # No intent: the dev status/action rules, except Pending never dispatches.
        pytest.param(None, False, None, None, "pending", 400, 400, False, None, "failed", id="legacy-pending-even-with-closed-history"),
        pytest.param(None, False, None, None, "running", 400, 200, True, None, "stalled", id="r8-2-precutover-running-no-timestamp-or-logger"),
        pytest.param(None, False, None, "worker-start", "paused", 200, 200, False, None, "paused", id="r8-3-pause-before-first-entry-history-expired"),
        pytest.param(None, False, None, "worker-end", "failed", 200, 200, False, None, "stalled", id="r7-2-worker-failed-before-first-entry"),
        pytest.param(None, False, None, "worker-start", "running", 400, 200, True, None, "stalled", id="r7-3-interrupted-after-mark-running"),
        pytest.param(None, False, None, None, "paused", 200, 200, False, None, "paused", id="predeploy-native-pause-no-marker"),
        pytest.param(None, False, None, "pause-pending", "paused", 400, 400, False, None, "failed", id="new-native-pause-recorded-before-first-step"),
        pytest.param(None, False, "picker", "pause-pending", "paused", 200, 200, False, None, "paused", id="pause-after-picker-is-not-marked"),
        # Live sender/observer, including a worker admitted but not evaluating.
        pytest.param("pending", False, None, None, "pending", 400, 400, False, None, "pending", id="requested"),
        pytest.param("claimed", False, None, None, "pending", 400, 400, False, None, "pending", id="claimed-sending"),
        pytest.param("accepted", False, None, None, "pending", 400, 400, False, None, "pending", id="accepted-awaiting-worker"),
        pytest.param("accepted", True, None, None, "running", 400, 400, False, None, "running", id="admitted-awaiting-work"),
        # Worker-only progress, a successful admission, or a recorded new run
        # makes a terminal create failure inapplicable. A detail PATCH doesn't.
        pytest.param("start_failed", False, None, "convert", "running", 400, 400, False, FAILED, "failed", id="r8-1-conversion-floor-and-status-are-not-work"),
        pytest.param("start_failed", False, None, "rename", "failed", 400, 400, False, FAILED, "failed", id="r8-4-rename-with-later-last-run"),
        pytest.param("start_failed", False, None, None, "failed", 400, 400, False, FAILED, "failed", id="r8-5-named-display-row-does-not-offer-resume"),
        pytest.param("exhausted", False, None, None, "paused", 400, 400, False, CLOSED, "failed", id="paused-status-does-not-erase-closure"),
        pytest.param("exhausted", True, None, None, "failed", 200, 200, False, None, "stalled", id="admission-is-independent"),
        pytest.param("exhausted", False, "logger", None, "running", 400, 200, True, None, "stalled", id="retired-worker-logger"),
        pytest.param("start_failed", False, "picker", None, "paused", 200, 200, False, None, "paused", id="worker-picker-cursor"),
        pytest.param("start_failed", False, "entry", None, "failed", 200, 200, False, None, "stalled", id="worker-entry"),
        pytest.param("start_failed", False, "entry", "rerun", "failed", 200, 200, False, None, "stalled", id="r7-1-rerun-pause-resume-503"),
        pytest.param("exhausted", False, None, "rerun", "paused", 200, 200, False, None, "paused", id="rerun-record-survives-without-history-or-work"),
        pytest.param("void", False, None, None, "paused", 400, 400, False, None, "failed", id="revoked-before-worker-admission"),
    ],
)
def test_start_refusal_table(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch,
    state, admitted, artifact, event, status, plain, instant, bridge, logs, ask,
):
    cache.clear()
    rid = str(UUID(int=1))
    task = EvalTask.objects.create(
        project=observe_project, row_type="sessions", run_type="historical",
        status="failed" if state in ("exhausted", "start_failed") else "pending",
        filters={"session_id": [rid]},
    )
    task.evals.add(yes_no)
    tasks = EvalTask.objects.filter(pk=task.pk)
    if state:
        EvalTaskStartRequest.objects.create(
            task=task, state=state, attempts=2 if state == "start_failed" else 1,
            last_outcome={"start_failed": "not_started", "exhausted": "execution_closed_without_progress"}.get(state, "requested"),
            lease_until=timezone.now() + timedelta(minutes=2) if state in ("accepted", "claimed") else None,
            admitted_run_id=uuid4() if admitted else None,
        )
    send = Mock()
    describe = Mock(return_value=client.WF_ABSENT if event in ("worker-start", "worker-end") else client.WF_CLOSED)
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", send)
    _prepared_run(task, {}, rate=None)
    if event == "rerun":
        result = auth_client.patch(f"{BASE}update_eval_task/", {"eval_task_id": str(task.pk), "edit_type": "fresh_run"}, format="json")
        assert result.status_code == 200, result.content
        send.assert_called_once_with(task, replace_existing=True)
        send.reset_mock()
    if artifact == "picker":
        InstantEvalRun.objects.filter(eval_task=task).update(hash_cursor="42")
    elif artifact == "entry":
        EvalLogger.objects.bulk_create([_answer(task, yes_no, rid, pending=True)])
    elif artifact == "logger":
        EvalTaskLogger.objects.create(eval_task=task, deleted=True)
    detail = f"{BASE}{task.pk}/"
    if event == "convert":
        for patch in ({"run_type": "continuous"}, {"run_type": "historical", "status": "running", "spans_limit": 10}):
            result = auth_client.patch(detail, patch, format="json")
            assert result.status_code == 200, result.content
    elif event == "rename":
        result = auth_client.patch(detail, {"name": "rename only", "last_run": timezone.now().isoformat()}, format="json")
        assert result.status_code == 200, result.content
    elif event == "pause-pending":
        assert _control(auth_client, observe_project, task, "pause").status_code == 200
    else:
        tasks.update(status=status)
        if event in ("worker-start", "worker-end"):
            tasks.update(**{"start_time" if event == "worker-start" else "end_time": timezone.now()})
    tasks.update(updated_at=timezone.now() - timedelta(hours=1))
    task.refresh_from_db()
    assert task.status == status

    result = auth_client.get(f"{BASE}get_eval_task_logs/", {"eval_task_id": task.pk})
    assert result.status_code == 200, result.content
    groups = result.json()["result"]["error_groups"]
    assert [g["sample"] for g in groups] == ([logs] if logs else [])
    summary = _summary(auth_client, observe_project, task)
    assert summary["pending"] == 1  # even the failed-create display-row case
    assert summary["status"] == ask
    if logs:
        assert summary["failure"]["message"] == logs
    _bridge_retired_dispatcher(task)
    assert started == ([(str(task.pk), {})] if bridge else [])
    started.clear()
    # Exercise each endpoint against the same original rows, not the Pending
    # state or voided intent that a successful Resume commits.
    with transaction.atomic():
        response = auth_client.post(f"{BASE}unpause_eval_task/?eval_task_id={task.pk}")
        assert response.status_code == plain, response.content
        if plain == 200:
            send.assert_called_once_with(task, replace_existing=True)
        else:
            send.assert_not_called()
        transaction.set_rollback(True)
    response = _control(auth_client, observe_project, task, "resume")
    assert response.status_code == instant, response.content
    assert started == ([(str(task.pk), {"replace_existing": True})] if instant == 200 else [])
