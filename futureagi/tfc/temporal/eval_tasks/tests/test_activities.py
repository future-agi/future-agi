"""Unit tests for the eval-task activity sync helpers — the testable core that
each ``@activity.defn`` wrapper delegates to. They reload by id and call the
PR4/5 services; CH-backed reconcile is mocked (materialize is covered in PR4)."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger


@pytest.mark.asyncio
async def test_exact_selection_budget_error_is_retryable(monkeypatch):
    from temporalio.exceptions import ApplicationError

    import tfc.temporal.eval_tasks.activities as activities
    from tfc.temporal.eval_tasks.types import ReconcileActivityInput
    from tracer.selectors.eval_tasks.row_resolver import EvalTaskReadBudgetExceeded

    class NoopHeartbeater:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def fake_sync_to_async(function, **_kwargs):
        async def invoke(*args, **kwargs):
            return function(*args, **kwargs)

        return invoke

    def reject(_task_id):
        raise EvalTaskReadBudgetExceeded(
            "Evaluation task row selection exceeded its read budget. "
            "Narrow the time range and retry."
        )

    monkeypatch.setattr(activities, "Heartbeater", NoopHeartbeater)
    monkeypatch.setattr(activities, "otel_sync_to_async", fake_sync_to_async)
    monkeypatch.setattr(activities, "_reconcile_sync", reject)

    with pytest.raises(ApplicationError) as captured:
        await activities.reconcile_eval_task_activity(
            ReconcileActivityInput(task_id="task-id")
        )

    assert captured.value.non_retryable is False
    assert captured.value.type == "EvalTaskReadBudgetExceeded"
    assert "Narrow the time range" in str(captured.value)


@pytest.mark.asyncio
async def test_deterministic_selection_rejection_is_non_retryable(monkeypatch):
    from temporalio.exceptions import ApplicationError

    import tfc.temporal.eval_tasks.activities as activities
    from tfc.temporal.eval_tasks.types import ReconcileActivityInput
    from tracer.selectors.eval_tasks.row_resolver import EvalTaskSelectionRejected

    class NoopHeartbeater:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def fake_sync_to_async(function, **_kwargs):
        async def invoke(*args, **kwargs):
            return function(*args, **kwargs)

        return invoke

    def reject(_task_id):
        raise EvalTaskSelectionRejected(
            "Evaluation task row selection contains a filter that cannot be "
            "resolved safely. Update the filters and retry."
        )

    monkeypatch.setattr(activities, "Heartbeater", NoopHeartbeater)
    monkeypatch.setattr(activities, "otel_sync_to_async", fake_sync_to_async)
    monkeypatch.setattr(activities, "_reconcile_sync", reject)

    with pytest.raises(ApplicationError) as captured:
        await activities.reconcile_eval_task_activity(
            ReconcileActivityInput(task_id="task-id")
        )

    assert captured.value.non_retryable is True
    assert captured.value.type == "EvalTaskSelectionRejected"
    assert "cannot be resolved safely" in str(captured.value)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestReconcileActivitySync:
    def test_delegates_to_service_and_maps_counts(self, monkeypatch, eval_task):
        from tracer.services.eval_tasks.reconciler import ReconcileResult

        captured = {}

        def _fake(task):
            captured["task_id"] = str(task.id)
            return ReconcileResult(created=3, requeued=1, dropped=2)

        monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", _fake)
        from tfc.temporal.eval_tasks.activities import _reconcile_sync

        out = _reconcile_sync(str(eval_task.id))
        assert captured["task_id"] == str(eval_task.id)
        assert out == {
            "task_id": str(eval_task.id),
            "created": 3,
            "requeued": 1,
            "dropped": 2,
        }


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestClaimBatchActivitySync:
    def test_returns_ids_and_marks_running(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 4)
        from tfc.temporal.eval_tasks.activities import _claim_batch_sync

        out = _claim_batch_sync(str(eval_task.id), 3)
        assert len(out["entry_ids"]) == 3
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), status=EvalEntryStatus.RUNNING
            ).count()
            == 3
        )

    def test_empty_when_no_pending(self, eval_task):
        from tfc.temporal.eval_tasks.activities import _claim_batch_sync

        assert _claim_batch_sync(str(eval_task.id), 5) == {"entry_ids": []}


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestRunEntryActivitySync:
    def test_delegates_and_returns_status(
        self, monkeypatch, eval_task, make_pending_entries
    ):
        [entry] = make_pending_entries(eval_task, 1)
        seen = {}

        def _fake(e):
            seen["id"] = str(e.id)
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _fake)
        from tfc.temporal.eval_tasks.activities import _run_entry_sync

        out = _run_entry_sync(str(entry.id))
        assert seen["id"] == str(entry.id)
        # ``task_id`` rides along so the activity wrapper can log which task a
        # run belonged to without naming the entry in a log line.
        assert out == {
            "entry_id": str(entry.id),
            "task_id": str(eval_task.id),
            "status": EvalEntryStatus.COMPLETED,
        }

    def test_deleted_when_entry_gone(self, eval_task, make_pending_entries):
        [entry] = make_pending_entries(eval_task, 1)
        entry.delete()  # soft-delete
        from tfc.temporal.eval_tasks.activities import _run_entry_sync

        assert _run_entry_sync(str(entry.id))["status"] == "deleted"


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestFailEntryActivitySync:
    """The fail activity is the last write of a run that could not finish.

    It is handed an entry id and nothing else, so the only claim it can fence
    on is the one its own read saw. That closes exactly one window -- between
    that read and its write -- and nothing else covered it: deleting the
    ``epoch=`` argument left every module in this change green.
    """

    @staticmethod
    def _running_entry(eval_task, make_pending_entries):
        [entry] = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)
        return entry

    def test_a_run_that_exhausted_its_retries_is_errored_once(
        self, monkeypatch, eval_task, make_pending_entries
    ):
        import tfc.temporal.eval_tasks.activities as act

        entry = self._running_entry(eval_task, make_pending_entries)
        monkeypatch.setattr(act, "close_old_connections", lambda: None)

        out = act._fail_entry_sync(str(entry.id))

        entry.refresh_from_db()
        assert out == {"entry_id": str(entry.id), "status": EvalEntryStatus.ERRORED}
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error_message == "run_entry activity failed after retries"

    def test_a_claim_that_moved_under_the_read_refuses_the_write_and_says_so(
        self, monkeypatch, eval_task, make_pending_entries
    ):
        """The write is fenced on the stamp the read saw, so a claim that moved
        in between -- the row re-stamped by a run that started, or retired and
        re-taken -- keeps its own state instead of being stamped ERRORED under
        a message describing somebody else's run. The activity has to report
        that it wrote nothing, or the workflow records a terminal outcome the
        entry table never took.

        The concurrent re-stamp is driven from ``resolved_config_hash``, which
        runs between the read and the write on the real path.
        """
        import tfc.temporal.eval_tasks.activities as act
        import tracer.services.eval_tasks.config_hash as config_hash_module

        entry = self._running_entry(eval_task, make_pending_entries)
        monkeypatch.setattr(act, "close_old_connections", lambda: None)
        moved_to = timezone.now() + timedelta(seconds=1)
        real_hash = config_hash_module.resolved_config_hash

        def _restamp_then_hash(config):
            EvalLogger.objects.filter(id=entry.id).update(updated_at=moved_to)
            return real_hash(config)

        monkeypatch.setattr(
            config_hash_module, "resolved_config_hash", _restamp_then_hash
        )

        out = act._fail_entry_sync(str(entry.id))

        entry.refresh_from_db()
        assert out == {"entry_id": str(entry.id), "status": "noop"}
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.error_message != "run_entry activity failed after retries"


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestReapActivitySync:
    def test_requeues_stale_running(self, eval_task, make_pending_entries):
        from tracer.services.eval_tasks.reaper import MIN_STALE_RUNNING_SECONDS

        entries = make_pending_entries(eval_task, 2, status=EvalEntryStatus.RUNNING)
        old = timezone.now() - timedelta(seconds=MIN_STALE_RUNNING_SECONDS + 60)
        EvalLogger.objects.filter(id__in=[e.id for e in entries]).update(
            updated_at=old, attempts=0
        )
        from tfc.temporal.eval_tasks.activities import _reap_sync

        out = _reap_sync(str(eval_task.id), 600, 3)
        assert out == {
            "requeued": 2,
            "failed": 0,
            "older_than_seconds": MIN_STALE_RUNNING_SECONDS,
        }
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), status=EvalEntryStatus.PENDING
            ).count()
            == 2
        )

    def test_a_reap_that_cannot_see_the_workflow_waits_out_the_run(
        self, eval_task, make_pending_entries
    ):
        """A reap with no evidence about the workflow applies the floor.

        ``ReapInput``'s own default is 600 s, well inside the window an
        activity of a since-closed execution can still be running on a worker.
        Requeueing there re-claims an entry whose run is still executing: the
        evaluation is paid for twice, one of the row's three reclaims is spent,
        and the first run's result is refused by the fence. This is the branch
        a starter whose describe could not answer lands in.
        """
        from tfc.temporal.eval_tasks.activities import _reap_sync
        from tfc.temporal.eval_tasks.types import ReapInput
        from tracer.services.eval_tasks.reaper import MIN_STALE_RUNNING_SECONDS

        asked = ReapInput(task_id=str(eval_task.id)).older_than_seconds
        in_flight = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)[
            0
        ]
        abandoned = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)[
            0
        ]
        EvalLogger.objects.filter(id=in_flight.id).update(
            updated_at=timezone.now() - timedelta(seconds=1_000), attempts=0
        )
        EvalLogger.objects.filter(id=abandoned.id).update(
            updated_at=timezone.now()
            - timedelta(seconds=MIN_STALE_RUNNING_SECONDS + 60),
            attempts=0,
        )

        out = _reap_sync(str(eval_task.id), asked, 3)

        assert asked == 600
        in_flight.refresh_from_db()
        abandoned.refresh_from_db()
        assert (in_flight.status, in_flight.attempts) == (
            EvalEntryStatus.RUNNING,
            0,
        )
        assert (out["requeued"], out["failed"]) == (1, 0)
        assert abandoned.status == EvalEntryStatus.PENDING
        assert out["older_than_seconds"] == MIN_STALE_RUNNING_SECONDS

    def test_a_restart_that_knows_the_workflow_is_dead_reclaims_at_once(
        self, eval_task, make_pending_entries
    ):
        """The other branch, and the one Resume depends on.

        The starter described the task's workflow id in the moment before this
        execution began and the server said nothing owned it, so no dispatcher
        can take the entries this reap requeues, and ``ReapInput``'s 600 s is
        honoured as asked. Without this the same entry waits ninety minutes: a
        Resume inside that window reclaims nothing, claims nothing, refuses to
        finalize and drives the task straight back to FAILED in seconds.

        What the describe does not rule out is an abandoned evaluation thread
        from the dead execution. Its write is refused by the claim-epoch fence,
        so the row stays correct; the residue is a duplicated evaluation spend,
        which closes with the run-entry thread-leak follow-up.
        """
        from tfc.temporal.eval_tasks.activities import _reap_sync
        from tfc.temporal.eval_tasks.types import ReapInput
        from tracer.services.eval_tasks.reaper import MIN_STALE_RUNNING_SECONDS

        asked = ReapInput(task_id=str(eval_task.id)).older_than_seconds
        stranded = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)[0]
        fresh = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)[0]
        EvalLogger.objects.filter(id=stranded.id).update(
            updated_at=timezone.now() - timedelta(seconds=asked + 60), attempts=0
        )
        EvalLogger.objects.filter(id=fresh.id).update(
            updated_at=timezone.now() - timedelta(seconds=30), attempts=0
        )

        out = _reap_sync(str(eval_task.id), asked, 3, True)

        stranded.refresh_from_db()
        fresh.refresh_from_db()
        assert asked == 600
        assert out["older_than_seconds"] == asked < MIN_STALE_RUNNING_SECONDS
        assert (out["requeued"], out["failed"]) == (1, 0)
        assert stranded.status == EvalEntryStatus.PENDING
        # Still inside the requested threshold: the waiver lowers the floor, it
        # does not make the reap indiscriminate.
        assert fresh.status == EvalEntryStatus.RUNNING


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestTaskStateActivitySync:
    def test_active_with_undrained_work(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 1)
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        out = _get_task_state_sync(str(eval_task.id))
        assert out["active"] is True
        assert out["has_undrained_work"] is True

    def test_paused_is_inactive(self, eval_task):
        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save(update_fields=["status"])
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        assert _get_task_state_sync(str(eval_task.id))["active"] is False


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestWorkflowLabelsActivitySync:
    def test_returns_sa_values_and_memo_context(self, eval_task):
        from tfc.temporal.eval_tasks.activities import _get_workflow_labels_sync

        out = _get_workflow_labels_sync(str(eval_task.id))
        assert out["project_id"] == str(eval_task.project_id)
        assert out["org_id"] == str(eval_task.project.organization_id)
        assert out["run_type"] == "historical"
        assert out["task_name"] == "WF Task"
        assert out["project_name"] == "WF Test Project"
        assert out["org_name"]  # org has a name
        assert "evals=1" in out["config_summary"]
        assert "row_type=spans" in out["config_summary"]


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestRequeueEntriesActivitySync:
    def test_running_entries_reset_to_pending(self, eval_task, make_pending_entries):
        entries = make_pending_entries(eval_task, 3, status=EvalEntryStatus.RUNNING)
        ids = [str(e.id) for e in entries]
        from tfc.temporal.eval_tasks.activities import _requeue_entries_sync

        out = _requeue_entries_sync(str(eval_task.id), ids)
        assert out == {"requeued": 3}
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), status=EvalEntryStatus.PENDING
            ).count()
            == 3
        )

    def test_completed_entries_left_untouched(self, eval_task, make_pending_entries):
        # An entry that finished between the skip and the requeue must not be
        # dragged back to pending — only still-running ones are reset.
        [done] = make_pending_entries(eval_task, 1, status=EvalEntryStatus.COMPLETED)
        [running] = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)
        from tfc.temporal.eval_tasks.activities import _requeue_entries_sync

        out = _requeue_entries_sync(str(eval_task.id), [str(done.id), str(running.id)])
        assert out == {"requeued": 1}
        done.refresh_from_db()
        running.refresh_from_db()
        assert done.status == EvalEntryStatus.COMPLETED
        assert running.status == EvalEntryStatus.PENDING


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestSetStatusActivitySync:
    def test_guarded_flip_pending_to_running_stamps_start_time(self, eval_task):
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        assert eval_task.status == EvalTaskStatus.PENDING
        out = _set_task_status_sync(
            str(eval_task.id),
            EvalTaskStatus.RUNNING,
            expected_status=EvalTaskStatus.PENDING,
        )

        assert out["changed"] is True
        assert out["status"] == EvalTaskStatus.RUNNING
        refreshed = EvalTask.objects.get(id=eval_task.id)
        assert refreshed.status == EvalTaskStatus.RUNNING
        assert refreshed.start_time is not None

    def test_guard_noop_when_status_mismatch(self, eval_task):
        eval_task.status = EvalTaskStatus.RUNNING
        eval_task.save(update_fields=["status"])
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        out = _set_task_status_sync(
            str(eval_task.id),
            EvalTaskStatus.RUNNING,
            expected_status=EvalTaskStatus.PENDING,
        )
        assert out["changed"] is False
        assert out["status"] == EvalTaskStatus.RUNNING

    def test_guard_does_not_clobber_paused(self, eval_task):
        # A pause landing between create and workflow start must survive: the
        # guarded update only touches a still-pending row.
        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save(update_fields=["status"])
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        out = _set_task_status_sync(
            str(eval_task.id),
            EvalTaskStatus.RUNNING,
            expected_status=EvalTaskStatus.PENDING,
        )
        assert out["changed"] is False
        assert EvalTask.objects.get(id=eval_task.id).status == EvalTaskStatus.PAUSED

    def test_unconditional_set_to_terminal_stamps_end_time(self, eval_task):
        eval_task.status = EvalTaskStatus.RUNNING
        eval_task.save(update_fields=["status"])
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        out = _set_task_status_sync(str(eval_task.id), EvalTaskStatus.FAILED)
        assert out["changed"] is True
        refreshed = EvalTask.objects.get(id=eval_task.id)
        assert refreshed.status == EvalTaskStatus.FAILED
        assert refreshed.end_time is not None


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestFinalizeActivitySync:
    def test_finalizes_when_drained(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 2, status=EvalEntryStatus.COMPLETED)
        from tfc.temporal.eval_tasks.activities import _finalize_task_sync

        out = _finalize_task_sync(str(eval_task.id))
        assert out["finalized"] is True
        assert EvalTask.objects.get(id=eval_task.id).status == EvalTaskStatus.COMPLETED

    def test_not_finalized_while_pending(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 1)  # still pending
        from tfc.temporal.eval_tasks.activities import _finalize_task_sync

        out = _finalize_task_sync(str(eval_task.id))
        assert out["finalized"] is False
        assert EvalTask.objects.get(id=eval_task.id).status != EvalTaskStatus.COMPLETED

    def test_missing_task_id_is_handled(self):
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        with pytest.raises(EvalTask.DoesNotExist):
            _get_task_state_sync(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_the_reap_activity_carries_the_workflows_evidence_through(monkeypatch):
    """The flag has to survive the whole hop: workflow input -> ``ReapInput``
    -> activity wrapper -> ``_reap_sync``. Dropped anywhere in that chain it
    reads as "unknown", and the ninety-minute floor comes back silently."""
    import tfc.temporal.eval_tasks.activities as activities
    from tfc.temporal.eval_tasks.types import ReapInput

    seen = {}

    class NoopHeartbeater:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def fake_sync_to_async(function, **_kwargs):
        async def invoke(*args, **kwargs):
            return function(*args, **kwargs)

        return invoke

    def capture(task_id, older_than_seconds, max_attempts, confirmed):
        seen.update(asked=older_than_seconds, confirmed=confirmed)
        return {"requeued": 0, "failed": 0, "older_than_seconds": older_than_seconds}

    monkeypatch.setattr(activities, "Heartbeater", NoopHeartbeater)
    monkeypatch.setattr(activities, "otel_sync_to_async", fake_sync_to_async)
    monkeypatch.setattr(activities, "_reap_sync", capture)

    await activities.reap_stale_running_activity(
        ReapInput(task_id="task-id", workflow_confirmed_stopped=True)
    )
    assert seen == {"asked": 600, "confirmed": True}

    await activities.reap_stale_running_activity(ReapInput(task_id="task-id"))
    assert seen == {"asked": 600, "confirmed": False}
