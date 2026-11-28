"""Session context for traces that exist only in ClickHouse (TH-11001).

Traces ingested through the collector have spans (and curated CH traces) but
no Postgres ``Trace`` row. The session context used to page traces through
Postgres only, so such a session reached the eval agent with no traces at
all while its span totals were non-zero.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import create_autospec

from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.tests.test_eval_native_cost_coverage import START, _span
from tracer.utils.eval import build_session_context

SESSION = SimpleNamespace(
    id="session-1",
    project_id="project-1",
    name="booking",
    bookmarked=False,
    created_at=START,
)


class _TraceQuery:
    def __init__(self, traces=()):
        self.traces = list(traces)

    def order_by(self, *_args):
        return self

    def __getitem__(self, key):
        return self.traces[key]

    def count(self):
        return len(self.traces)

    def values_list(self, *_args, **_kwargs):
        return [t.id for t in self.traces]


def _context(monkeypatch, *, session_trace_ids, spans, pg_traces=()):
    reader = create_autospec(CHSpanReader, instance=True)
    reader.__enter__.return_value = reader
    reader.session_trace_ids.return_value = session_trace_ids
    reader.list_by_session.return_value = spans
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    def scoped_traces(**kwargs):
        assert kwargs == {
            "project_id": "project-1",
            "id__in": session_trace_ids,
            "deleted": False,
        }
        return _TraceQuery(pg_traces)

    monkeypatch.setattr(Trace.objects, "filter", scoped_traces)
    context = build_session_context(SESSION)
    assert context is not None
    return context


def test_collector_only_session_lists_every_trace_in_order(monkeypatch):
    """Previously: ``traces == []`` and ``trace_count == 0`` although both
    traces' spans were read. Now each trace is listed from its spans."""
    request = _span(
        id="span-request",
        trace_id="trace-request",
        name="booking request",
        start_time=START,
    )
    tool_call = _span(
        id="span-tool-call",
        trace_id="trace-request",
        parent_span_id="span-request",
        name="create_reservation",
        start_time=START + timedelta(seconds=1),
    )
    reply = _span(
        id="span-reply",
        trace_id="trace-reply",
        name="booking reply",
        start_time=START + timedelta(minutes=5),
        status="ERROR",
    )
    context = _context(
        monkeypatch,
        # CH returns DISTINCT ids in no promised order.
        session_trace_ids=["trace-reply", "trace-request"],
        spans=[request, tool_call, reply],
    )

    assert context["trace_count"] == 2
    assert context["total_spans"] == 3
    traces = context["traces"]
    assert [t["id"] for t in traces] == ["trace-request", "trace-reply"]
    assert [t["name"] for t in traces] == ["booking request", "booking reply"]
    assert traces[0]["created_at"] == START.isoformat()
    assert [t["span_count"] for t in traces] == [2, 1]
    assert [s["id"] for s in traces[0]["spans"]] == ["span-request", "span-tool-call"]
    assert traces[0]["spans"][1]["parent_span_id"] == "span-request"
    assert [t["has_error"] for t in traces] == [False, True]


def test_postgres_rows_are_kept_and_merged_by_time(monkeypatch):
    """A trace with a Postgres row keeps that row's name and time; a
    collector-only trace of the same session is listed beside it, oldest
    first, even when ClickHouse times come back without a timezone."""
    pg_trace = SimpleNamespace(
        id="trace-pg",
        name="pg name",
        created_at=START + timedelta(minutes=3),
        error=None,
    )
    naive_start = datetime(2026, 10, 20, 12, 0, 0)  # START without tzinfo
    collector_root = _span(
        id="span-collector",
        trace_id="trace-collector",
        name="collector root",
        start_time=naive_start,
    )
    pg_span = _span(
        id="span-pg",
        trace_id="trace-pg",
        name="span name",
        start_time=START + timedelta(minutes=3),
    )
    context = _context(
        monkeypatch,
        session_trace_ids=["trace-pg", "trace-collector"],
        spans=[collector_root, pg_span],
        pg_traces=[pg_trace],
    )

    assert context["trace_count"] == 2
    assert [t["id"] for t in context["traces"]] == ["trace-collector", "trace-pg"]
    assert [t["name"] for t in context["traces"]] == ["collector root", "pg name"]
    assert context["traces"][1]["created_at"] == pg_trace.created_at.isoformat()


def test_span_derived_name_prefers_the_root_span(monkeypatch):
    child = _span(
        id="span-child",
        trace_id="trace-1",
        parent_span_id="span-root",
        name="child first",
        start_time=START,
    )
    root = _span(
        id="span-root",
        trace_id="trace-1",
        name="root",
        start_time=START + timedelta(seconds=2),
    )
    context = _context(monkeypatch, session_trace_ids=["trace-1"], spans=[child, root])

    (trace,) = context["traces"]
    assert trace["name"] == "root"
    assert trace["created_at"] == START.isoformat()


def test_all_postgres_session_is_unchanged(monkeypatch):
    traces = [
        SimpleNamespace(
            id=f"trace-{n}",
            name=f"trace {n}",
            created_at=START + timedelta(minutes=n),
            error=None,
        )
        for n in range(3)
    ]
    spans = [
        _span(id=f"span-{n}", trace_id=f"trace-{n}", start_time=t.created_at)
        for n, t in enumerate(traces)
    ]
    context = _context(
        monkeypatch,
        session_trace_ids=[t.id for t in traces],
        spans=spans,
        pg_traces=traces,
    )

    assert context["trace_count"] == 3
    assert [t["id"] for t in context["traces"]] == ["trace-0", "trace-1", "trace-2"]
    assert [t["name"] for t in context["traces"]] == ["trace 0", "trace 1", "trace 2"]
