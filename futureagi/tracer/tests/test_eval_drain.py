"""Tests for the drain queue mechanics: claim (SKIP LOCKED), mark_terminal,
the stale-running reaper (+ poison cap), and the count-by-status reads."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.selectors.eval_tasks.progress import count_by_status, has_undrained_work
from tracer.services.eval_tasks.entries import claim_pending_batch, mark_terminal
from tracer.services.eval_tasks.reaper import reap_stale_running


def _task(project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="drain-task",
        filters={},
        sampling_rate=100.0,
        spans_limit=1_000_000,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )
    task.evals.add(custom_eval_config)
    return task


def _entries(task, config, n, *, status=EvalEntryStatus.PENDING):
    out = []
    for i in range(n):
        trace = Trace.objects.create(project=task.project, name=f"t-{i}")
        span = ObservationSpan.objects.create(
            id=f"s-{i}-{uuid.uuid4().hex[:8]}",
            project=task.project,
            trace=trace,
            name="s",
            observation_type="llm",
        )
        out.append(
            EvalLogger.objects.create(
                target_type=EvalTargetType.SPAN,
                observation_span=span,
                trace=trace,
                custom_eval_config=config,
                eval_task_id=str(task.id),
                status=status,
            )
        )
    return out


def _live(task, **f):
    return EvalLogger.objects.filter(eval_task_id=str(task.id), **f)


@pytest.mark.integration
@pytest.mark.django_db
class TestClaimPendingBatch:
    def test_claims_up_to_n_and_marks_running(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        _entries(task, custom_eval_config, 5)
        claimed = claim_pending_batch(task, 3)
        assert len(claimed) == 3
        assert all(e.status == EvalEntryStatus.RUNNING for e in claimed)
        assert _live(task, status=EvalEntryStatus.RUNNING).count() == 3
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 2

    def test_does_not_reclaim_running(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        _entries(task, custom_eval_config, 5)
        claim_pending_batch(task, 3)
        second = claim_pending_batch(task, 3)  # only 2 pending remain
        assert len(second) == 2

    def test_empty_when_no_pending(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        _entries(task, custom_eval_config, 2, status=EvalEntryStatus.COMPLETED)
        assert claim_pending_batch(task, 5) == []


@pytest.mark.integration
@pytest.mark.django_db
class TestMarkTerminal:
    def test_completed_sets_status_and_hash(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        [entry] = _entries(task, custom_eval_config, 1, status=EvalEntryStatus.RUNNING)
        assert (
            mark_terminal(entry, EvalEntryStatus.COMPLETED, config_hash="a" * 64)
            is True
        )
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.COMPLETED
        assert entry.config_hash == "a" * 64

    def test_errored_sets_error_fields(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        [entry] = _entries(task, custom_eval_config, 1, status=EvalEntryStatus.RUNNING)
        mark_terminal(
            entry,
            EvalEntryStatus.ERRORED,
            config_hash="b" * 64,
            error=True,
            error_message="boom",
        )
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error is True
        assert entry.error_message == "boom"

    def test_skipped_sets_skipped_reason(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        [entry] = _entries(task, custom_eval_config, 1, status=EvalEntryStatus.RUNNING)
        mark_terminal(
            entry,
            EvalEntryStatus.SKIPPED,
            config_hash="c" * 64,
            error=False,
            skipped_reason="missing_required_attribute: input",
        )
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.SKIPPED
        assert entry.skipped_reason == "missing_required_attribute: input"
        assert entry.error is False

    def test_noop_on_deleted_entry(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        [entry] = _entries(task, custom_eval_config, 1)
        entry.delete()  # soft-delete (Delete & rerun landed mid-flight)
        assert (
            mark_terminal(entry, EvalEntryStatus.COMPLETED, config_hash="d" * 64)
            is False
        )
        # Still deleted, not resurrected.
        assert _live(task).count() == 0

    def test_noop_on_non_running_entry(self, project, custom_eval_config):
        # Fence: a stale worker's terminal write must not land after the reaper
        # requeued the entry (RUNNING is the only writable state).
        task = _task(project, custom_eval_config)
        [entry] = _entries(task, custom_eval_config, 1, status=EvalEntryStatus.PENDING)
        assert (
            mark_terminal(entry, EvalEntryStatus.COMPLETED, config_hash="e" * 64)
            is False
        )
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.PENDING  # untouched


@pytest.mark.integration
@pytest.mark.django_db
class TestReaper:
    def _make_running(self, task, config, n, *, age_seconds, attempts=0):
        entries = _entries(task, config, n, status=EvalEntryStatus.RUNNING)
        old = timezone.now() - timedelta(seconds=age_seconds)
        EvalLogger.objects.filter(id__in=[e.id for e in entries]).update(
            updated_at=old, attempts=attempts
        )
        return entries

    def test_stale_running_reset_to_pending_and_attempts_incremented(
        self, project, custom_eval_config
    ):
        task = _task(project, custom_eval_config)
        self._make_running(task, custom_eval_config, 3, age_seconds=3600)
        requeued, failed = reap_stale_running(
            task, older_than_seconds=600, max_attempts=3
        )
        assert (requeued, failed) == (3, 0)
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 3
        assert all(e.attempts == 1 for e in _live(task))

    def test_fresh_running_untouched(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        self._make_running(task, custom_eval_config, 2, age_seconds=10)
        requeued, failed = reap_stale_running(
            task, older_than_seconds=600, max_attempts=3
        )
        assert (requeued, failed) == (0, 0)
        assert _live(task, status=EvalEntryStatus.RUNNING).count() == 2

    def test_poison_item_failed_at_cap(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        self._make_running(task, custom_eval_config, 1, age_seconds=3600, attempts=3)
        requeued, failed = reap_stale_running(
            task, older_than_seconds=600, max_attempts=3
        )
        assert (requeued, failed) == (0, 1)
        entry = _live(task).get()
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error is True


@pytest.mark.integration
@pytest.mark.django_db
class TestProgressReads:
    def test_count_by_status(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        _entries(task, custom_eval_config, 3, status=EvalEntryStatus.PENDING)
        _entries(task, custom_eval_config, 2, status=EvalEntryStatus.COMPLETED)
        counts = count_by_status(task)
        assert counts.get("pending") == 3
        assert counts.get("completed") == 2

    def test_has_undrained_work(self, project, custom_eval_config):
        task = _task(project, custom_eval_config)
        _entries(task, custom_eval_config, 1, status=EvalEntryStatus.COMPLETED)
        assert has_undrained_work(task) is False
        _entries(task, custom_eval_config, 1, status=EvalEntryStatus.PENDING)
        assert has_undrained_work(task) is True
