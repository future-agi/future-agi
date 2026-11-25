"""The workflow-generation fence lives in the activities.

A run that carries a generation writes nothing once its task has been handed
to another run. A run without one (a pre-fence start, or a new start whose
generation an older worker dropped) behaves exactly as on v1.59, whatever
stamp the row holds, so a mixed-version rollout never strands it.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import OperationalError, connection, connections, transaction

from tfc.temporal.eval_tasks import activities
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger


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


@pytest.mark.django_db(transaction=True)
def test_replaced_run_cannot_claim_the_new_owners_batch(
    eval_task, make_pending_entries
):
    """Reconcile refuses a replaced run, but a continuous run claims next: the
    claim itself must select nothing once the task is handed over."""
    first, second = make_pending_entries(eval_task, 2)
    current = _stamp(eval_task, EvalTaskStatus.RUNNING)
    task_id = str(eval_task.pk)

    assert activities._claim_batch_sync(task_id, 5, str(uuid.uuid4())) == {
        "entry_ids": []
    }
    assert set(
        EvalLogger.objects.filter(pk__in=[first.pk, second.pk]).values_list(
            "status", flat=True
        )
    ) == {EvalEntryStatus.PENDING}

    assert activities._claim_batch_sync(task_id, 1, current) == {
        "entry_ids": [str(first.pk)]
    }
    # A run without a generation is unfenced, as on v1.59.
    assert activities._claim_batch_sync(task_id, 1) == {"entry_ids": [str(second.pk)]}


def _probe_task_lock_while_recording_answers(monkeypatch, task):
    """Record, from inside the Instant answer write, whether another session
    could lock the task row at that moment. Instant eval cancel, Resume and
    retry lock the run row and then the task row, and the answer write locks
    the run row: doing it under the task lock is a lock-order inversion."""
    from tracer.services.eval_tasks import reaper

    original = reaper._record_instant_answers
    seen = []

    def contender():
        try:
            with transaction.atomic():
                EvalTask.all_objects.select_for_update(nowait=True).get(pk=task.pk)
            return "free"
        except OperationalError as exc:
            return exc.__cause__.sqlstate
        finally:
            connections.close_all()

    def recording(task, *args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            seen.append(pool.submit(contender).result(timeout=5))
        return original(task, *args, **kwargs)

    monkeypatch.setattr(reaper, "_record_instant_answers", recording)
    return seen


@pytest.fixture
def instant_stale_entry(stale_running_entry):
    """``stale_running_entry`` on a task an Instant eval run reads: its span
    is one of the run's rows, so a given-up entry has an answer to copy."""
    from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun

    task, entry = stale_running_entry
    InstantEvalRun.objects.create(
        eval_task=task,
        project=task.project,
        organization=task.project.organization,
        unit="spans",
        row_type="spans",
        rows_requested=1,
        population=1,
    )
    InstantEvalRow.objects.create(
        task=task, row_id=entry.observation_span_id, unit="spans"
    )
    return task, entry


