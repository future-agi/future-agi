"""The workflow-generation fence lives in the activities (TH-7963, #4199).

A run that carries a generation writes nothing once its task has been handed
to another run. A run without one (a pre-fence start, or a new start whose
generation an older worker dropped) behaves exactly as on v1.59, whatever
stamp the row holds, so a mixed-version rollout never strands it.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection, connections, transaction

from tfc.temporal.eval_tasks import activities
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus


def _stamp(task, status):
    task.prepare_workflow_replacement(status=status)
    return str(task.workflow_generation)


@pytest.mark.django_db(transaction=True)
def test_run_without_generation_is_not_fenced_by_a_new_api_stamp(eval_task):
    # A new-API start that a v1.59 workflow worker ran: it sends no generation.
    _stamp(eval_task, EvalTaskStatus.PENDING)
    task_id = str(eval_task.pk)

    assert activities._get_task_state_sync(task_id)["active"] is True
    assert activities._set_task_status_sync(
        task_id, EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )["changed"]
    assert activities._finalize_task_sync(task_id)["finalized"] is True
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
def test_replaced_run_cannot_publish_status_or_completion(eval_task):
    current = _stamp(eval_task, EvalTaskStatus.RUNNING)
    stale, task_id = str(uuid.uuid4()), str(eval_task.pk)

    state = activities._get_task_state_sync(task_id, stale)
    assert (state["active"], state["status"]) == (False, EvalTaskStatus.RUNNING)
    assert not activities._set_task_status_sync(
        task_id, EvalTaskStatus.FAILED, failure_reason="late", workflow_generation=stale
    )["changed"]
    assert activities._finalize_task_sync(task_id, stale)["finalized"] is False
    eval_task.refresh_from_db()
    assert (eval_task.status, eval_task.failure_reason) == (
        EvalTaskStatus.RUNNING,
        None,
    )
    assert activities._get_task_state_sync(task_id, current)["active"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("require_running", [False, True])
def test_replaced_run_cannot_reap_the_new_owners_claims(
    stale_running_entry, require_running
):
    task, entry = stale_running_entry
    current = _stamp(task, EvalTaskStatus.RUNNING)
    guard = {"require_running": True} if require_running else {}

    reaped = activities._reap_sync(
        str(task.pk), 600, 3, True, workflow_generation=str(uuid.uuid4()), **guard
    )
    entry.refresh_from_db()
    assert (reaped["requeued"], reaped["inactive_status"]) == (0, "running")
    assert (entry.status, entry.attempts) == (EvalEntryStatus.RUNNING, 0)

    reaped = activities._reap_sync(
        str(task.pk), 600, 3, True, workflow_generation=current, **guard
    )
    entry.refresh_from_db()
    assert reaped["requeued"] == 1
    assert (entry.status, entry.attempts) == (EvalEntryStatus.PENDING, 1)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("require_running", [False, True])
def test_replaced_run_does_not_reconcile(eval_task, monkeypatch, require_running):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a replaced run reconciled the new owner's entries")

    monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", must_not_run)
    _stamp(eval_task, EvalTaskStatus.RUNNING)

    result = activities._reconcile_sync(
        str(eval_task.pk),
        require_running=require_running,
        workflow_generation=str(uuid.uuid4()),
    )
    assert (result["created"], result["inactive_status"]) == (0, "running")
    assert EvalTask.objects.get(pk=eval_task.pk).status == EvalTaskStatus.RUNNING


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("step", ["mark_running", "reconcile"])
def test_first_write_waits_for_recovery_to_commit_its_handoff(
    eval_task, monkeypatch, step
):
    """Recovery stamps the row and starts the run in one transaction. The run's
    first writes must wait for that commit, not skip the row as replaced."""
    from tracer.services.eval_tasks.reconciler import ReconcileResult

    monkeypatch.setattr(
        "tracer.services.eval_tasks.reconciler.reconcile",
        lambda task: ReconcileResult(),
    )
    EvalTask.objects.filter(pk=eval_task.pk).update(status=EvalTaskStatus.FAILED)
    task_id = str(eval_task.pk)
    waiting = Event()

    def worker(generation):
        # A second PostgreSQL session, as the worker's would be.
        def on_query(execute, sql, params, many, context):
            if "FOR UPDATE" in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(on_query):
                if step == "mark_running":
                    return activities._set_task_status_sync(
                        task_id,
                        EvalTaskStatus.RUNNING,
                        EvalTaskStatus.PENDING,
                        workflow_generation=generation,
                    )
                return activities._reconcile_sync(
                    task_id, workflow_generation=generation
                )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            eval_task.prepare_workflow_replacement()
            result = pool.submit(worker, str(eval_task.workflow_generation))
            assert waiting.wait(5), "the run did not wait for the uncommitted stamp"
        result = result.result(timeout=10)

    if step == "mark_running":
        assert result["changed"]
        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.RUNNING
    else:
        assert result.get("inactive_status") is None
