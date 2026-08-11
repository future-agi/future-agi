"""Tests for the sampling-threshold derivation — the cut-off that turns
``sampling_rate`` into an exact row count.

Covers the short-circuits (which must not touch ClickHouse), the exact counts
and the subset/superset nesting the reconciler depends on, and the agreement of
the naive and histogram strategies either side of ``NAIVE_MAX_K``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks import threshold as threshold_module
from tracer.services.eval_tasks.threshold import (
    HASH_SPACE,
    _bucket_and_offset,
    _eligible_query,
    _rows,
    derive_threshold,
    sampling_hash_sql,
)
from tracer.tests._ch_seed import seed_ch_spans


def _make_task(project, *, sampling_rate, run_type=RunType.HISTORICAL):
    return EvalTask.objects.create(
        project=project,
        name="threshold-task",
        filters={},
        sampling_rate=sampling_rate,
        spans_limit=1_000_000,
        run_type=run_type,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )


def _make_spans(project, n):
    trace = Trace.objects.create(project=project, name="threshold-trace")
    recent = datetime.now(UTC) - timedelta(minutes=1)
    spans = [
        ObservationSpan.objects.create(
            id=f"th-{i}-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name=f"span-{i}",
            observation_type="llm",
            start_time=recent,
        )
        for i in range(n)
    ]
    # created_at is auto_now_add; override the in-memory value the CH seed reads
    # so rows land safely inside the builder's default time window.
    for span in spans:
        span.created_at = recent
    seed_ch_spans(spans)
    return spans


def _selected_ids(task, threshold):
    """The ids the threshold predicate admits — what the resolver will select."""
    sql, params, id_col = _eligible_query(task)
    rows = _rows(
        f"SELECT {id_col} FROM ({sql}) "
        f"WHERE {sampling_hash_sql('salt', id_col)} <= %(threshold)s",
        {**params, "threshold": threshold},
    )
    return {str(row[0]) for row in rows}


@pytest.fixture
def no_ch_queries(monkeypatch):
    def _fail(sql, params):
        raise AssertionError(f"unexpected ClickHouse query: {sql}")

    monkeypatch.setattr(threshold_module, "_rows", _fail)


@pytest.fixture
def issued_sql(monkeypatch):
    """Record the SQL of every CH read the derivation issues."""
    seen: list[str] = []
    original = threshold_module._rows

    def _spy(sql, params):
        seen.append(sql)
        return original(sql, params)

    monkeypatch.setattr(threshold_module, "_rows", _spy)
    return seen


def test_hash_expression_is_the_63_bit_city_hash():
    assert (
        sampling_hash_sql("salt", "trace_id")
        == "bitShiftRight(cityHash64(%(salt)s, toString(trace_id)), 1)"
    )


def test_bucket_and_offset_walks_the_cumulative_counts():
    histogram = [(3, 4), (9, 5), (20, 2)]
    assert _bucket_and_offset(histogram, 4) == (3, 3)
    assert _bucket_and_offset(histogram, 7) == (9, 2)
    # More rows wanted than the histogram holds: clamp to the largest hash.
    assert _bucket_and_offset(histogram, 99) == (20, 1)


@pytest.mark.django_db
class TestShortCircuits:
    def test_rate_100_admits_everything(self, project, no_ch_queries):
        task = _make_task(project, sampling_rate=100.0)
        assert derive_threshold(task) == HASH_SPACE - 1

    def test_rate_0_admits_nothing(self, project, no_ch_queries):
        task = _make_task(project, sampling_rate=0.0)
        assert derive_threshold(task) == -1

    def test_continuous_task_is_analytic(self, project, no_ch_queries):
        task = _make_task(project, sampling_rate=50.0, run_type=RunType.CONTINUOUS)
        assert derive_threshold(task) == HASH_SPACE // 2


@pytest.mark.integration
@pytest.mark.django_db
class TestHistoricalExactCounts:
    def test_half_of_seventeen_selects_nine(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0)
        assert len(_selected_ids(task, derive_threshold(task))) == 9

    def test_one_percent_of_seventeen_selects_one(self, project):
        # ceil, not floor: a floored k would silently select nothing here.
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=1.0)
        assert len(_selected_ids(task, derive_threshold(task))) == 1

    def test_whole_number_k_is_not_rounded_up(self, project):
        _make_spans(project, 100)
        task = _make_task(project, sampling_rate=7.0)
        assert len(_selected_ids(task, derive_threshold(task))) == 7

    def test_lower_rates_nest_inside_higher_ones(self, project):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=30.0)
        small = _selected_ids(task, derive_threshold(task))
        task.sampling_rate = 50.0
        task.save()
        medium = _selected_ids(task, derive_threshold(task))
        task.sampling_rate = 80.0
        task.save()
        large = _selected_ids(task, derive_threshold(task))

        assert (len(small), len(medium), len(large)) == (6, 9, 14)
        assert small < medium < large

    def test_empty_population_admits_nothing(self, project):
        task = _make_task(project, sampling_rate=50.0)
        assert derive_threshold(task) == -1


@pytest.mark.integration
@pytest.mark.django_db
class TestStrategyEquivalence:
    """The strategy is chosen by k; both must return the same threshold."""

    def _derive(self, task, issued_sql, *, naive_max_k, monkeypatch):
        monkeypatch.setattr(threshold_module, "NAIVE_MAX_K", naive_max_k)
        issued_sql.clear()
        return derive_threshold(task)

    def test_naive_and_histogram_agree_either_side_of_the_cutoff(
        self, project, issued_sql, monkeypatch
    ):
        _make_spans(project, 17)
        task = _make_task(project, sampling_rate=50.0)  # k = 9

        naive = self._derive(task, issued_sql, naive_max_k=9, monkeypatch=monkeypatch)
        naive_sql = list(issued_sql)
        histogram = self._derive(
            task, issued_sql, naive_max_k=8, monkeypatch=monkeypatch
        )

        assert not any("GROUP BY bucket" in sql for sql in naive_sql)
        assert any("GROUP BY bucket" in sql for sql in issued_sql)
        assert naive == histogram
        assert len(_selected_ids(task, histogram)) == 9
