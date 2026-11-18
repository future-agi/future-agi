"""One start decision as observed through the public and worker surfaces.

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
from tracer.selectors.eval_tasks.row_resolver import resolve_desired_rows
from tracer.services.eval_tasks import start_requests
from tracer.services.eval_tasks.recovery import recover_stranded_tasks
from tracer.services.instant_eval_answers import record_answer
from tracer.tests.test_instant_eval_scale import (
    _answer,
    _control,
    _start,
    _summary,
)
from tracer.tests.test_instant_eval_scale import fake_view as fake_view
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.tests.test_instant_eval_scale import started as started
from tracer.tests.test_instant_eval_scale import yes_no as yes_no
from tracer.utils.eval_tasks import _bridge_retired_dispatcher
from tracer.views.instant_eval_scale import _prepared_run, _rows_left

BASE = "/tracer/eval-task/"
FAILED = "The run couldn't start after an automatic retry. Your task is saved."
CLOSED = "The workflow ended before evaluation began. Your task is saved."


@pytest.mark.django_db
@pytest.mark.usefixtures("fake_view", "no_new_usage")
@pytest.mark.parametrize(
    "state,admitted,artifact,event,status,plain,instant,bridge,logs,ask",
    [
        # No intent: the dev status/action rules, except Pending never dispatches.
        pytest.param(
            None,
            False,
            None,
            None,
            "pending",
            400,
            400,
            False,
            None,
            "failed",
            id="legacy-pending-even-with-closed-history",
        ),
        pytest.param(
            None,
            False,
            None,
            None,
            "running",
            400,
            200,
            True,
            None,
            "stalled",
            id="r8-2-precutover-running-no-timestamp-or-logger",
        ),
        pytest.param(
            None,
            False,
            None,
            "worker-start",
            "paused",
            200,
            200,
            False,
            None,
            "paused",
            id="r8-3-pause-before-first-entry-history-expired",
        ),
        pytest.param(
            None,
            False,
            None,
            "worker-end",
            "failed",
            200,
            200,
            False,
            None,
            "stalled",
            id="r7-2-worker-failed-before-first-entry",
        ),
        pytest.param(
            None,
            False,
            None,
            "worker-start",
            "running",
            400,
            200,
            True,
            None,
            "stalled",
            id="r7-3-interrupted-after-mark-running",
        ),
        pytest.param(
            None,
            False,
            None,
            None,
            "paused",
            200,
            200,
            False,
            None,
            "paused",
            id="predeploy-native-pause-no-marker",
        ),
        pytest.param(
            None,
            False,
            None,
            "pause-pending",
            "paused",
            400,
            400,
            False,
            None,
            "failed",
            id="new-native-pause-recorded-before-first-step",
        ),
        pytest.param(
            None,
            False,
            "picker",
            "pause-pending",
            "paused",
            200,
            200,
            False,
            None,
            "paused",
            id="pause-after-picker-is-not-marked",
        ),
        # Live sender/observer, including a worker admitted but not evaluating.
        pytest.param(
            "pending",
            False,
            None,
            None,
            "pending",
            400,
            400,
            False,
            None,
            "pending",
            id="requested",
        ),
        pytest.param(
            "claimed",
            False,
            None,
            None,
            "pending",
            400,
            400,
            False,
            None,
            "pending",
            id="claimed-sending",
        ),
        pytest.param(
            "accepted",
            False,
            None,
            None,
            "pending",
            400,
            400,
            False,
            None,
            "pending",
            id="accepted-awaiting-worker",
        ),
        pytest.param(
            "accepted",
            True,
            None,
            None,
            "running",
            400,
            400,
            False,
            None,
            "running",
            id="admitted-awaiting-work",
        ),
        pytest.param(
            "accepted",
            True,
            None,
            "settled",
            "running",
            400,
            200,
            True,
            None,
            "stalled",
            id="admitted-sender-has-settled",
        ),
        pytest.param(
            "accepted",
            True,
            "picker",
            None,
            "running",
            400,
            200,
            False,
            None,
            "stalled",
            id="watched-with-work-bridge-still-yields",
        ),
        pytest.param(
            "accepted",
            True,
            None,
            "observe-end",
            "running",
            400,
            400,
            False,
            None,
            "running",
            id="admitted-end-time-does-not-settle-the-watch",
        ),
        # Worker-only progress, a successful admission, or a recorded new run
        # makes a terminal create failure inapplicable. A detail PATCH doesn't.
        pytest.param(
            "start_failed",
            False,
            None,
            "convert",
            "running",
            400,
            400,
            False,
            FAILED,
            "failed",
            id="r8-1-conversion-floor-and-status-are-not-work",
        ),
        pytest.param(
            "start_failed",
            False,
            None,
            "rename",
            "failed",
            400,
            400,
            False,
            FAILED,
            "failed",
            id="r8-4-rename-with-later-last-run",
        ),
        pytest.param(
            "start_failed",
            False,
            None,
            "named-trace",
            "failed",
            400,
            400,
            False,
            FAILED,
            "failed",
            id="r8-5-named-display-row-does-not-offer-resume",
        ),
        pytest.param(
            "exhausted",
            False,
            None,
            None,
            "paused",
            400,
            400,
            False,
            CLOSED,
            "failed",
            id="paused-status-does-not-erase-closure",
        ),
        pytest.param(
            "exhausted",
            True,
            None,
            None,
            "failed",
            200,
            200,
            False,
            None,
            "stalled",
            id="admission-is-independent",
        ),
        pytest.param(
            "exhausted",
            False,
            "logger",
            None,
            "running",
            400,
            200,
            True,
            None,
            "stalled",
            id="retired-worker-logger",
        ),
        pytest.param(
            "start_failed",
            False,
            "picker",
            None,
            "paused",
            200,
            200,
            False,
            None,
            "paused",
            id="worker-picker-cursor",
        ),
        pytest.param(
            "start_failed",
            False,
            "entry",
            None,
            "failed",
            200,
            200,
            False,
            None,
            "stalled",
            id="worker-entry",
        ),
        pytest.param(
            "start_failed",
            False,
            "entry",
            "rerun",
            "failed",
            200,
            200,
            False,
            None,
            "stalled",
            id="r7-1-rerun-pause-resume-503",
        ),
        pytest.param(
            "exhausted",
            False,
            None,
            "rerun-pause",
            "paused",
            200,
            200,
            False,
            None,
            "paused",
            id="rerun-pause-before-worker-work-stays-resumable",
        ),
        pytest.param(
            "exhausted",
            False,
            None,
            None,
            "pending",
            400,
            400,
            False,
            CLOSED,
            "failed",
            id="pending-status-cannot-reopen-exhaustion",
        ),
        pytest.param(
            "start_failed",
            False,
            None,
            "worker-end",
            "failed",
            400,
            400,
            False,
            FAILED,
            "failed",
            id="end-time-alone-is-not-worker-evidence",
        ),
        pytest.param(
            "start_failed",
            False,
            None,
            "rerun",
            "pending",
            400,
            400,
            False,
            None,
            "failed",
            id="explicit-rerun-pending-is-not-for-redelivery",
        ),
        pytest.param(
            "void",
            False,
            None,
            None,
            "paused",
            400,
            400,
            False,
            None,
            "failed",
            id="revoked-before-worker-admission",
        ),
    ],
)
def test_start_refusal_table(
    auth_client,
    observe_project,
    yes_no,  # noqa: F811 (fixture)
    started,  # noqa: F811 (fixture)
    monkeypatch,
    state,
    admitted,
    artifact,
    event,
    status,
    plain,
    instant,
    bridge,
    logs,
    ask,
):
    cache.clear()
    rid = str(UUID(int=1))
    unit = "traces" if event == "named-trace" else "sessions"
    if event == "named-trace":
        yes_no.config["instant_eval"]["unit"] = unit
        yes_no.save(update_fields=["config"])
    task = EvalTask.objects.create(
        project=observe_project,
        row_type=unit,
        run_type="historical",
        status="failed" if state in ("exhausted", "start_failed") else "pending",
        filters={"trace_id" if unit == "traces" else "session_id": [rid]},
    )
    task.evals.add(yes_no)
    tasks = EvalTask.objects.filter(pk=task.pk)
    if event == "named-trace":
        # A previous poll materialized this named row and cached a stalled
        # summary. The same version must stop offering Resume on exhaustion.
        assert _summary(auth_client, observe_project, task)["status"] == "stalled"
    if state and event not in ("observe-end", "settled"):
        watched = state in ("accepted", "claimed")
        outcomes = {
            "claimed": "send_reserved",
            "accepted": "worker_admitted" if admitted else "awaiting_worker",
            "start_failed": "not_started",
            "exhausted": "execution_closed_without_progress",
            "void": "superseded",
        }
        EvalTaskStartRequest.objects.create(
            task=task,
            state=state,
            attempts=2 if state == "start_failed" else 1,
            last_outcome=outcomes.get(state, "requested"),
            lease_until=timezone.now() + timedelta(minutes=2) if watched else None,
            admitted_run_id=uuid4() if admitted else None,
        )
    send = Mock()
    # An expired history on the paused/failed legacy rows, and no history
    # at all for the pre-cutover Running row that never wrote start_time.
    absent = state is None and (
        event == "worker-end"
        or status == "paused"
        or (status == "running" and event is None)
    )
    describe = Mock(return_value=client.WF_ABSENT if absent else client.WF_CLOSED)
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", send)
    _prepared_run(task, {}, rate=None)
    if event in ("observe-end", "settled"):
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        request = EvalTaskStartRequest.objects.create(task=task)
        start_requests.dispatch_start_request(task.pk, initial_start=send)
        assert start_requests.admit_start(task.pk, str(uuid4()))
        if event == "observe-end":
            # Lifecycle transitions leave an end timestamp behind on Running.
            # Only the worker/observer may settle this admitted run's lease.
            monkeypatch.setattr(
                "tfc.temporal.eval_tasks.activities.close_old_connections", lambda: None
            )
            assert _set_task_status_sync(str(task.pk), "failed", "running")["changed"]
            assert _set_task_status_sync(str(task.pk), "running", "failed")["changed"]
            assert tasks.get().end_time is not None
        EvalTaskStartRequest.objects.filter(pk=task.pk).update(
            lease_until=timezone.now() - timedelta(days=1)
        )
        describe.return_value = (
            client.WF_PROGRESSING if event == "observe-end" else client.WF_COMPLETED
        )
        start_requests.dispatch_start_request(task.pk)
        describe.assert_called_once_with(
            task.pk, timeout=5, distinguish_completion=True
        )
        request.refresh_from_db()
        assert request.last_outcome == (
            "worker_admitted" if event == "observe-end" else "execution_completed"
        )
        assert (request.lease_until is not None) == (event == "observe-end")
        send.assert_called_once_with(task, start_once=True)
        send.reset_mock()
        describe.return_value = (
            client.WF_CLOSED
        )  # independent probes by the other surfaces
    reran = event in ("rerun", "rerun-pause")
    if reran:
        result = auth_client.patch(
            f"{BASE}update_eval_task/",
            {"eval_task_id": str(task.pk), "edit_type": "fresh_run"},
            format="json",
        )
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
        for patch in (
            {"run_type": "continuous"},
            {"run_type": "historical", "status": "running", "spans_limit": 10},
        ):
            result = auth_client.patch(detail, patch, format="json")
            assert result.status_code == 200, result.content
    elif event == "rename":
        result = auth_client.patch(
            detail,
            {"name": "rename only", "last_run": timezone.now().isoformat()},
            format="json",
        )
        assert result.status_code == 200, result.content
    elif event == "worker-start" and status == "paused":
        tasks.update(status="running", start_time=timezone.now())
        monkeypatch.setattr(
            "tracer.views.eval_task.signal_pause_eval_task_workflow", Mock()
        )
        result = auth_client.post(f"{BASE}pause_eval_task/?eval_task_id={task.pk}")
        assert result.status_code == 200, result.content
    elif event in ("pause-pending", "rerun-pause"):
        assert _control(auth_client, observe_project, task, "pause").status_code == 200
    else:
        tasks.update(status=status)
        if event in ("worker-start", "worker-end"):
            tasks.update(
                **{
                    "start_time"
                    if event == "worker-start"
                    else "end_time": timezone.now()
                }
            )
    tasks.update(updated_at=timezone.now() - timedelta(hours=1))
    task.refresh_from_db()
    assert task.status == status
    if event in ("pause-pending", "rerun-pause"):
        assert (task.instant_paused_before_start_at is not None) == (
            artifact is None and not reran
        )
    if reran or event in ("rename", "convert"):
        request = EvalTaskStartRequest.objects.get(task=task)
        assert (request.superseded_at is not None) == reran
        assert request.state == state

    result = auth_client.get(f"{BASE}get_eval_task_logs/", {"eval_task_id": task.pk})
    assert result.status_code == 200, result.content
    groups = result.json()["result"]["error_groups"]
    assert [g["sample"] for g in groups] == ([logs] if logs else [])
    assert [g["start_reason_code"] for g in groups] == ([state] if logs else [])
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
    expected = [(str(task.pk), {"replace_existing": True})] if instant == 200 else []
    assert started == expected


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["pending", "running", "failed", "paused"])
def test_no_intent_guard_only_looks_up_the_intent(
    eval_task, django_assert_num_queries, status
):
    from tracer.services.eval_tasks.start_requests import start_refusal

    eval_task.status = status
    with django_assert_num_queries(1):
        refusal = start_refusal(eval_task)
    assert (refusal.code if refusal else None) == (
        "pending" if status == "pending" else None
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("started")
def test_only_an_explicit_submission_clears_the_native_pause_marker(
    auth_client,
    observe_project,
    yes_no,
    monkeypatch,  # noqa: F811 (fixture)
):
    task = EvalTask.objects.create(
        project=observe_project, run_type="historical", status="pending"
    )
    task.evals.add(yes_no)
    assert _control(auth_client, observe_project, task, "pause").status_code == 200
    task.refresh_from_db()
    marker = task.instant_paused_before_start_at
    assert marker is not None

    detail = auth_client.patch(f"{BASE}{task.pk}/", {"name": "new name"}, format="json")
    assert detail.status_code == 200, detail.content
    task.refresh_from_db()
    assert task.instant_paused_before_start_at == marker

    send = Mock()
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", send)
    rerun = auth_client.patch(
        f"{BASE}update_eval_task/",
        {"eval_task_id": str(task.pk), "edit_type": "fresh_run"},
        format="json",
    )
    assert rerun.status_code == 200, rerun.content
    send.assert_called_once_with(task, replace_existing=True)
    task.refresh_from_db()
    assert task.instant_paused_before_start_at is None
    assert not EvalTaskStartRequest.objects.filter(task=task).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("fake_view", "no_new_usage")
@pytest.mark.parametrize(
    "stale_read",
    [False, True],
    ids=["marked-sample-now-completed", "pause-marker-missed-at-load"],
)
def test_refused_read_more_leaves_the_run_untouched(
    auth_client,
    observe_project,
    yes_no,
    started,
    monkeypatch,
    stale_read,  # noqa: F811
):
    monkeypatch.setattr(
        "tracer.views.instant_eval_scale.credits_per_answer", lambda: None
    )
    response = _start(auth_client, observe_project, [yes_no], sample_size=5)
    assert response.status_code == 200, response.content
    task = EvalTask.objects.get(pk=response.json()["result"]["task_id"])
    assert _control(auth_client, observe_project, task, "pause").status_code == 200
    EvalTask.objects.filter(pk=task.pk).update(status="completed")
    task.refresh_from_db()
    assert task.instant_paused_before_start_at is not None
    assert not task.instant_rows.exists()
    assert not EvalTaskStartRequest.objects.filter(task=task).exists()
    runs = InstantEvalRun.objects.filter(eval_task=task)
    assert _rows_left(task, runs.get()) == 0
    run_before = runs.values().get()
    task_before = (task.status, task.spans_limit, task.instant_paused_before_start_at)
    if stale_read:
        # The routed read missed the marker. The sender must see it on the
        # writer and roll back Read more's new limit, version and credit estimate.
        task.instant_paused_before_start_at = None
        monkeypatch.setattr(
            "tracer.views.instant_eval_scale._load", lambda *_: (task, None)
        )
    started.clear()  # Do not count the native sample's original submission.
    response = _control(auth_client, observe_project, task, "read_more", more=1)
    assert response.status_code == 400, response.content
    assert response.json()["message"] == "This task was paused before it started."
    assert started == []
    assert runs.values().get() == run_before
    task.refresh_from_db()
    assert (
        task.status,
        task.spans_limit,
        task.instant_paused_before_start_at,
    ) == task_before


@pytest.mark.django_db
@pytest.mark.usefixtures("fake_view", "no_new_usage")
@pytest.mark.parametrize(
    "history,expected_status",
    [
        pytest.param(
            client.WF_ABSENT, 400, id="legacy-pending-failed-answer-workflow-gone"
        ),
        pytest.param(
            client.WF_CLOSED, 400, id="legacy-pending-failed-answer-workflow-closed"
        ),
        pytest.param(
            TimeoutError(), 503, id="legacy-pending-failed-answer-workflow-unknown"
        ),
        pytest.param(
            client.WF_PROGRESSING,
            200,
            id="legacy-pending-failed-answer-workflow-running",
        ),
    ],
)
def test_retry_failed_on_active_task(
    auth_client,
    observe_project,
    yes_no,
    started,
    monkeypatch,
    history,
    expected_status,  # noqa: F811
):
    rid = str(UUID(int=1))
    task = EvalTask.objects.create(
        project=observe_project,
        status="pending",
        run_type="historical",
        row_type="sessions",
        filters={"session_id": [rid]},
    )
    task.evals.add(yes_no)
    _prepared_run(task, {}, rate=None)
    [entry] = EvalLogger.objects.bulk_create([_answer(task, yes_no, rid, error=True)])
    assert record_answer(entry.pk) == 1
    assert not EvalTaskStartRequest.objects.filter(task=task).exists()
    runs = InstantEvalRun.objects.filter(eval_task=task)
    assert _rows_left(task, runs.get()) == 0
    entries = EvalLogger.objects.filter(pk=entry.pk)
    entry_before = entries.values().get()
    answer_before = task.instant_answers.values().get()
    version = runs.get().answers_version
    describe = Mock(side_effect=[history])
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    # A cached stall-display answer is not evidence for a write.
    cache.set(f"ie-wf-gone:{task.pk}", False)
    response = _control(auth_client, observe_project, task, "retry_failed")
    assert response.status_code == expected_status, response.content
    describe.assert_called_once()
    assert started == []
    task.refresh_from_db()
    assert task.status == "pending"
    if expected_status == 200:
        assert response.json()["result"]["retried"] == 1
        entry.refresh_from_db()
        assert entry.status == "pending" and not entry.error
        assert not task.instant_answers.exists()
        assert runs.get().answers_version == version + 1
    else:
        message = response.json()["message"]
        if expected_status == 503:
            assert "Try again shortly." in message
        else:
            assert message == "A pending task cannot be resumed."
        assert entries.values().get() == entry_before
        assert task.instant_answers.values().get() == answer_before
        assert runs.get().answers_version == version


@pytest.mark.django_db
@pytest.mark.usefixtures("fake_view", "no_new_usage")
@pytest.mark.parametrize(
    "history,http_status,final_status",
    [
        pytest.param(
            TimeoutError(), 503, "completed", id="completed-timeout-preserves-answer"
        ),
        pytest.param(
            client.WF_PROGRESSING, 200, "running", id="stale-completed-requeues-only"
        ),
        pytest.param(client.WF_CLOSED, 200, "pending", id="completed-closed-restarts"),
    ],
)
def test_retry_failed_on_completed_native_run(
    auth_client,
    observe_project,
    yes_no,
    started,
    monkeypatch,
    history,
    http_status,
    final_status,  # noqa: F811
):
    monkeypatch.setattr(
        "tracer.views.instant_eval_scale.credits_per_answer", lambda: None
    )
    response = _start(auth_client, observe_project, [yes_no], sample_size=1)
    assert response.status_code == 200, response.content
    task = EvalTask.objects.get(pk=response.json()["result"]["task_id"])
    [rid] = resolve_desired_rows(task).matched_ids
    [entry] = EvalLogger.objects.bulk_create([_answer(task, yes_no, rid, error=True)])
    assert record_answer(entry.pk) == 1
    task.status = "completed"
    task.save(update_fields=["status"])
    run = InstantEvalRun.objects.get(eval_task=task)
    assert _rows_left(task, run) == 0
    entries = EvalLogger.objects.filter(pk=entry.pk)
    entry_before = entries.values().get()
    answer_before = task.instant_answers.values().get()
    version = run.answers_version
    started.clear()  # Exclude the original submission.
    if history == client.WF_PROGRESSING:
        # The writer moved on, but the control action loaded the older Completed row.
        EvalTask.objects.filter(pk=task.pk).update(status="running")
        monkeypatch.setattr(
            "tracer.views.instant_eval_scale._load", lambda *_: (task, None)
        )
    describe = Mock(side_effect=[history])
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)

    response = _control(auth_client, observe_project, task, "retry_failed")
    assert response.status_code == http_status, response.content
    describe.assert_called_once_with(task.pk)
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == final_status
    assert started == (
        [(str(task.pk), {"replace_existing": True})]
        if history == client.WF_CLOSED
        else []
    )
    if http_status == 503:
        assert (
            response.json()["message"]
            == "The run's status couldn't be checked. Try again shortly."
        )
        assert entries.values().get() == entry_before
        assert task.instant_answers.values().get() == answer_before
        assert run.answers_version == version
    else:
        assert response.json()["result"]["retried"] == 1
        entry.refresh_from_db()
        assert entry.status == "pending" and not entry.error
        assert not task.instant_answers.exists()
        assert run.answers_version == version + 1


@pytest.mark.django_db
@pytest.mark.usefixtures("no_new_usage")
@pytest.mark.parametrize(
    "state,status,older_pending,candidates,restarts",
    [
        pytest.param(
            "accepted",
            "running",
            None,
            1,
            False,
            id="sweep-watched-running-with-pending-entry",
        ),
        pytest.param(
            None, "running", None, 1, True, id="sweep-legacy-running-with-pending-entry"
        ),
        pytest.param(
            None,
            "pending",
            None,
            0,
            False,
            id="sweep-legacy-pending-with-pending-entry",
        ),
        pytest.param(
            None,
            "running",
            "entry",
            1,
            True,
            id="sweep-old-pending-entry-cannot-hold-the-slot",
        ),
        pytest.param(
            None,
            "running",
            "cursor",
            1,
            True,
            id="sweep-old-pending-cursor-cannot-hold-the-slot",
        ),
    ],
)
def test_stranded_sweep_rows(
    observe_project,
    yes_no,
    monkeypatch,
    state,
    status,
    older_pending,
    candidates,
    restarts,  # noqa: F811
):
    if older_pending:
        old = timezone.now() - timedelta(days=1)
        if older_pending == "entry":
            parked = EvalTask.objects.create(
                project=observe_project, status="pending", run_type="historical"
            )
            [entry] = EvalLogger.objects.bulk_create(
                [_answer(parked, yes_no, str(uuid4()), pending=True)]
            )
            EvalLogger.objects.filter(pk=entry.pk).update(updated_at=old)
        else:
            EvalTask.objects.create(
                project=observe_project,
                status="pending",
                run_type="continuous",
                continuous_cursor=old,
            )
    task = EvalTask.objects.create(
        project=observe_project, status=status, run_type="historical"
    )
    EvalLogger.objects.bulk_create([_answer(task, yes_no, str(uuid4()), pending=True)])
    if state:
        EvalTaskStartRequest.objects.create(
            task=task,
            state=state,
            attempts=1,
            admitted_run_id=uuid4(),
            last_outcome="worker_admitted",
            lease_until=timezone.now() + timedelta(minutes=2),
        )
    describe = Mock(return_value=client.WF_CLOSED)
    start = Mock()
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    monkeypatch.setattr(client, "start_recovery_workflow_sync", start)
    result = recover_stranded_tasks(limit=1, stale_running_seconds=7200)
    assert result["candidates"] == candidates and result["errors"] == 0
    assert result["restarted"] == int(restarts)
    assert result["deferred"] == candidates - int(restarts)
    if candidates:
        describe.assert_called_once_with(task.pk)
    else:
        describe.assert_not_called()
    if restarts:
        start.assert_called_once_with(task)
        task.refresh_from_db()
        assert task.workflow_generation is not None
        assert start.call_args.args[0].workflow_generation == task.workflow_generation
    else:
        start.assert_not_called()
