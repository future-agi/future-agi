"""Tests for when a task's sampling threshold is re-derived.

The threshold is pinned on the task, so re-deriving it moves the row set. It
must happen exactly when the eligible population changes — a rate or filter
edit — and never on edits that leave the population alone, above all a
historical/continuous flip, which would otherwise shift the row set underneath
results already paid for.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.services.eval_tasks import threshold as threshold_module
from tracer.services.eval_tasks.edit_options import scope_changed
from tracer.services.eval_tasks.threshold import refresh_sample_threshold
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _make_task(project, **overrides):
    fields = {
        "project": project,
        "name": "lifecycle-task",
        "filters": {},
        "sampling_rate": 50.0,
        "spans_limit": 1_000_000,
        "run_type": RunType.HISTORICAL,
        "status": EvalTaskStatus.PENDING,
        "row_type": RowType.SPANS,
    }
    fields.update(overrides)
    return EvalTask.objects.create(**fields)


def _make_spans(project, n):
    trace = Trace.objects.create(project=project, name="lifecycle-trace")
    recent = datetime.now(UTC) - timedelta(minutes=1)
    spans = [
        ObservationSpan.objects.create(
            id=f"life-{i}-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name=f"span-{i}",
            observation_type="llm",
            start_time=recent,
        )
        for i in range(n)
    ]
    for span in spans:
        span.created_at = recent
    seed_ch_spans(spans)
    return spans


class TestScopeChangedPredicate:
    """Which edits invalidate the threshold. The negative cases carry the
    weight — a false positive here silently re-samples a running task."""

    def test_sampling_rate_change_invalidates(self, project):
        task = _make_task(project, sampling_rate=50.0)
        assert scope_changed({"sampling_rate": 30.0}, task) is True

    def test_filters_change_invalidates(self, project):
        task = _make_task(project, filters={})
        assert scope_changed({"filters": {"observation_type": ["llm"]}}, task) is True

    def test_spans_limit_change_does_not_invalidate(self, project):
        # A cost cap applied after sampling — it never moves the cut-off.
        task = _make_task(project, spans_limit=1000)
        assert scope_changed({"spans_limit": 50}, task) is False

    def test_run_type_flip_does_not_invalidate(self, project):
        task = _make_task(project, run_type=RunType.HISTORICAL)
        assert scope_changed({"run_type": RunType.CONTINUOUS}, task) is False

    def test_rename_does_not_invalidate(self, project):
        task = _make_task(project, name="before")
        assert scope_changed({"name": "after"}, task) is False

    def test_resubmitting_an_unchanged_value_does_not_invalidate(self, project):
        task = _make_task(project, sampling_rate=50.0)
        assert scope_changed({"sampling_rate": 50.0}, task) is False


class TestRefreshPersistsAndFailsOpen:
    def test_refresh_stores_the_threshold_on_the_row(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0)

        stored = refresh_sample_threshold(task)

        task.refresh_from_db()
        assert stored is not None
        assert task.sample_threshold == stored

    def test_clickhouse_failure_leaves_the_task_usable(self, project, monkeypatch):
        task = _make_task(project, sampling_rate=50.0, sample_threshold=None)

        def _boom(_task):
            raise RuntimeError("clickhouse unreachable")

        monkeypatch.setattr(threshold_module, "derive_threshold", _boom)

        assert refresh_sample_threshold(task) is None
        task.refresh_from_db()
        assert task.sample_threshold is None


class TestRunTypeFlipDoesNotResample:
    """The load-bearing invariant: flipping run_type must not move the sample.

    A continuous task floors its scan on arrival time, so while it is continuous
    it legitimately sees only rows that landed after activation — that narrowing
    is the run type, not a new sample. What must hold is that the cut-off never
    moves, which the round trip proves: flip away and back and the original row
    set returns intact.
    """

    def test_threshold_survives_the_flip(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0, run_type=RunType.HISTORICAL)
        refresh_sample_threshold(task)
        threshold_before = task.sample_threshold

        assert scope_changed({"run_type": RunType.CONTINUOUS}, task) is False
        task.run_type = RunType.CONTINUOUS
        task.save(update_fields=["run_type"])
        task.refresh_from_db()

        assert task.sample_threshold == threshold_before

    def test_round_trip_restores_the_original_row_set(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0, run_type=RunType.HISTORICAL)
        refresh_sample_threshold(task)
        rows_before = {r for batch in iter_desired_rows(task) for r in batch}
        assert rows_before

        for run_type in (RunType.CONTINUOUS, RunType.HISTORICAL):
            task.run_type = run_type
            task.save(update_fields=["run_type"])
            task.refresh_from_db()

        rows_after = {r for batch in iter_desired_rows(task) for r in batch}
        assert rows_after == rows_before
