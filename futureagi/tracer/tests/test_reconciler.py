"""Tests for the reconciler — the single idempotent engine that makes a task's
live entries match its desired state (incl. edge cases)."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.services.eval_tasks.entries import soft_delete_live
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.tests._ch_seed import seed_ch_spans


def _config(project, eval_template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.5},
        mapping={"input": "input"},
        filters={},
    )


def _task(
    project,
    *,
    evals=(),
    sampling_rate=100.0,
    run_type=RunType.HISTORICAL,
    spans_limit=1_000_000,
    filters=None,
):
    task = EvalTask.objects.create(
        project=project,
        name="rec-task",
        filters=filters or {},
        sampling_rate=sampling_rate,
        spans_limit=spans_limit,
        run_type=run_type,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )
    for cfg in evals:
        task.evals.add(cfg)
    return task


def _make_spans(project, n, *, observation_type="llm", prefix="s"):
    # Stamp a moment in the past so the seeded span's start_time is strictly
    # before the desired-row query's now() upper bound — otherwise a span
    # created in the same second as the reconcile (as these tests do) is
    # excluded by ``start_time < end_date`` and never materializes.
    seeded_at = timezone.now() - timedelta(minutes=1)
    spans = []
    for i in range(n):
        trace = Trace.objects.create(project=project, name=f"tr-{prefix}-{i}")
        span = ObservationSpan.objects.create(
            id=f"{prefix}-{i}-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name=f"sp-{prefix}-{i}",
            observation_type=observation_type,
        )
        ObservationSpan.objects.filter(id=span.id).update(created_at=seeded_at)
        span.refresh_from_db()
        spans.append(span)
    seed_ch_spans(spans)
    return spans


def _make_spans_at(project, n, created_at, *, prefix="t"):
    """Like ``_make_spans`` but stamps each span's ``created_at`` (the column the
    continuous forward floor filters on) before seeding CH."""
    spans = []
    for i in range(n):
        trace = Trace.objects.create(project=project, name=f"tr-{prefix}-{i}")
        span = ObservationSpan.objects.create(
            id=f"{prefix}-{i}-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name=f"sp-{prefix}-{i}",
            observation_type="llm",
        )
        ObservationSpan.objects.filter(id=span.id).update(created_at=created_at)
        span.refresh_from_db()
        spans.append(span)
    seed_ch_spans(spans)
    return spans


def _live(task, **f):
    return EvalLogger.objects.filter(eval_task_id=str(task.id), **f)


def _mark(task, status, **f):
    return _live(task, **f).update(status=status)


@pytest.mark.integration
@pytest.mark.django_db
class TestCreateAndIdempotency:
    def test_create_when_empty(self, project, custom_eval_config):
        _make_spans(project, 5)
        task = _task(project, evals=[custom_eval_config])
        result = reconcile(task)
        assert result.created == 5
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 5

    def test_idempotent(self, project, custom_eval_config):
        _make_spans(project, 5)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        result = reconcile(task)
        assert result.created == 0
        assert result.requeued == 0
        assert result.dropped == 0
        assert _live(task).count() == 5


@pytest.mark.integration
@pytest.mark.django_db
class TestEvalChanges:
    def test_add_eval_creates_new_pairs(
        self, project, eval_template, custom_eval_config
    ):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        task.evals.add(_config(project, eval_template, "eval-2"))
        result = reconcile(task)
        assert result.created == 4
        assert _live(task).count() == 8

    def test_remove_eval_drops_pending_keeps_completed(
        self, project, eval_template, custom_eval_config
    ):
        _make_spans(project, 4)
        e2 = _config(project, eval_template, "eval-2")
        task = _task(project, evals=[custom_eval_config, e2])
        reconcile(task)
        # Mark e2's entries completed, then remove e2.
        _mark(task, EvalEntryStatus.COMPLETED, custom_eval_config=e2)
        task.evals.remove(e2)
        reconcile(task)
        # e2 completed rows kept (paid); e2 had no pending left to drop.
        assert _live(task, custom_eval_config=e2).count() == 4
        assert _live(task, custom_eval_config=custom_eval_config).count() == 4

    def test_remove_eval_drops_its_pending(
        self, project, eval_template, custom_eval_config
    ):
        _make_spans(project, 4)
        e2 = _config(project, eval_template, "eval-2")
        task = _task(project, evals=[custom_eval_config, e2])
        reconcile(task)  # all pending
        task.evals.remove(e2)
        reconcile(task)
        assert _live(task, custom_eval_config=e2).count() == 0  # pending dropped
        assert _live(task, custom_eval_config=custom_eval_config).count() == 4

    def test_edit_config_requeues_completed(self, project, custom_eval_config):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        # Edit the eval -> hash changes -> completed entries are stale.
        custom_eval_config.config = {"threshold": 0.9}
        custom_eval_config.save()
        result = reconcile(task)
        assert result.requeued == 4
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 4
        assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 0

    def test_completed_with_matching_hash_left_untouched(
        self, project, custom_eval_config
    ):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        result = reconcile(task)  # no config change
        assert result.requeued == 0
        assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 4

    def test_empty_config_hash_treated_as_not_stale(self, project, custom_eval_config):
        # Transient-window guard: legacy completed rows not yet backfilled
        # (config_hash empty) must NOT be re-run, even though "" != current hash.
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        _live(task).update(config_hash=None)  # simulate pre-backfill legacy rows
        result = reconcile(task)
        assert result.requeued == 0
        assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 4

    def test_errored_entries_requeued(self, project, custom_eval_config):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.ERRORED)
        result = reconcile(task)
        assert result.requeued == 4
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 4

    def test_skipped_entries_requeued(self, project, custom_eval_config):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.SKIPPED)
        result = reconcile(task)
        assert result.requeued == 4
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 4


@pytest.mark.integration
@pytest.mark.django_db
class TestScopeChange:
    def test_scope_shrink_drops_pending_keeps_completed(
        self, project, custom_eval_config
    ):
        _make_spans(project, 40)
        task = _task(project, evals=[custom_eval_config], sampling_rate=100.0)
        reconcile(task)  # 40 pending
        # Mark 10 completed, then shrink scope.
        ids = list(_live(task).values_list("id", flat=True)[:10])
        EvalLogger.objects.filter(id__in=ids).update(status=EvalEntryStatus.COMPLETED)
        task.sampling_rate = 25.0
        task.save()
        reconcile(task)
        # No completed entry was ever dropped (paid data kept).
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(task.id),
                status=EvalEntryStatus.COMPLETED,
                deleted=True,
            ).count()
            == 0
        )
        # Some out-of-scope pending were dropped.
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(task.id), status=EvalEntryStatus.PENDING, deleted=True
            ).count()
            > 0
        )

    def test_zero_rows_creates_nothing(self, project, custom_eval_config):
        task = _task(project, evals=[custom_eval_config])  # no spans seeded
        result = reconcile(task)
        assert result.created == 0
        assert _live(task).count() == 0


@pytest.mark.integration
@pytest.mark.django_db
class TestLifecycleFlows:
    """Lifecycle flows the reconciler is responsible for. (The option table / which
    buttons, immutable validation, continuous->historical window requirement,
    continuous_cursor, and the forward tail are PR 9 / PR 6 concerns.)"""

    def test_delete_and_rerun_recreates_fresh(self, project, custom_eval_config):
        # Delete & rerun = wipe live entries, then reconcile.
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        soft_delete_live(task)
        result = reconcile(task)
        assert result.created == 4
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 4
        # The wiped completed rows are gone (soft-deleted), replaced by fresh pending.
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(task.id),
                status=EvalEntryStatus.COMPLETED,
                deleted=True,
            ).count()
            == 4
        )

    def test_both_evals_and_rows_change_handled_in_one_pass(
        self, project, eval_template, custom_eval_config
    ):
        # Case 3: the reconcile engine handles both axes at once.
        _make_spans(project, 40)
        task = _task(project, evals=[custom_eval_config], sampling_rate=100.0)
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        e2 = _config(project, eval_template, "eval-2")
        task.evals.add(e2)  # evals change
        task.sampling_rate = 50.0  # rows change
        task.save()
        reconcile(task)
        # New eval got entries for the in-scope (rate-50) rows.
        in_scope_e2 = _live(
            task, custom_eval_config=e2, status=EvalEntryStatus.PENDING
        ).count()
        assert 0 < in_scope_e2 < 40
        # No completed result was ever dropped (paid data kept across both axes).
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(task.id),
                custom_eval_config=custom_eval_config,
                status=EvalEntryStatus.COMPLETED,
                deleted=True,
            ).count()
            == 0
        )

    def test_limit_shrink_drops_out_of_scope_pending(self, project, custom_eval_config):
        # Rows change via row limit.
        _make_spans(project, 20)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        task.spans_limit = 5
        task.save()
        reconcile(task)
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 5

    def test_filter_change_drops_out_of_scope_pending(
        self, project, custom_eval_config
    ):
        # Rows change via filters.
        _make_spans(project, 5, observation_type="llm", prefix="llm")
        _make_spans(project, 5, observation_type="tool", prefix="tool")
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        assert _live(task).count() == 10
        task.filters = {"observation_type": ["llm"]}
        task.save()
        reconcile(task)
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 5

    def test_scope_regrow_reuses_completed_without_duplication(
        self, project, custom_eval_config
    ):
        # Out-of-scope completed are kept and reused on regrow.
        _make_spans(project, 40)
        task = _task(project, evals=[custom_eval_config], sampling_rate=100.0)
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        task.sampling_rate = 25.0  # shrink — completed kept (never dropped)
        task.save()
        reconcile(task)
        task.sampling_rate = 100.0  # regrow — kept completed back in scope
        task.save()
        result = reconcile(task)
        # Reused, not recreated: no new rows, no duplicates, all still completed.
        assert result.created == 0
        assert _live(task).count() == 40
        assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 40

    def test_continuous_task_materializes_and_is_idempotent(
        self, project, custom_eval_config
    ):
        # Continuous reconcile materializes the matching slice (no limit). The
        # spans land after the task's start, so the forward floor keeps them in.
        start = timezone.now() - timedelta(hours=1)
        _make_spans_at(project, 5, start + timedelta(minutes=5))
        task = _task(project, evals=[custom_eval_config], run_type=RunType.CONTINUOUS)
        EvalTask.objects.filter(id=task.id).update(start_time=start)
        task.refresh_from_db()
        reconcile(task)
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 5
        result = reconcile(task)
        assert result.created == 0  # idempotent

    def test_continuous_excludes_rows_created_before_start(
        self, project, custom_eval_config
    ):
        # A continuous task starts from its own start point forward: rows that
        # pre-date it are never backfilled, only rows that arrive after.
        start = timezone.now() - timedelta(hours=1)
        old = _make_spans_at(project, 2, start - timedelta(hours=2), prefix="old")
        new = _make_spans_at(project, 3, start + timedelta(minutes=5), prefix="new")
        task = _task(project, evals=[custom_eval_config], run_type=RunType.CONTINUOUS)
        EvalTask.objects.filter(id=task.id).update(start_time=start)
        task.refresh_from_db()

        reconcile(task)

        materialized = set(_live(task).values_list("observation_span_id", flat=True))
        assert {s.id for s in new} <= materialized
        assert not ({s.id for s in old} & materialized)

    def test_continuous_reconcile_advances_cursor(self, project, custom_eval_config):
        # A task older than the overlap parks its watermark just behind now() so
        # the next pass scans only the new tail instead of the whole history.
        start = timezone.now() - timedelta(hours=1)
        task = _task(project, evals=[custom_eval_config], run_type=RunType.CONTINUOUS)
        EvalTask.objects.filter(id=task.id).update(start_time=start)
        task.refresh_from_db()
        assert task.continuous_cursor is None

        reconcile(task)

        task.refresh_from_db()
        assert task.continuous_cursor is not None
        # Advanced forward (well past the hour-old start), but still behind now.
        assert start < task.continuous_cursor < timezone.now()

    def test_continuous_cursor_never_regresses_before_start(
        self, project, custom_eval_config
    ):
        # For a task younger than the overlap, now()-overlap is before its start;
        # the cursor must clamp to the start floor, never pulling pre-start
        # history back into scope on the next pass.
        start = timezone.now()
        task = _task(project, evals=[custom_eval_config], run_type=RunType.CONTINUOUS)
        EvalTask.objects.filter(id=task.id).update(start_time=start)
        task.refresh_from_db()

        reconcile(task)

        task.refresh_from_db()
        assert task.continuous_cursor is not None
        assert task.continuous_cursor >= start

    def test_historical_to_continuous_keeps_entries(self, project, custom_eval_config):
        # Switching to continuous keeps existing entries (no wipe).
        _make_spans(project, 5)
        task = _task(project, evals=[custom_eval_config])
        reconcile(task)
        _mark(task, EvalEntryStatus.COMPLETED)
        task.run_type = RunType.CONTINUOUS
        task.save()
        reconcile(task)
        assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 5
        assert _live(task).count() == 5  # no duplicates