def _answers(task):
    from tracer.models.instant_eval import InstantEvalAnswer

    return list(
        InstantEvalAnswer.objects.filter(task=task).values_list("label", flat=True)
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("guard", ["generation", "require_running"])
def test_fenced_reap_records_given_up_answers_after_the_task_lock(
    instant_stale_entry, monkeypatch, guard
):
    from tracer.services.instant_eval_summary import ERROR

    task, entry = instant_stale_entry
    current = _stamp(task, EvalTaskStatus.RUNNING)
    EvalLogger.objects.filter(pk=entry.pk).update(attempts=3)
    seen = _probe_task_lock_while_recording_answers(monkeypatch, task)
    kwargs = (
        {"workflow_generation": current}
        if guard == "generation"
        else {"require_running": True}
    )

    reaped = activities._reap_sync(str(task.pk), 600, 3, True, **kwargs)

    assert (reaped["requeued"], reaped["failed"]) == (0, 1)
    assert seen == ["free"]
    entry.refresh_from_db()
    assert entry.status == EvalEntryStatus.ERRORED
    assert _answers(task) == [ERROR]


@pytest.mark.django_db(transaction=True)
def test_recovery_records_given_up_answers_after_the_task_lock(
    instant_stale_entry, monkeypatch
):
    from tfc.temporal.eval_tasks import client
    from tracer.services.eval_tasks.recovery import recover_task
    from tracer.services.instant_eval_summary import ERROR

    task, entry = instant_stale_entry
    EvalTask.objects.filter(pk=task.pk).update(status=EvalTaskStatus.RUNNING)
    task.refresh_from_db()
    EvalLogger.objects.filter(pk=entry.pk).update(attempts=3)
    monkeypatch.setattr(
        client, "describe_eval_task_workflow_sync", lambda _id: client.WF_CLOSED
    )
    monkeypatch.setattr(client, "start_recovery_workflow_sync", lambda _task: "wf")
    seen = _probe_task_lock_while_recording_answers(monkeypatch, task)

    outcome = recover_task(task, stale_running_seconds=7_200)

    assert (outcome["failed"], outcome["restarted"]) == (1, True)
    assert seen == ["free"]
    assert _answers(task) == [ERROR]


@pytest.mark.django_db
def test_given_up_answers_read_their_entries_from_the_primary(
    instant_stale_entry, monkeypatch
):
    """With EvalLogger reads routed to a replica, the entries the reap failed
    a moment ago may not be there yet."""
    from tfc.routers import ReadReplicaRouter
    from tracer.services.eval_tasks.reaper import reap_stale_running

    task, entry = instant_stale_entry
    EvalLogger.objects.filter(pk=entry.pk).update(attempts=3)
    route = ReadReplicaRouter.db_for_read
    monkeypatch.setattr(
        ReadReplicaRouter,
        "db_for_read",
        lambda self, model, **hints: (
            "replica" if model is EvalLogger else route(self, model, **hints)
        ),
    )
    copied = []
    monkeypatch.setattr(
        "tracer.services.instant_eval_answers.record_answers",
        lambda _task_id, ids: copied.extend(str(i) for i in ids) or 0,
    )

    assert reap_stale_running(task, older_than_seconds=600, max_attempts=3) == (0, 1)
    assert copied == [str(entry.pk)]


@pytest.mark.django_db(transaction=True)
def test_a_retry_between_the_reap_and_its_answer_copy_is_not_undone(
    instant_stale_entry, monkeypatch
):
    """Retry requeues a given-up entry and drops its answer while the fenced
    reap, its task lock released, is copying that answer. The copy must not
    bring the dropped answer back over the requeued entry."""
    from django.db.models import F

    from tracer.models.instant_eval import InstantEvalRun
    from tracer.services.eval_tasks.reaper import StaleRunningClaims
    from tracer.services.instant_eval_answers import forget_answers

    task, entry = instant_stale_entry
    current = _stamp(task, EvalTaskStatus.RUNNING)
    EvalLogger.objects.filter(pk=entry.pk).update(attempts=3)
    reaped, retrying, copied, dropped = Event(), Event(), Event(), Event()
    record = StaleRunningClaims.record_failed_answers

    def record_while_retry_runs(self):
        # The reap has committed and released the task lock.
        reaped.set()
        assert retrying.wait(10)

        def on_query(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql.startswith("INSERT INTO tracer_instant_eval_answer"):
                # Keep the copy open across the Retry's answer delete.
                copied.set()
                assert dropped.wait(10)
            return result

        with connection.execute_wrapper(on_query):
            return record(self)

    monkeypatch.setattr(
        StaleRunningClaims, "record_failed_answers", record_while_retry_runs
    )

    def worker():
        try:
            return activities._reap_sync(
                str(task.pk), 600, 3, True, workflow_generation=current
            )
        finally:
            copied.set()  # finished, whether or not it copied anything
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(worker)
        assert reaped.wait(10), "the reap never reached its answer copy"
        # The Instant eval Retry (``_retry_failed``), one statement at a time.
        with transaction.atomic():
            assert (
                EvalLogger.objects.filter(
                    pk=entry.pk, status=EvalEntryStatus.ERRORED
                ).update(
                    status=EvalEntryStatus.PENDING,
                    error=False,
                    error_message=None,
                    skipped_reason=None,
                    attempts=0,
                )
                == 1
            )
            retrying.set()
            assert copied.wait(10)
            forget_answers(task.id, [entry.pk])
            dropped.set()
            InstantEvalRun.objects.filter(eval_task=task).update(
                answers_version=F("answers_version") + 1
            )
        reaped_result = result.result(timeout=30)

    assert reaped_result["failed"] == 1
    entry.refresh_from_db()
    assert entry.status == EvalEntryStatus.PENDING
    assert _answers(task) == []


class _WorkerExit(BaseException):
    """The worker process stops: nothing after this point runs."""


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("guard", ["generation", "require_running"])
def test_an_answer_copy_lost_after_the_reap_committed_is_made_on_retry(
    instant_stale_entry, monkeypatch, guard
):
    from tracer.services.eval_tasks.reaper import StaleRunningClaims
    from tracer.services.instant_eval_summary import ERROR

    task, entry = instant_stale_entry
    current = _stamp(task, EvalTaskStatus.RUNNING)
    EvalLogger.objects.filter(pk=entry.pk).update(attempts=3)
    kwargs = (
        {"workflow_generation": current}
        if guard == "generation"
        else {"require_running": True}
    )

    def worker_exits(self):
        raise _WorkerExit

    with monkeypatch.context() as patch:
        patch.setattr(StaleRunningClaims, "record_failed_answers", worker_exits)
        with pytest.raises(_WorkerExit):
            activities._reap_sync(str(task.pk), 600, 3, True, **kwargs)
    entry.refresh_from_db()
    assert entry.status == EvalEntryStatus.ERRORED  # the reap committed
    assert _answers(task) == []

    # Temporal retries the activity; nothing is left for it to reap.
    retried = activities._reap_sync(str(task.pk), 600, 3, True, **kwargs)
    assert (retried["requeued"], retried["failed"]) == (0, 0)
    assert _answers(task) == [ERROR]

    # Once copied, later reaps find nothing to copy.
    copies = []
    monkeypatch.setattr(
        "tracer.services.instant_eval_answers.record_answers",
        lambda *args: copies.append(args) or 0,
    )
    activities._reap_sync(str(task.pk), 600, 3, True, **kwargs)
    assert copies == []
