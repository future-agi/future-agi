"""Tests for the resolver's newest-first drain order. Exercises iter_desired_rows
against CH-seeded spans with a known event-time spread: the emitted sequence is
ordered by event time descending for every row_type, ``spans_limit`` keeps the
most recent rows rather than the smallest ids, and rows sharing a timestamp fall
back to a stable id tiebreak."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.tests._ch_seed import seed_ch_spans

# Event times sit a few minutes back so every row lands inside the builders'
# default window; ids are assigned in oldest-first order so ordering by id is the
# exact reverse of ordering by event time.
_BASE = datetime.now(UTC) - timedelta(minutes=30)


def _ids(task, **kwargs):
    return [row_id for batch in iter_desired_rows(task, **kwargs) for row_id in batch]


def _make_task(project, *, row_type=RowType.SPANS, spans_limit=1_000_000):
    return EvalTask.objects.create(
        project=project,
        name="ordering-task",
        filters={},
        sampling_rate=100.0,
        spans_limit=spans_limit,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=row_type,
    )


def _seed_spans(project, minute_offsets, *, observation_type="llm", prefix="ord"):
    """Seed one root span per offset (minutes after ``_BASE``), oldest first.

    Returns the spans in the order given, so ``reversed(...)`` is the expected
    newest-first drain.
    """
    spans = []
    for i, offset in enumerate(minute_offsets):
        trace = Trace.objects.create(project=project, name=f"trace-{prefix}-{i}")
        spans.append(
            ObservationSpan.objects.create(
                id=f"{prefix}-{i:02d}",
                project=project,
                trace=trace,
                name=f"span-{prefix}-{i}",
                observation_type=observation_type,
                start_time=_BASE + timedelta(minutes=offset),
            )
        )
    for s in spans:
        s.created_at = datetime.now(UTC) - timedelta(minutes=1)
    seed_ch_spans(spans)
    return spans


@pytest.mark.integration
@pytest.mark.django_db
class TestNewestFirstOrdering:
    def test_no_limit_yields_newest_first_and_repeats_identically(self, project):
        spans = _seed_spans(project, range(8))
        task = _make_task(project)
        newest_first = [s.id for s in reversed(spans)]
        assert _ids(task) == newest_first
        assert _ids(task) == newest_first
        assert _ids(task) == newest_first

    def test_limit_keeps_the_newest_rows_not_the_smallest_ids(self, project):
        spans = _seed_spans(project, range(8))
        task = _make_task(project, spans_limit=3)
        assert _ids(task) == [s.id for s in reversed(spans)][:3]

    def test_identical_start_times_order_stably_by_id(self, project):
        spans = _seed_spans(project, [4, 4, 4])
        task = _make_task(project)
        by_id_desc = [s.id for s in reversed(spans)]
        assert _ids(task) == by_id_desc
        assert _ids(task) == by_id_desc
        assert _ids(task) == by_id_desc


@pytest.mark.integration
@pytest.mark.django_db
class TestOrderingPerRowType:
    def test_spans_order_by_start_time(self, project):
        spans = _seed_spans(project, range(5), prefix="sp")
        task = _make_task(project, row_type=RowType.SPANS)
        assert _ids(task) == [s.id for s in reversed(spans)]

    def test_traces_order_by_root_span_start_time(self, project):
        spans = _seed_spans(project, range(5), prefix="tr")
        task = _make_task(project, row_type=RowType.TRACES)
        assert _ids(task) == [str(s.trace_id) for s in reversed(spans)]

    def test_voice_calls_order_by_start_time(self, project):
        calls = _seed_spans(
            project, range(4), observation_type="conversation", prefix="vc"
        )
        task = _make_task(project, row_type=RowType.VOICE_CALLS)
        assert _ids(task) == [c.id for c in reversed(calls)]

    def test_sessions_order_by_session_start(self, project):
        sessions = []
        for i in range(4):
            session = TraceSession.objects.create(project=project, name=f"sess-{i}")
            trace = Trace.objects.create(
                project=project, name=f"trace-sess-{i}", session=session
            )
            span = ObservationSpan.objects.create(
                id=f"sess-{i:02d}-{uuid.uuid4().hex[:8]}",
                project=project,
                trace=trace,
                name=f"span-sess-{i}",
                observation_type="llm",
                start_time=_BASE + timedelta(minutes=i),
            )
            span.created_at = datetime.now(UTC) - timedelta(minutes=1)
            seed_ch_spans([span])
            sessions.append(session)
        task = _make_task(project, row_type=RowType.SESSIONS)
        assert _ids(task) == [str(s.id) for s in reversed(sessions)]
