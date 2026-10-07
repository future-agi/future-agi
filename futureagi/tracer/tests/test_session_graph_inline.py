"""An affordable Sessions latency chart is computed inline, not queued.

Every Sessions latency chart is an exact snapshot, and in the US deployment
the exact worker has one slot for every tenant: a small tenant's 0.3-1.6 s
chart waited behind the largest tenant's 30-day read. Before the job is
scheduled, ``fetch_session_graph_ch`` now sends one ``EXPLAIN ESTIMATE`` of
the lean statement's root read (project, window, ``is_deleted = 0``,
``parent_span_id = ''``, a session) with a small server cap. At or below
``SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS`` (0 disables) the SAME statement
runs inline on the interactive wall with the interactive settings (one
thread) and the chart is returned complete, not cached. Otherwise - a larger
or unknown estimate, a shape that is not the lean one, a lane that cannot
carry per-query settings, or an inline read stopped at the wall - the
background path is unchanged.

Inline is tried only on a true cache miss (a hit is served, a running refresh
is polled, an explicit refresh of a hit goes to the worker), with at most half
of what is left of the wall, and a scope whose inline read failed goes
straight to the worker for a backoff: the browser gives up on a request after
30 s and stops polling, so the fallback must answer inside that.

The live equality of the inline and background numbers against an oracle is
in ``test_latency_mean_parity_ch25`` (``session/inline/none``).
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException
from django.conf import settings
from django.test import override_settings

PROJECT_ID = "7c3a2b11-0000-4000-8000-00000000e001"
ORGANIZATION_ID = "7c3a2b11-0000-4000-8000-00000000e0a1"
LO = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
HI = LO + timedelta(hours=6)
NAMESPACE = "observe-session-system-graph"
ESTIMATE_COLUMNS = ["database", "table", "parts", "rows", "marks"]

WINDOW = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [LO.isoformat(), HI.isoformat()],
    },
}


def _system(column, filter_type, op, value):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
        },
    }


LEAN_SHAPES = {
    "window": [WINDOW],
    "duration": [WINDOW, _system("duration", "number", "greater_than", 5)],
    "session_id": [
        WINDOW,
        _system(
            "session_id", "text", "not_in", ["11111111-1111-4111-8111-111111111111"]
        ),
    ],
}
NOT_LEAN_SHAPES = {
    "model": [WINDOW, _system("model", "text", "equals", "gpt-x")],
    "attribute": [
        WINDOW,
        {
            "column_id": "customer_tier",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gold",
            },
        },
    ],
    "first_message": [WINDOW, _system("first_message", "text", "contains", "hi")],
}

GRAPH_ROWS = [
    {"time_bucket": LO + timedelta(hours=1), "value": 812.25, "primary_traffic": 3},
    {"time_bucket": LO + timedelta(hours=4), "value": 40.5, "primary_traffic": 1},
]


def _estimate(rows):
    def respond(call):
        del call
        return SimpleNamespace(
            data=[
                {
                    "database": "db",
                    "table": "spans",
                    "parts": 1,
                    "rows": rows,
                    "marks": 1,
                }
            ],
            columns=list(ESTIMATE_COLUMNS),
        )

    return respond


def _graph(call):
    del call
    return SimpleNamespace(
        data=[dict(row) for row in GRAPH_ROWS],
        columns=["time_bucket", "value", "primary_traffic"],
    )


class _Analytics:
    """Records each statement with the timeout, settings and server cap."""

    def __init__(self, *, estimate=None, graph=_graph, per_query_settings=True):
        self.calls = []
        self._estimate = estimate or _estimate(1_000)
        self._graph = graph
        self.supports_per_query_read_settings = per_query_settings

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        call = SimpleNamespace(
            query=query,
            params=dict(params or {}),
            timeout_ms=timeout_ms,
            settings=dict(settings or {}),
            cap=server_execution_cap_ms,
        )
        self.calls.append(call)
        if "EXPLAIN ESTIMATE" in query:
            return self._estimate(call)
        return self._graph(call)

    def estimates(self):
        return [call for call in self.calls if "EXPLAIN ESTIMATE" in call.query]

    def graphs(self):
        return [call for call in self.calls if "EXPLAIN ESTIMATE" not in call.query]


class _Scheduled(list):
    """Scheduling calls; cache-only probes are kept apart in ``probes``.

    ``cached`` is what the cache holds for the identity (``None`` = a true
    miss); a probe serves it as the real cache would, and never schedules.
    """

    def __init__(self):
        super().__init__()
        self.probes = []
        self.cached = None


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def scheduled(monkeypatch):
    from tracer.services.clickhouse import session_graph

    calls = _Scheduled()

    def read_or_schedule(namespace, identity, **kwargs):
        call = SimpleNamespace(namespace=namespace, identity=identity, **kwargs)
        if kwargs.get("schedule_on_miss", True) is False:
            calls.probes.append(call)
            if calls.cached is not None:
                return dict(calls.cached)
            return {
                **kwargs["pending_payload"],
                "query_refreshing": False,
                "query_refresh_failed": False,
            }
        calls.append(call)
        if calls.cached is not None:
            return {**calls.cached, "query_refreshing": True}
        return {**kwargs["pending_payload"], "query_refreshing": True}

    monkeypatch.setattr(
        session_graph, "read_or_schedule_exact_snapshot", read_or_schedule
    )
    return calls


def _fetch(analytics, filters, metric_id="latency", **kwargs):
    from tracer.services.clickhouse.session_graph import fetch_session_graph_ch

    return fetch_session_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="hour",
        req_data_config={"type": "SYSTEM_METRIC", "id": metric_id},
        organization_id=ORGANIZATION_ID,
        **kwargs,
    )


def _points(payload):
    points = {}
    for point in payload["data"]:
        stamp = datetime.fromisoformat(str(point["timestamp"]))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        points[stamp.astimezone(UTC)] = point
    return points


@pytest.mark.unit
@pytest.mark.parametrize("shape", LEAN_SHAPES)
def test_an_affordable_latency_chart_is_computed_inline_not_scheduled(scheduled, shape):
    analytics = _Analytics()

    payload = _fetch(analytics, LEAN_SHAPES[shape])

    assert scheduled == []
    assert payload["query_status"] == "complete"
    assert payload["query_complete"] is True
    assert payload["metric_statistic"] == "mean"
    assert "query_refreshing" not in payload and "query_cached" not in payload
    points = _points(payload)
    assert points[LO + timedelta(hours=1)]["value"] == 812.25
    assert points[LO + timedelta(hours=1)]["primary_traffic"] == 3
    assert points[LO]["value"] == 0.0
    # One estimate, then the one statement.
    (estimate,) = analytics.estimates()
    (graph,) = analytics.graphs()
    assert analytics.calls == [estimate, graph]
    assert "AS exact_sessions" in graph.query


@pytest.mark.unit
def test_the_estimate_costs_the_lean_root_read_with_a_small_server_cap(scheduled):
    analytics = _Analytics()

    _fetch(analytics, [WINDOW])

    (estimate,) = analytics.estimates()
    sql = " ".join(estimate.query.split())
    assert sql.startswith("EXPLAIN ESTIMATE SELECT trace_session_id FROM spans")
    for predicate in (
        "project_id = toUUID(%(project_id)s)",
        "is_deleted = 0",
        "parent_span_id = ''",
        "start_time >= fromUnixTimestamp64Micro(%(start_date_us)s)",
        "start_time < fromUnixTimestamp64Micro(%(end_date_us)s)",
        "isNotNull(trace_session_id)",
    ):
        assert predicate in sql, predicate
    assert estimate.params == {
        "project_id": PROJECT_ID,
        "start_date_us": int(LO.timestamp() * 1_000_000),
        "end_date_us": int(HI.timestamp() * 1_000_000),
    }
    assert 0 < estimate.cap <= 1_500


@pytest.mark.unit
def test_the_inline_statement_runs_on_the_interactive_wall_and_one_thread(scheduled):
    analytics = _Analytics()

    _fetch(analytics, [WINDOW], wall_deadline_ms=12_000)

    (graph,) = analytics.graphs()
    assert graph.settings["max_threads"] == settings.FILTER_SELECTOR_MAX_THREADS == 1
    # The server stops it at what is left of the interactive wall.
    assert 0 < graph.cap <= 12_000
    assert graph.timeout_ms <= 12_000


@pytest.mark.unit
def test_inline_and_background_run_the_same_statement_with_the_same_numbers(
    scheduled, monkeypatch
):
    from tracer.tasks import exact_aggregation

    inline_analytics = _Analytics()
    inline = _fetch(inline_analytics, [WINDOW])

    background_analytics = _Analytics()
    monkeypatch.setattr(
        exact_aggregation,
        "_exact_observe_analytics",
        lambda: nullcontext(background_analytics),
    )
    monkeypatch.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )
    background = exact_aggregation._load_exact_payload(
        NAMESPACE,
        {
            "project_id": PROJECT_ID,
            "organization_id": ORGANIZATION_ID,
            "filters": [WINDOW],
            "interval": "hour",
            "metric_id": "latency",
        },
    )

    (inline_graph,) = inline_analytics.graphs()
    (background_graph,) = background_analytics.calls
    assert inline_graph.query == background_graph.query
    assert inline_graph.params == background_graph.params
    # Only the thread budget differs: interactive one thread, worker four.
    assert inline_graph.settings["max_threads"] == settings.FILTER_SELECTOR_MAX_THREADS
    assert (
        background_graph.settings["max_threads"]
        == settings.EXACT_GRAPH_SESSION_READ_MAX_THREADS
    )
    assert {k: v for k, v in inline_graph.settings.items() if k != "max_threads"} == {
        k: v for k, v in background_graph.settings.items() if k != "max_threads"
    }
    assert inline["data"] == background["data"]
    assert inline["metric_statistic"] == background["metric_statistic"] == "mean"


@pytest.mark.unit
@pytest.mark.parametrize(
    "rows,runs_inline",
    [(2_000_000, True), (2_000_001, False), (77_000_000, False)],
    ids=["at-threshold", "above", "largest-30d"],
)
def test_the_threshold_decides_inline_or_background(scheduled, rows, runs_inline):
    analytics = _Analytics(estimate=_estimate(rows))

    payload = _fetch(analytics, [WINDOW])

    assert len(analytics.estimates()) == 1
    if runs_inline:
        assert scheduled == [] and len(analytics.graphs()) == 1
        assert payload["query_status"] == "complete"
    else:
        assert analytics.graphs() == []
        assert [call.namespace for call in scheduled] == [NAMESPACE]
        assert payload["query_status"] == "pending"


def _raises(exc):
    def respond(call):
        del call
        raise exc

    return respond


@pytest.mark.unit
@pytest.mark.parametrize(
    "estimate",
    [
        _raises(ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)),
        _raises(ServerException("unknown", code=ErrorCodes.UNKNOWN_IDENTIFIER)),
        _raises(TimeoutError("socket")),
        _raises(EOFError()),
        lambda _call: SimpleNamespace(data=[], columns=[]),
        lambda _call: SimpleNamespace(
            data=[{"rows": "many"}], columns=list(ESTIMATE_COLUMNS)
        ),
        lambda _call: SimpleNamespace(data=[("db", 5)], columns=list(ESTIMATE_COLUMNS)),
        lambda _call: SimpleNamespace(
            data=[{"table": "spans", "rows": "10"}], columns=list(ESTIMATE_COLUMNS)
        ),
        lambda _call: SimpleNamespace(
            data=[{"table": "spans", "rows": True}], columns=list(ESTIMATE_COLUMNS)
        ),
        lambda _call: SimpleNamespace(
            data=[{"table": "traces", "rows": 10}], columns=list(ESTIMATE_COLUMNS)
        ),
        lambda _call: SimpleNamespace(
            data=[{"table": "spans", "rows": 10}], columns=["rows"]
        ),
        lambda _call: SimpleNamespace(data=None, columns=list(ESTIMATE_COLUMNS)),
    ],
    ids=[
        "stopped",
        "server-error",
        "timeout",
        "eof",
        "no-columns",
        "bad-rows",
        "not-a-dict",
        "text-rows",
        "bool-rows",
        "another-table",
        "rows-column-only",
        "no-data",
    ],
)
def test_an_unknown_estimate_never_guesses_inline(scheduled, estimate):
    analytics = _Analytics(estimate=estimate)

    payload = _fetch(analytics, [WINDOW])

    assert analytics.graphs() == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert payload["query_status"] == "pending"


@pytest.mark.unit
def test_a_failed_estimate_is_logged_with_its_stack(scheduled):
    import structlog

    analytics = _Analytics(
        estimate=_raises(ServerException("unknown", code=ErrorCodes.UNKNOWN_IDENTIFIER))
    )

    with structlog.testing.capture_logs() as records:
        _fetch(analytics, [WINDOW])

    (record,) = [
        record
        for record in records
        if record["event"] == "session_graph_inline_estimate_unavailable"
    ]
    assert record["log_level"] == "warning"
    assert record["exc_info"] is True


@pytest.mark.unit
def test_an_empty_estimate_answer_is_zero_rows(scheduled):
    analytics = _Analytics(
        estimate=lambda _call: SimpleNamespace(data=[], columns=list(ESTIMATE_COLUMNS))
    )

    payload = _fetch(analytics, [WINDOW])

    assert scheduled == [] and payload["query_status"] == "complete"


@pytest.mark.unit
def test_zero_turns_inline_off(scheduled):
    analytics = _Analytics()

    with override_settings(SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS=0):
        payload = _fetch(analytics, [WINDOW])

    assert analytics.calls == []
    # Off means the old path exactly: not even a cache-only probe first.
    assert scheduled.probes == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert payload["query_status"] == "pending"


@pytest.mark.unit
def test_a_lane_without_per_query_settings_is_never_inline(scheduled):
    analytics = _Analytics(per_query_settings=False)

    _fetch(analytics, [WINDOW])

    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]


@pytest.mark.unit
@pytest.mark.parametrize("shape", NOT_LEAN_SHAPES)
def test_span_level_and_message_filters_keep_the_background_path(scheduled, shape):
    analytics = _Analytics()

    _fetch(analytics, NOT_LEAN_SHAPES[shape])

    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]


@pytest.mark.unit
def test_other_session_metrics_are_unchanged(scheduled):
    analytics = _Analytics()

    _fetch(analytics, LEAN_SHAPES["duration"], metric_id="error_rate")

    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED),
        ServerException("cancelled", code=ErrorCodes.QUERY_WAS_CANCELLED),
    ],
    ids=["timeout", "cancelled"],
)
def test_an_inline_read_stopped_at_the_wall_goes_to_the_worker(scheduled, error):
    analytics = _Analytics(graph=_raises(error))

    payload = _fetch(analytics, [WINDOW])

    assert len(analytics.graphs()) == 1
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert scheduled[0].refresh is False
    assert payload["query_status"] == "pending"


@pytest.mark.unit
def test_an_inline_programming_error_is_not_hidden(scheduled):
    analytics = _Analytics(
        graph=_raises(ServerException("syntax", code=ErrorCodes.SYNTAX_ERROR))
    )

    with pytest.raises(ServerException):
        _fetch(analytics, [WINDOW])

    assert scheduled == []


@pytest.mark.unit
def test_an_explicit_refresh_of_an_affordable_scope_is_inline(scheduled):
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW], refresh=True)

    assert scheduled == [] and payload["query_status"] == "complete"


@pytest.mark.unit
def test_an_empty_window_is_inline_without_a_statement(scheduled):
    analytics = _Analytics()
    empty = _system("created_at", "datetime", "between", [LO.isoformat()] * 2)

    payload = _fetch(analytics, [empty])

    assert analytics.calls == [] and scheduled == []
    assert payload["query_status"] == "complete" and payload["data"] == []


@pytest.mark.unit
def test_the_threshold_is_a_registered_runtime_setting_documented_for_operators():
    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

    name = "SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS"
    spec = RUNTIME_NUMERIC_SETTING_SPECS[name]
    assert (
        spec.parse(name)
        == 2_000_000
        == settings.SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS
    )
    assert spec.parse(name, "") == 2_000_000
    assert spec.parse(name, "0") == 0  # off
    with pytest.raises(ValueError, match=name):
        spec.parse(name, "-1")
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    assert f"\n{name}=2000000\n" in env_example.read_text()


# A scope the estimate misjudges must not cost every request the whole wall.
# The browser's first request gives up after 30 s (AGGREGATION_REQUEST_TIMEOUT_MS)
# and then stops polling, so an inline read that ran to the view's 30 s wall
# before the fallback answered hid the cached or pending chart for good - on
# every reload, even after the worker had published a complete snapshot.

COMPLETE_SNAPSHOT = {
    "metric_name": "latency",
    "data": [{"timestamp": LO.isoformat(), "value": 7.0, "primary_traffic": 1}],
    "query_complete": True,
    "query_status": "complete",
    "query_sampled": False,
    "query_refreshing": False,
    "query_refresh_failed": False,
    "query_cached": True,
    "metric_statistic": "mean",
}


@pytest.mark.unit
def test_a_cached_snapshot_is_served_before_any_estimate_or_inline_read(scheduled):
    scheduled.cached = COMPLETE_SNAPSHOT
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW])

    assert analytics.calls == []
    assert scheduled == []
    (probe,) = scheduled.probes
    assert probe.namespace == NAMESPACE and probe.refresh is False
    # The probe may revalidate an old open-window hit, as the trace path does.
    assert probe.revalidate_open_window is True
    assert payload["query_status"] == "complete"
    assert payload["data"] == COMPLETE_SNAPSHOT["data"]


@pytest.mark.unit
def test_polls_while_a_refresh_runs_do_not_repeat_the_inline_read(scheduled):
    scheduled.cached = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
        "query_refresh_failed": False,
    }
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW])

    assert analytics.calls == [] and scheduled == []
    assert payload["query_refreshing"] is True


@pytest.mark.unit
def test_an_explicit_refresh_of_a_cached_scope_goes_to_the_worker(scheduled):
    scheduled.cached = COMPLETE_SNAPSHOT
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW], refresh=True)

    assert analytics.calls == []
    (probe,) = scheduled.probes
    assert probe.revalidate_open_window is False
    (call,) = scheduled
    assert call.refresh is True
    assert payload["query_status"] == "complete"


@pytest.mark.unit
def test_a_miss_after_a_failed_background_refresh_may_still_go_inline(scheduled):
    scheduled.cached = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": False,
        "query_refresh_failed": True,
    }
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW])

    assert scheduled == [] and len(analytics.graphs()) == 1
    assert payload["query_status"] == "complete"


@pytest.mark.unit
def test_after_an_inline_read_fails_the_same_scope_goes_straight_to_the_worker(
    scheduled,
):
    stopped = ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)
    first = _Analytics(graph=_raises(stopped))

    _fetch(first, [WINDOW])

    assert len(first.estimates()) == 1 and len(first.graphs()) == 1
    # The worker's job is deferred or failed, so the next request misses again:
    # it must not spend another estimate plus an inline read on the same scope.
    again = _Analytics(graph=_raises(stopped))
    payload = _fetch(again, [WINDOW])

    assert again.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE, NAMESPACE]
    assert payload["query_status"] == "pending"
    # Another scope is not affected.
    other = _Analytics()
    assert _fetch(other, LEAN_SHAPES["duration"])["query_status"] == "complete"


@pytest.mark.unit
@pytest.mark.parametrize("configured", [None, 600])
def test_the_inline_failure_backoff_expires(scheduled, monkeypatch, configured):
    """The backoff is the configured failed-refresh TTL (300 s by default)."""

    from tracer.services.clickhouse import session_graph

    ttls = []
    real_set = session_graph.cache.set

    def recording_set(key, value, timeout=None, **kwargs):
        ttls.append(timeout)
        return real_set(key, value, timeout=timeout, **kwargs)

    monkeypatch.setattr(session_graph.cache, "set", recording_set)
    stopped = ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)

    overrides = {"EXACT_AGGREGATION_REFRESH_FAILURE_SECONDS": configured}
    with override_settings(**(overrides if configured else {})):
        _fetch(_Analytics(graph=_raises(stopped)), [WINDOW])

    assert ttls == [configured or 5 * 60]


@pytest.mark.unit
def test_an_unavailable_backoff_store_does_not_fail_the_request(scheduled, monkeypatch):
    from tracer.services.clickhouse import session_graph

    def broken(*_args, **_kwargs):
        raise ConnectionError("cache down")

    monkeypatch.setattr(session_graph.cache, "get", broken)
    monkeypatch.setattr(session_graph.cache, "set", broken)
    stopped = ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)

    payload = _fetch(_Analytics(graph=_raises(stopped)), [WINDOW])

    assert payload["query_status"] == "pending"
    assert [call.namespace for call in scheduled] == [NAMESPACE]


@pytest.mark.unit
@pytest.mark.parametrize(
    "wall_ms,inline_ms",
    [(30_000, 15_000), (12_000, 6_000), (60_000, 15_000)],
    ids=["view-wall", "late-in-the-view", "longer-wall"],
)
def test_the_inline_attempt_leaves_the_fallback_half_the_wall(
    scheduled, wall_ms, inline_ms
):
    analytics = _Analytics()

    _fetch(analytics, [WINDOW], wall_deadline_ms=wall_ms)

    (estimate,) = analytics.estimates()
    (graph,) = analytics.graphs()
    # At most half of what is left, never more than 15 s: the fallback keeps
    # the other half, well inside the browser's 30 s request timeout.
    assert 0 < graph.cap <= inline_ms
    assert graph.timeout_ms <= inline_ms
    assert 0 < estimate.cap <= 1_500


@pytest.mark.unit
def test_a_wall_too_short_to_share_goes_to_the_worker(scheduled):
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW], wall_deadline_ms=20)

    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert payload["query_status"] == "pending"


# Classifying a shape must not read metadata on the request path: a
# has_annotation filter can never be the lean shape, and building its
# membership plan read PostgreSQL - during an outage that answered HTTP 500
# where the background path answers pending.


def _has_annotation(complete):
    return _system("has_annotation", "boolean", "equals", complete)


@pytest.mark.unit
@pytest.mark.parametrize("complete", [True, False], ids=["complete", "incomplete"])
def test_a_has_annotation_filter_is_classified_without_a_metadata_read(
    scheduled, monkeypatch, complete
):
    from django.db import DatabaseError

    from tracer.services.clickhouse import exact_graph_reads

    reads = []

    def unavailable(project_id):
        reads.append(project_id)
        raise DatabaseError("metadata down")

    monkeypatch.setattr(
        exact_graph_reads, "get_annotation_labels_for_project", unavailable
    )
    analytics = _Analytics()

    payload = _fetch(analytics, [WINDOW, _has_annotation(complete)])

    assert reads == []
    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert payload["query_status"] == "pending"


@pytest.mark.unit
@pytest.mark.parametrize("complete", [True, False], ids=["complete", "incomplete"])
def test_the_has_annotation_shortcut_agrees_with_the_membership_plan(
    monkeypatch, complete
):
    from tracer.services.clickhouse import exact_graph_reads

    monkeypatch.setattr(
        exact_graph_reads,
        "_annotation_label_ids_for_filters",
        lambda *_args: ("11111111-1111-4111-8111-111111111111",),
    )
    plan = exact_graph_reads._session_membership_plan(
        project_id=PROJECT_ID, filters=[_has_annotation(complete)]
    )

    # The plan the classifier used to build always had a span-level leaf.
    assert plan.scalar_predicates or plan.relational_predicates
    assert not exact_graph_reads.session_graph_reads_lean_roots(
        project_id=PROJECT_ID, filters=[WINDOW, _has_annotation(complete)]
    )


@pytest.mark.unit
def test_a_classification_that_cannot_be_built_takes_the_background_path(
    scheduled, monkeypatch
):
    from tracer.services.clickhouse import exact_graph_reads

    def unbuildable(**_kwargs):
        raise exact_graph_reads.ExactGraphReadError("metadata unavailable")

    monkeypatch.setattr(exact_graph_reads, "_session_membership_plan", unbuildable)
    analytics = _Analytics()

    payload = _fetch(analytics, NOT_LEAN_SHAPES["model"])

    assert analytics.calls == []
    assert [call.namespace for call in scheduled] == [NAMESPACE]
    assert payload["query_status"] == "pending"
