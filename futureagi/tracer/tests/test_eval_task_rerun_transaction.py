"""Transaction boundary contracts for eval-task edit/rerun dispatch."""

import pytest
from django.db import connection
from rest_framework import status

from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync as real_start
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType

_UPDATE = "/tracer/eval-task/update_eval_task/"


@pytest.fixture
def historical_task(db, project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="rerun transaction task",
        filters={},
        sampling_rate=100.0,
        spans_limit=100,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.FAILED,
        row_type=RowType.SPANS,
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_update_dispatches_only_after_pending_state_commits(
    auth_client, historical_task, monkeypatch
):
    observed = {}

    def _start(task, *, replace_existing=False):
        observed["in_atomic_block"] = connection.in_atomic_block
        observed["status"] = EvalTask.objects.get(id=task.id).status
        observed["replace_existing"] = replace_existing
        return f"eval-task-{task.id}"

    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", _start)

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "committed rerun",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert observed == {
        "in_atomic_block": False,
        "status": EvalTaskStatus.PENDING,
        "replace_existing": True,
    }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_post_commit_dispatch_failure_is_returned_and_pending_state_is_retryable(
    auth_client, historical_task, monkeypatch
):
    def _fail(_task, *, replace_existing=False):
        assert connection.in_atomic_block is False
        assert replace_existing is True
        raise RuntimeError("temporal dispatch unavailable")

    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", _fail)

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "committed despite dispatch failure",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Evaluation task could not be updated" in response.content.decode()

    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING
    assert historical_task.name == "committed despite dispatch failure"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_duplicate_pending_rerun_replaces_once_and_remains_recoverable(
    auth_client, historical_task, monkeypatch
):
    active_runs = []
    starts = []

    def _replace(task, *, replace_existing=False):
        assert connection.in_atomic_block is False
        assert replace_existing is True
        if active_runs:
            active_runs.pop()
        run_id = f"run-{len(starts) + 1}"
        active_runs.append(run_id)
        starts.append((str(task.id), run_id))
        return f"eval-task-{task.id}"

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", _replace
    )

    payload = {
        "eval_task_id": str(historical_task.id),
        "name": "same pending rerun",
        "edit_type": "fresh_run",
    }
    first = auth_client.patch(_UPDATE, payload, format="json")
    second = auth_client.patch(_UPDATE, payload, format="json")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert len(starts) == 2
    assert active_runs == ["run-2"]
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_running_task_rejects_rerun_without_replacing_workflow(
    auth_client, historical_task, monkeypatch
):
    historical_task.status = EvalTaskStatus.RUNNING
    historical_task.save(update_fields=["status"])
    starts = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    response = auth_client.patch(
        _UPDATE,
        {
            "eval_task_id": str(historical_task.id),
            "name": "must not rerun",
            "edit_type": "fresh_run",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert starts == []
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.RUNNING


@pytest.mark.django_db(transaction=True)
def test_rejected_resume_preserves_zero_entry_diagnostic(
    auth_client, historical_task, monkeypatch
):
    reason = "Evaluation task row selection exceeded its read budget. Narrow the time range and retry."
    historical_task.failure_reason = reason
    historical_task.save(update_fields=["failure_reason"])

    def unavailable(task, *, replace_existing):
        assert replace_existing
        raise RuntimeError("dispatch unavailable")

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", unavailable
    )
    response = auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={historical_task.pk}", {}
    )
    assert response.status_code == 400
    logs = auth_client.get(
        "/tracer/eval-task/get_eval_task_logs/",
        {"eval_task_id": str(historical_task.pk)},
    ).json()["result"]
    assert (logs["status"], logs["failure_reason"], logs["total_count"]) == (
        "failed",
        reason,
        0,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("newer_action", ["rerun", "pause"])
def test_stale_replacement_callback_does_not_contact_temporal(
    historical_task, monkeypatch, newer_action
):
    from unittest.mock import Mock

    from tfc.temporal.eval_tasks import client

    historical_task.prepare_workflow_replacement()
    newer = EvalTask.objects.get(pk=historical_task.pk)
    if newer_action == "rerun":
        newer.prepare_workflow_replacement()
    else:
        newer.status = EvalTaskStatus.PAUSED
        newer.save(update_fields=["status"])
    describe, dispatch = Mock(), Mock()
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    monkeypatch.setattr(client, "start_workflow_sync", dispatch)

    real_start(historical_task, replace_existing=True)

    describe.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("accepted", [True, False])
def test_early_worker_waits_for_resume_commit(
    auth_client, historical_task, monkeypatch, accepted
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connections

    from tfc.temporal.eval_tasks.activities import _get_task_state_sync

    waiting = Event()
    task_id = str(historical_task.pk)

    def worker(generation):
        # Observe the actual admission read from a second PostgreSQL session.
        def on_query(execute, sql, params, many, context):
            if "FOR UPDATE" in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(on_query):
                return _get_task_state_sync(task_id, generation)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:

        def start(task, **kwargs):
            nonlocal admission
            admission = pool.submit(worker, str(task.workflow_generation))
            assert waiting.wait(5), "worker did not wait for the uncommitted generation"
            if not accepted:
                raise RuntimeError("start acknowledgement lost")

        admission = None
        monkeypatch.setattr(
            "tracer.views.eval_task.start_eval_task_workflow_sync", start
        )
        response = auth_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={task_id}", {}
        )
        assert response.status_code == (200 if accepted else 400)
        state = admission.result(timeout=5)
        assert state["active"] is accepted
        assert state["status"] == ("pending" if accepted else "failed")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("run_type", [RunType.HISTORICAL, RunType.CONTINUOUS])
def test_superseded_workflow_cannot_publish_its_status(
    auth_client, historical_task, monkeypatch, run_type
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from asgiref.sync import async_to_sync
    from temporalio.testing import ActivityEnvironment

    from tfc.temporal.eval_tasks import activities, client
    from tfc.temporal.eval_tasks.types import FinalizeInput, SetStatusInput

    historical_task.run_type = run_type
    historical_task.save(update_fields=["run_type"])
    starts = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", real_start
    )
    monkeypatch.setattr(activities, "notify_eval_task_failed", AsyncMock())
    monkeypatch.setattr(client, "_describe_says_nothing_is_draining", lambda _: True)

    def accepted(**kwargs):
        starts.append(kwargs["workflow_input"])
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", accepted)
    url = f"/tracer/eval-task/unpause_eval_task/?eval_task_id={historical_task.pk}"
    env = ActivityEnvironment()
    run = async_to_sync(env.run)
    task_id = str(historical_task.pk)
    old = SetStatusInput(task_id, "failed", failure_reason="from the old run")
    for _ in range(2):
        assert auth_client.post(url, {}).status_code == 200
        assert not run(activities.set_eval_task_status_activity, old).changed
        if run_type == RunType.HISTORICAL:
            assert not run(
                activities.finalize_eval_task_activity,
                FinalizeInput(task_id, old.workflow_generation),
            ).finalized
        historical_task.refresh_from_db()
        assert (
            historical_task.status == "pending"
            and historical_task.failure_reason is None
        )
        # The newly dispatched run can publish; its delayed delivery must be
        # ignored after a subsequent resume, just like a pre-upgrade payload.
        old = SetStatusInput(
            task_id,
            "failed",
            failure_reason="from this run",
            workflow_generation=starts[-1].workflow_generation,
        )
        assert run(activities.set_eval_task_status_activity, old).changed
        historical_task.refresh_from_db()
        assert historical_task.failure_reason == "from this run"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
def test_unpause_replaces_a_still_closing_paused_workflow(
    auth_client, historical_task, monkeypatch
):
    historical_task.status = EvalTaskStatus.PAUSED
    historical_task.save(update_fields=["status"])
    starts = []

    def _replace(task, *, replace_existing=False):
        starts.append((str(task.id), replace_existing))
        return f"eval-task-{task.id}"

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", _replace
    )

    response = auth_client.post(
        f"/tracer/eval-task/unpause_eval_task/?eval_task_id={historical_task.id}",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert starts == [(str(historical_task.id), True)]
    historical_task.refresh_from_db()
    assert historical_task.status == EvalTaskStatus.PENDING
