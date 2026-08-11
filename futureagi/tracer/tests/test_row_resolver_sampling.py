"""Tests for the resolver honouring a task's stored sampling threshold.

A task with a threshold selects by comparing each row's hash to it, which is
what makes ``sampling_rate`` land on an exact count. A task without one — every
task created before the threshold shipped — keeps the old per-row modulo test so
its row set cannot shift mid-run.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.selectors.eval_tasks.row_resolver import (
    _build_sample_query,
    iter_desired_rows,
)
from tracer.selectors.eval_tasks.sampling import HASH_SPACE
from tracer.services.eval_tasks.threshold import derive_threshold
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _make_task(project, *, sampling_rate=50.0, threshold=None):
    return EvalTask.objects.create(
        project=project,
        name="sampling-task",
        filters={},
        sampling_rate=sampling_rate,
        spans_limit=1_000_000,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
        sample_threshold=threshold,
    )


def _make_spans(project, n):
    trace = Trace.objects.create(project=project, name="sampling-trace")
    recent = datetime.now(UTC) - timedelta(minutes=1)
    spans = [
        ObservationSpan.objects.create(
            id=f"samp-{i}-{uuid.uuid4().hex[:8]}",
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


def _ids(task):
    return [row_id for batch in iter_desired_rows(task) for row_id in batch]


def _sql(**overrides):
    params = {
        "project_id": "p",
        "row_type": RowType.SPANS,
        "salt": "s",
        "sampling_rate": 50.0,
        "filters": {},
        "limit": None,
    }
    params.update(overrides)
    return _build_sample_query(**params)[0]


class TestPredicateSelection:
    def test_threshold_task_compares_against_the_stored_cut_off(self):
        sql = _sql(threshold=123)
        assert "<= %(threshold)s" in sql
        assert "modulo(" not in sql

    def test_task_without_a_threshold_keeps_the_modulo_predicate(self):
        sql = _sql(threshold=None)
        assert "modulo(cityHash64(%(salt)s, toString(id)), 100) < %(rate)s" in sql
        assert "%(threshold)s" not in sql

    def test_threshold_of_zero_is_used_not_treated_as_absent(self):
        # 0 is a real cut-off (it admits the single hash 0). Guarding on
        # truthiness rather than `is not None` would silently fall back.
        sql = _sql(threshold=0)
        assert "<= %(threshold)s" in sql
        assert "modulo(" not in sql

    def test_only_the_predicate_differs_between_the_two_modes(self):
        with_threshold = _sql(threshold=123)
        without = _sql(threshold=None)
        assert with_threshold.split("WHERE")[0] == without.split("WHERE")[0]
        tail = "ORDER BY start_time DESC, id DESC"
        assert with_threshold.rstrip().endswith(tail)
        assert without.rstrip().endswith(tail)


class TestSelectionAgainstRealRows:
    def test_derived_threshold_selects_exactly_the_target_count(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0)
        task.sample_threshold = derive_threshold(task)
        task.save(update_fields=["sample_threshold"])

        assert len(_ids(task)) == 9  # ceil(0.5 * 17)

    def test_rate_100_selects_every_row(self, project):
        spans = _make_spans(project, 8)
        task = _make_task(project, sampling_rate=100.0, threshold=HASH_SPACE - 1)

        assert set(_ids(task)) == {s.id for s in spans}

    def test_negative_threshold_selects_nothing(self, project):
        _make_spans(project, 8)
        task = _make_task(project, sampling_rate=0.0, threshold=-1)

        assert _ids(task) == []

    def test_selection_is_stable_across_repeated_calls(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0)
        task.sample_threshold = derive_threshold(task)
        task.save(update_fields=["sample_threshold"])

        assert _ids(task) == _ids(task) == _ids(task)

    def test_lowering_the_threshold_yields_a_strict_subset(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=80.0)
        task.sample_threshold = derive_threshold(task)
        task.save(update_fields=["sample_threshold"])
        wide = set(_ids(task))

        task.sampling_rate = 30.0
        task.sample_threshold = derive_threshold(task)
        task.save(update_fields=["sampling_rate", "sample_threshold"])
        narrow = set(_ids(task))

        assert narrow < wide
