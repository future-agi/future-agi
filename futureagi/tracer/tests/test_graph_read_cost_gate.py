"""The filtered graph must decide its lane from the index, not from the wall.

A filtered trace/span graph reads every physical span in its window. On the
highest-volume reference tenant that window is hundreds of millions of rows:
the interactive statement expires at thirty seconds and publishes nothing, and
the background worker then runs the identical SQL anyway. These tests pin the
routing decision that has to happen BEFORE the statement, and in particular
pin that a probe which cannot answer never licenses an unbounded read.

Every assertion here is about which statements are ISSUED. None of them
changes what a statement returns: the graph SQL and its window parameters are
byte-identical on both lanes, which is the property
``test_gate_never_narrows_the_statement_window`` states directly.
"""

import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import graph_dispatch, graph_read_cost
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

PROJECT_ID = "3f7d5b3a-2c41-4f8e-9b0d-5a1c7e2f4d60"
ORG_ID = "6a2b1c4d-8e3f-4a5b-9c7d-0e1f2a3b4c5d"

GRAPH_COLUMNS = [
    "time_bucket",
    "avg_latency",
    "total_tokens",
    "avg_cost",
    "traffic_count",
    "prompt_tokens",
    "completion_tokens",
    "error_rate",
]
ESTIMATE_COLUMNS = ["database", "table", "parts", "rows", "marks"]


def _window(days: int = 30) -> dict:
    end = datetime(2026, 9, 16, tzinfo=UTC)
    start = end - timedelta(days=days)
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _attribute_filter(key: str = "deployment.environment", value: str = "production"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _estimate(rows: int) -> SimpleNamespace:
    return SimpleNamespace(
        data=[
            {
                "database": "default",
                "table": "spans",
                "parts": 207,
                "rows": rows,
                "marks": max(1, rows // 8192),
            }
        ],
        columns=list(ESTIMATE_COLUMNS),
        query_time_ms=1,
    )


class Analytics:
    """Answer the cost probe with *estimated_rows*; record every statement."""

    supports_per_query_read_settings = True

    def __init__(self, *, estimated_rows, seed_estimate=None, seed_raises=False):
        self.calls = []
        self._estimated_rows = estimated_rows
        self._seed_estimate = seed_estimate
        self._seed_raises = seed_raises

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append((query, dict(params), kwargs))
        if "EXPLAIN ESTIMATE" not in query:
            return SimpleNamespace(
                data=[], columns=list(GRAPH_COLUMNS), query_time_ms=1
            )
        if "graph_cost_project_id" in query:
            if self._estimated_rows is None:
                return SimpleNamespace(data=[], columns=[], query_time_ms=1)
            return _estimate(self._estimated_rows)
        if self._seed_raises:
            raise TimeoutError("seed probe exceeded its budget")
        if self._seed_estimate is None:
            return SimpleNamespace(data=[], columns=[], query_time_ms=1)
        return _estimate(self._seed_estimate)

    @property
    def statements(self):
        return [call for call in self.calls if "EXPLAIN ESTIMATE" not in call[0]]

    @property
    def cost_probes(self):
        return [call for call in self.calls if "graph_cost_project_id" in call[0]]

    @property
    def seed_probes(self):
        return [
            call
            for call in self.calls
            if "EXPLAIN ESTIMATE" in call[0] and "graph_cost_project_id" not in call[0]
        ]


class _ScheduleLog(list):
    """Recorded cache calls, plus the one piece of cache state a test sets."""

    previous_refresh_failed = False

    @property
    def enqueued(self):
        """Calls that would actually start a background refresh."""
        return [call for call in self if call[2].get("schedule_on_miss") is not False]


@pytest.fixture
def scheduled(monkeypatch):
    """Model the snapshot cache closely enough to tell its answers apart.

    ``schedule_on_miss=False`` is the interactive cold probe and enqueues
    nothing. Every other call enqueues - EXCEPT behind a failed refresh, where
    the real cache re-enqueues only for an explicit user refresh and otherwise
    hands back its own sanitized failed envelope
    (``read_or_schedule_exact_snapshot``: "Failed cold jobs wait for another
    explicit refresh instead of being resubmitted by every polling request").
    """

    calls = _ScheduleLog()

    def _read_or_schedule(namespace, identity, **kwargs):
        calls.append((namespace, identity, kwargs))
        if kwargs.get("schedule_on_miss") is False:
            # A cold cache probe. Returning the pending envelope here would
            # short-circuit every case below before any read is routed.
            return None
        if calls.previous_refresh_failed and not kwargs.get("refresh"):
            return {
                **dict(kwargs["pending_payload"]),
                "query_refreshing": False,
                "query_refresh_failed": True,
            }
        return dict(kwargs["pending_payload"])

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        _read_or_schedule,
    )
    return calls


def _fetch(analytics, *, observe_type="trace", organization_id=ORG_ID, filters=None):
    return graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters if filters is not None else [_window(), _attribute_filter()],
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
        organization_id=organization_id,
        workspace_id=None,
    )


# --- the read that cannot finish must never be issued ----------------------


@pytest.mark.unit
def test_unaffordable_trace_graph_is_scheduled_without_issuing_the_statement(
    scheduled,
):
    """87.4M estimated rows on a 30 s wall: schedule, do not spend it."""

    analytics = Analytics(estimated_rows=87_400_000, seed_estimate=87_300_000)
    response = _fetch(analytics)

    assert analytics.statements == [], "the graph statement must not be issued"
    assert len(analytics.cost_probes) == 1
    refreshes = scheduled.enqueued
    assert len(refreshes) == 1
    assert refreshes[0][0] == "observe-system-graph"
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_unaffordable_span_graph_is_scheduled_although_it_never_seeds(scheduled):
    """A span graph compiles no trace witness, so the window IS the read."""

    analytics = Analytics(estimated_rows=87_400_000)
    response = _fetch(analytics, observe_type="span")

    assert analytics.statements == []
    assert analytics.seed_probes == [], "a span graph has no seed to probe"
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_seed_probe_failure_on_an_unaffordable_read_schedules_not_scans(scheduled):
    """The deleted fallback: a probe that times out must not mean 'read all'.

    This is the exact production shape. The seed probe exceeded its own
    1,500 ms budget on the high-volume tenant, the dispatcher swallowed the
    failure, and the unseeded full-window statement then ran to the thirty
    second wall and returned nothing.
    """

    analytics = Analytics(estimated_rows=87_400_000, seed_raises=True)
    response = _fetch(analytics)

    assert analytics.statements == [], (
        "a seed probe that cannot answer is not a licence to read the window"
    )
    assert len(analytics.seed_probes) >= 1
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_a_read_no_wall_can_absorb_is_refused_not_scheduled(scheduled):
    """The background lane is a wider wall, not an unbounded one.

    Twelve months on the high-volume tenant is 311.9M estimated rows. At the
    measured rate the 180 s background wall affords 174.2M, so the worker
    would expire too - and a failed cold refresh leaves no snapshot while the
    next poll is free to ask for another one. Scheduling that is a spinner
    with no terminal state and one full-window scan per poll cycle.
    """
    from django.conf import settings

    hopeless = settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._RAW_SCAN_ROWS_PER_MS
    analytics = Analytics(estimated_rows=hopeless + 1, seed_raises=True)
    response = _fetch(analytics)

    assert analytics.statements == []
    assert scheduled.enqueued == []
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_provenance"] == "read_cost_gate"


@pytest.mark.unit
def test_a_read_the_background_wall_can_absorb_is_still_scheduled(scheduled):
    """Thirty days on the same tenant fits the worker, so it must reach it."""
    from django.conf import settings

    hopeless = settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._RAW_SCAN_ROWS_PER_MS
    analytics = Analytics(estimated_rows=hopeless, seed_raises=True)
    response = _fetch(analytics)

    assert analytics.statements == []
    assert len(scheduled.enqueued) == 1
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_unaffordable_read_without_a_background_lane_fails_fast(scheduled):
    """No organization to schedule under: refuse now, not in thirty seconds."""

    analytics = Analytics(estimated_rows=87_400_000, seed_raises=True)
    response = _fetch(analytics, organization_id=None)

    assert analytics.statements == []
    assert scheduled.enqueued == []
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_provenance"] == "read_cost_gate"


@pytest.mark.unit
def test_twelve_months_on_the_high_volume_tenant_delivers_a_chart(scheduled):
    """The window this lane first declared unreachable must now SCHEDULE.

    The index says 311,867,981 physical spans over twelve months on the
    reference tenant, and the statement then reads 311,867,844 of them. Run on
    production at this surface's own worker count, over five abutting scan
    windows whose union is exactly what the whole statement reads, that costs
    170,148 ms of server time - inside the 180 s background wall, outside the
    30 s interactive one. So the browser gets a pending envelope and then a
    chart, not the terminal error a 968 rows/ms calibration predicted.

    The margin is single digits, and that is the honest state of it: a read
    that misses now costs ONE bounded worker attempt and a terminal envelope,
    which is what makes scheduling it the better answer than refusing it.
    """
    from django.conf import settings

    twelve_months = 311_867_981
    analytics = Analytics(estimated_rows=twelve_months, seed_raises=True)

    response = _fetch(analytics)

    assert analytics.statements == [], "30 s cannot run it; do not spend the wall"
    assert len(scheduled.enqueued) == 1, "it must reach the worker, not be refused"
    assert response["query_status"] == "pending"
    # The prediction that decides it, stated as arithmetic rather than trusted.
    assert graph_read_cost.raw_graph_scan_fits_wall(
        twelve_months, remaining_ms=settings.GRAPH_BACKGROUND_WALL_MS
    ), "the background wall must afford the measured twelve-month scan"
    assert not graph_read_cost.raw_graph_scan_fits_wall(
        twelve_months, remaining_ms=settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


@pytest.mark.unit
def test_thirty_days_on_the_high_volume_tenant_never_runs_inline():
    """The rate must stay slow enough to keep the original defect out.

    A rate honest for twelve months is optimistic for thirty days, and if it
    ever rose past 87,413,938 rows / 30,000 ms the 30-day read would be
    predicted to fit the interactive wall and would run there - which is the
    production defect this PR exists to remove, reintroduced through the
    constant. Measured, that read takes 48,677 ms.
    """
    from django.conf import settings

    thirty_days = 87_413_938
    assert not graph_read_cost.raw_graph_scan_fits_wall(
        thirty_days, remaining_ms=settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert graph_read_cost.raw_graph_scan_fits_wall(
        thirty_days, remaining_ms=settings.GRAPH_BACKGROUND_WALL_MS
    )
    # The measured duration, and the gate's prediction of it, agree to 2%.
    predicted_ms = thirty_days / graph_read_cost._RAW_SCAN_ROWS_PER_MS
    assert 44_000 <= predicted_ms <= 53_000


# --- the read that can finish must still run inline ------------------------


@pytest.mark.unit
def test_affordable_read_still_runs_inline(scheduled):
    """The mid-volume tenant completes today and must keep completing."""

    analytics = Analytics(estimated_rows=10_500_000)
    response = _fetch(analytics)

    assert len(analytics.statements) == 1
    assert response["query_complete"] is True
    assert scheduled.enqueued == []


@pytest.mark.unit
def test_an_unknown_estimate_schedules_instead_of_scanning(scheduled):
    """A read nobody could cost is the one least safe to issue inline.

    This assertion is the inverse of the one this file shipped with. That one
    pinned "an unknown estimate does not divert the read", i.e. a cost probe
    that CANNOT ANSWER hands the full-window statement the whole interactive
    wall - which is the production defect this PR exists to remove, moved from
    the second probe in the chain to the first. Absence of proof means
    SCHEDULE, not SCAN: the read goes to the background wall, and the browser
    gets a pending envelope in the same half second instead of an error in
    thirty.
    """

    analytics = Analytics(estimated_rows=None, seed_raises=True)
    response = _fetch(analytics)

    assert analytics.statements == [], (
        "an uncosted read must not be issued on the interactive wall"
    )
    assert len(analytics.cost_probes) == 1
    refreshes = scheduled.enqueued
    assert len(refreshes) == 1
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_an_unknown_estimate_on_a_span_graph_also_schedules(scheduled):
    """A span graph has no seed lever, so the unknown estimate is the whole ruling."""

    analytics = Analytics(estimated_rows=None)
    response = _fetch(analytics, observe_type="span")

    assert analytics.statements == []
    assert analytics.seed_probes == []
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_an_unknown_estimate_still_lets_an_admitted_seed_run_inline(scheduled):
    """Failing closed is not refusing: a proven-selective witness still bounds it."""

    analytics = Analytics(estimated_rows=None, seed_estimate=1_600_000)
    response = _fetch(analytics)

    assert len(analytics.statements) == 1
    assert "GROUP BY trace_id" in analytics.statements[0][0]
    assert response["query_complete"] is True


@pytest.mark.unit
def test_an_admitted_seed_rescues_an_unaffordable_window(scheduled):
    """A selective witness is the one thing that can bound this read inline."""

    analytics = Analytics(estimated_rows=87_400_000, seed_estimate=1_600_000)
    response = _fetch(analytics)

    assert len(analytics.statements) == 1
    statement = analytics.statements[0][0]
    assert "GROUP BY trace_id" in statement, "the statement must carry the seed set"
    assert response["query_complete"] is True


# --- exactness: routing must not change what is read -----------------------


@pytest.mark.unit
def test_gate_never_narrows_the_statement_window(scheduled):
    """A routed read covers the same window an ungated one would.

    The gate decides WHICH lane runs the statement. If it ever changed the
    window, a published chart would silently describe less than the user
    asked for, which is worse than a slow one.
    """

    ungated = Analytics(estimated_rows=10_500_000)
    _fetch(ungated)
    _query, ungated_params, _kwargs = ungated.statements[0]

    seeded = Analytics(estimated_rows=87_400_000, seed_estimate=1_600_000)
    _fetch(seeded)
    _query, seeded_params, _kwargs = seeded.statements[0]

    for key in (
        "start_date",
        "end_date",
        "graph_witness_start_date",
        "graph_witness_end_date",
    ):
        assert seeded_params[key] == ungated_params[key], key


# --- the probes themselves -------------------------------------------------


@pytest.mark.unit
def test_cost_probe_is_metadata_only(scheduled):
    """It must read no parts: no predicate, no indexHint, no IN subquery."""

    analytics = Analytics(estimated_rows=10_500_000)
    _fetch(analytics)
    query, params, kwargs = analytics.cost_probes[0]

    assert query.strip().startswith("EXPLAIN ESTIMATE")
    lowered = query.lower()
    assert "indexhint" not in lowered
    assert "attrs_string" not in lowered
    assert "attrs_number" not in lowered
    assert " in (" not in lowered, "an EXPLAIN executes an IN subquery to plan it"
    assert " final" not in lowered
    assert "sample " not in lowered
    assert "is_deleted" not in lowered, "not in the primary key; it cannot narrow"
    # An estimate routed to a projection describes the projection, not the
    # base table the graph statement reads.
    assert kwargs["settings"]["optimize_use_projections"] == 0
    assert params["graph_cost_project_id"] == PROJECT_ID


@pytest.mark.unit
def test_cost_probe_covers_the_widest_window_the_statement_can_scan(scheduled):
    """The raw statement widens its scan by a day at each end; so must this."""

    analytics = Analytics(estimated_rows=10_500_000)
    _fetch(analytics)
    _query, cost_params, _kwargs = analytics.cost_probes[0]
    _query, statement_params, _kwargs = analytics.statements[0]

    assert (
        cost_params["graph_cost_scan_start"]
        <= statement_params["graph_witness_start_date"]
    )
    assert (
        cost_params["graph_cost_scan_end"] >= statement_params["graph_witness_end_date"]
    )


@pytest.mark.unit
def test_seed_probe_is_not_pinned_to_one_worker(scheduled):
    """Index analysis parallelises, and this probe is nothing but that.

    Measured on production against the highest-volume reference tenant, one
    worker cost 831 ms at thirty days and 2,777 ms at twelve months - past the
    probe's own 1,500 ms budget. The same estimate at the graph statement's
    own worker count took 63 ms and 314 ms.
    """
    from django.conf import settings

    analytics = Analytics(estimated_rows=87_400_000, seed_estimate=1_600_000)
    _fetch(analytics)
    _query, _params, kwargs = analytics.seed_probes[0]

    assert (
        kwargs["settings"]["max_threads"] == settings.DASHBOARD_TRACE_READ_MAX_THREADS
    )
    assert kwargs["settings"]["max_threads"] > 1
    assert kwargs["settings"]["optimize_use_projections"] == 0


# --- the background worker is one door along, and it is gated too ----------


def _background(analytics, *, observe_type="trace", filters=None):
    return graph_dispatch.fetch_background_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters if filters is not None else [_window(), _attribute_filter()],
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )


@pytest.mark.unit
def test_background_worker_refuses_a_read_its_own_wall_cannot_absorb():
    """The unbounded read was still there, one door along.

    The worker entered the identical raw statement with NO cost gate: its seed
    probe raised, the exception was swallowed, and it issued the unseeded
    full-window read on the 180 s wall. Gating at the statement - not at any
    caller - covers every way the worker is reached, the front-door explicit
    refresh included, because none of those doors costs anything.
    """
    from django.conf import settings

    hopeless = settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._RAW_SCAN_ROWS_PER_MS
    analytics = Analytics(estimated_rows=hopeless + 1, seed_raises=True)

    with pytest.raises(graph_dispatch.BoundedGraphReadError) as raised:
        _background(analytics)

    assert analytics.statements == [], (
        "the worker must not issue a statement its own wall cannot finish"
    )
    assert raised.value.error_code == "read_budget_exceeded"


@pytest.mark.unit
def test_background_worker_runs_a_read_its_own_wall_can_absorb():
    """The wider wall is the point of the lane: what fits it must still run."""
    from django.conf import settings

    # Half the wall: the probes themselves cost a millisecond or two, so the
    # exact boundary belongs to the arithmetic guard, not to this one.
    affordable = (
        settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._RAW_SCAN_ROWS_PER_MS
    ) // 2
    analytics = Analytics(estimated_rows=affordable, seed_raises=True)

    response = _background(analytics)

    assert len(analytics.statements) == 1
    assert response["query_complete"] is True


@pytest.mark.unit
def test_background_worker_runs_an_uncosted_read_on_its_bounded_wall():
    """Absence of proof means schedule - and that has to end somewhere.

    The worker IS the lane an uncosted read is scheduled to. Refusing it there
    would mean a graph whose cost probe merely could not answer never renders
    at all, and the containment the principle asks for - a bounded wall, one
    deduplicated attempt, a terminal state - is already what this lane gives.
    """

    analytics = Analytics(estimated_rows=None, seed_raises=True)

    response = _background(analytics)

    assert len(analytics.statements) == 1
    assert len(analytics.cost_probes) == 1
    assert response["query_complete"] is True


@pytest.mark.unit
def test_background_worker_costs_a_span_graph_too():
    """Span graphs were never probed at all; the worker's gate does not skip them."""
    from django.conf import settings

    hopeless = settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._RAW_SCAN_ROWS_PER_MS
    analytics = Analytics(estimated_rows=hopeless + 1)

    with pytest.raises(graph_dispatch.BoundedGraphReadError):
        _background(analytics, observe_type="span")

    assert analytics.statements == []
    assert analytics.seed_probes == []


@pytest.mark.unit
def test_background_worker_is_the_namespace_handler_the_refresh_task_calls():
    """The gate is worthless if the worker still reaches the ungated function."""

    from inspect import getsource

    from tracer.tasks import exact_aggregation

    source = getsource(exact_aggregation._observe_payload)
    assert "fetch_background_raw_system_metric_graph(" in source
    assert "_fetch_direct_raw_system_metric_graph" not in source


# --- a failed background read must be terminal, not a spinner per poll ------


@pytest.mark.unit
def test_a_poll_behind_a_failed_refresh_does_not_re_enqueue_the_scan(scheduled):
    """The cache already refuses to resubmit; the gate must stop overriding it.

    ``read_or_schedule_exact_snapshot`` re-enqueues a failed cold job only for
    an explicit user refresh. Passing ``refresh=True`` unconditionally bypassed
    exactly that rule, which is what would have made a scheduled read cost one
    full-window scan per poll cycle - and was the stated reason for refusing
    twelve months outright rather than scheduling it.
    """

    scheduled.previous_refresh_failed = True
    analytics = Analytics(estimated_rows=87_400_000, seed_raises=True)

    response = _fetch(analytics)

    assert analytics.statements == []
    assert [call for call in scheduled.enqueued if call[2]["refresh"] is True] == []
    # A pending envelope the cache will never complete is a spinner with no
    # terminal state. The user is owed the refusal instead.
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_provenance"] == "read_cost_gate"


# --- the estimate reducer --------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "columns", "expected"),
    [
        (
            [{"table": "spans", "rows": 5}, {"table": "spans", "rows": 7}],
            ESTIMATE_COLUMNS,
            12,
        ),
        ([], ESTIMATE_COLUMNS, 0),
        ([{"table": "spans", "rows": 5}], ["time_bucket", "traffic_count"], None),
        ([{"table": "other", "rows": 5}], ESTIMATE_COLUMNS, None),
        ([{"table": "spans", "rows": "many"}], ESTIMATE_COLUMNS, None),
        ([{"table": "spans"}], ESTIMATE_COLUMNS, None),
    ],
)
def test_estimate_reducer_tells_zero_from_unknown(rows, columns, expected):
    assert graph_read_cost._reduce_estimate(rows, columns) == expected


@pytest.mark.unit
def test_affordable_rows_scale_with_the_wall_not_with_a_window():
    """Doubling the deadline must double what it can afford."""

    rows = graph_read_cost._RAW_SCAN_ROWS_PER_MS * 10_000
    assert graph_read_cost.raw_graph_scan_fits_wall(rows, remaining_ms=10_000) is True
    assert (
        graph_read_cost.raw_graph_scan_fits_wall(rows + 1, remaining_ms=10_000) is False
    )
    assert (
        graph_read_cost.raw_graph_scan_fits_wall(rows + 1, remaining_ms=20_000) is True
    )


# --- the users graph: same door, its own statement, its own rate ------------
#
# The aggregate users graph had no gate at all: its dispatcher ran the ordered
# latest-state statement on the interactive wall, expired there at thirty
# seconds, and only then scheduled the identical statement. On the highest-
# volume tenant that statement reads 311M physical spans over six months at a
# measured 553 rows/ms - 563 s, against a 180 s background wall - so the wall
# was spent, the worker then failed, and the user got neither an answer nor a
# chart. These tests pin that the lane is decided before the statement, from
# the index, at THIS statement's rate, and that the probe costs exactly the
# identity-hour window the statement scans.


class _UsersGraphAnalytics(Analytics):
    """The gate's probe answered; the reader itself is patched in each test."""

    def execute_ch_query(self, query, params, **kwargs):
        assert "EXPLAIN ESTIMATE" in query, (
            "the users graph reader is patched; only the cost probe may run"
        )
        return super().execute_ch_query(query, params, **kwargs)


@pytest.fixture
def users_reader(monkeypatch):
    reads = []

    def _reader(**kwargs):
        reads.append(kwargs)
        return {
            "metric_name": kwargs["metric_id"],
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(graph_dispatch, "read_exact_user_system_graph", _reader)
    return reads


def _fetch_users(analytics, *, organization_id=ORG_ID, refresh=False, days=180):
    return graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_window(days)],
        interval="week",
        metric_id="cost",
        refresh=refresh,
        organization_id=organization_id,
        workspace_id=None,
    )


SIX_MONTHS_ON_THE_REFERENCE_TENANT = 311_340_000


@pytest.mark.unit
def test_unaffordable_users_graph_is_scheduled_without_spending_the_wall(
    scheduled, users_reader
):
    """The production shape on the reference tenant, decided before the read."""
    from django.conf import settings

    background_affords = (
        settings.GRAPH_BACKGROUND_WALL_MS * graph_read_cost._USER_GRAPH_SCAN_ROWS_PER_MS
    )
    analytics = _UsersGraphAnalytics(estimated_rows=background_affords)
    response = _fetch_users(analytics)

    assert users_reader == [], "the statement must not be issued on this wall"
    assert len(analytics.cost_probes) == 1
    refreshes = scheduled.enqueued
    assert len(refreshes) == 1
    assert refreshes[0][0] == "observe-user-system-graph"
    assert refreshes[0][2]["refresh"] is False, "the user's own flag, never forced"
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_six_months_on_the_reference_tenant_is_refused_in_probe_time(
    scheduled, users_reader
):
    """311M rows at 553 rows/ms is 563 s: no wall the product owns runs it.

    Before: thirty seconds inline, a pending envelope, then a worker attempt
    that expires at 180 s and a failed refresh the browser renders as an
    error. Now: the same terminal answer in the time one metadata probe takes,
    and no full-window scan on either lane.
    """

    analytics = _UsersGraphAnalytics(estimated_rows=SIX_MONTHS_ON_THE_REFERENCE_TENANT)
    response = _fetch_users(analytics)

    assert users_reader == []
    assert scheduled.enqueued == []
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_provenance"] == "read_cost_gate"


@pytest.mark.unit
def test_users_graph_probe_that_cannot_answer_schedules_not_scans(
    scheduled, users_reader
):
    """Unknown is not small. An uncosted read goes to the bounded worker."""

    analytics = _UsersGraphAnalytics(estimated_rows=None)
    response = _fetch_users(analytics)

    assert users_reader == []
    assert len(scheduled.enqueued) == 1
    assert response["query_status"] == "pending"


@pytest.mark.unit
def test_users_graph_that_fits_the_wall_reads_exactly_as_before(
    scheduled, users_reader
):
    """A week on the reference tenant (2.1M rows) keeps its synchronous answer."""

    analytics = _UsersGraphAnalytics(estimated_rows=2_140_000)
    response = _fetch_users(analytics, days=7)

    assert len(users_reader) == 1
    assert len(analytics.cost_probes) == 1
    assert scheduled.enqueued == []
    assert response["query_status"] == "complete"
    assert response["query_provenance"] == "exact_snapshot"


@pytest.mark.unit
def test_unaffordable_users_graph_without_a_background_lane_fails_fast(
    scheduled, users_reader
):
    analytics = _UsersGraphAnalytics(estimated_rows=SIX_MONTHS_ON_THE_REFERENCE_TENANT)
    response = _fetch_users(analytics, organization_id=None)

    assert users_reader == []
    assert scheduled.enqueued == []
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_provenance"] == "read_cost_gate"


@pytest.mark.unit
def test_users_graph_probe_costs_the_window_the_statement_scans():
    """The gate's probe and the statement bound the same identity hours.

    The statement scans complete identity hours because the replacement key
    holds ``toStartOfHour(start_time)``. A probe over a narrower window would
    under-cost the read; over a wider one it would refuse charts that fit.
    """
    from tracer.services.clickhouse.exact_graph_reads import (
        read_exact_user_system_graph,
    )

    end = datetime(2026, 9, 16, 10, 25, 30, tzinfo=UTC)
    start = end - timedelta(days=45)
    window = {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }

    class _Capture:
        captured = None

        def remaining_read_ms(self, cap_ms):
            return int(cap_ms)

        def execute_ch_query(self, query, params=None, **kwargs):
            self.captured = (query, dict(params or {}))
            raise RuntimeError("captured")

    statement = _Capture()
    with pytest.raises(RuntimeError):
        read_exact_user_system_graph(
            analytics=statement,
            project_id=PROJECT_ID,
            filters=[window],
            interval="day",
            metric_id="cost",
        )
    probe = _Capture()
    # The dispatcher's own gate, on the same filters: it must derive the
    # window the way the reader does (down to the naive-UTC normalisation the
    # analyzer applies), not from a parallel reading of the request.
    verdict = graph_dispatch._affordable_user_graph_read(
        analytics=probe,
        project_id=PROJECT_ID,
        filters=[window],
        interactive_deadline_ms=30_000,
    )
    assert isinstance(verdict, graph_dispatch._GraphReadUnaffordable)
    assert verdict.estimated_rows is None, (
        "a probe that raised leaves the read uncosted"
    )
    _statement_sql, statement_params = statement.captured
    _probe_sql, probe_params = probe.captured
    assert (
        probe_params["graph_cost_scan_start"]
        == statement_params["user_snapshot_scan_start"]
    )
    assert (
        probe_params["graph_cost_scan_end"]
        == statement_params["user_snapshot_scan_end"]
    )
    assert probe_params["graph_cost_scan_end"] > end.replace(tzinfo=None), (
        "the end hour is rounded UP"
    )
    assert probe_params["graph_cost_scan_start"].tzinfo == (
        statement_params["user_snapshot_scan_start"].tzinfo
    )


@pytest.mark.unit
def test_users_graph_rate_keeps_the_reference_windows_where_measured():
    """The calibration's consequences, as arithmetic rather than trust.

    Measured on the reference tenant: a week (2.14M rows) completes inline in
    about a second; six months (311M rows) read 21% of its window in 120 s.
    The rate must keep the first on the interactive wall, the second off
    every wall, and thirty days (87.4M rows) off the interactive wall but on
    the worker's.
    """
    from django.conf import settings

    interactive = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    background = settings.GRAPH_BACKGROUND_WALL_MS
    assert graph_read_cost.user_graph_scan_fits_wall(
        2_140_000, remaining_ms=interactive
    )
    assert not graph_read_cost.user_graph_scan_fits_wall(
        87_400_000, remaining_ms=interactive
    )
    assert graph_read_cost.user_graph_scan_fits_wall(
        87_400_000, remaining_ms=background
    )
    assert not graph_read_cost.user_graph_scan_fits_wall(
        SIX_MONTHS_ON_THE_REFERENCE_TENANT, remaining_ms=background
    )
    # This statement really is dearer per row than the raw filtered graph's;
    # a constant borrowed from that statement would have scheduled six months
    # to a worker that then expires.
    assert (
        graph_read_cost._USER_GRAPH_SCAN_ROWS_PER_MS
        < graph_read_cost._RAW_SCAN_ROWS_PER_MS
    )
    assert graph_read_cost.raw_graph_scan_fits_wall(
        SIX_MONTHS_ON_THE_REFERENCE_TENANT, remaining_ms=background
    )


# --- a probe that stalls past the wall is "cannot answer", on both gates ----
#
# The review of this gate found the one way it still differed from the raw
# gate it mirrors. ``_DeadlineBoundGraphAnalytics.remaining_read_ms`` RAISES
# ``ReadDeadlineExceeded`` once the interactive wall is spent, and the users
# gate asked it twice outside any ``except`` - once for the probe's timeout
# and once for the affordability arithmetic. A probe that stalled past the
# wall therefore left the gate as an exception rather than a verdict. The
# view (``ProjectView.get_users_aggregate_graph_data``) re-raises whatever
# this reader throws, and its outer handler answers HTTP 503
# ``service_unavailable`` - a terminal "retry" for a read that was owed a
# scheduled refresh, and every retry re-probes, stalls and refuses again, so
# the chart never arrives - where the raw gate under the identical stall
# returns its degraded-or-scheduled payload. On the candidate base the same
# stall degraded and scheduled, because ``is_read_budget_error`` accepts the
# deadline error; the gate must not be the one place the deadline escapes.

A_SHORT_WALL_MS = 100
A_STALL_PAST_IT_S = 0.15
# Fits every wall at BOTH statements' rates (553 and 1,796 rows/ms), so the
# two gates diverge only if one of them lets the expired wall escape.
A_WEEK_ON_THE_REFERENCE_TENANT = 2_140_000


class _StalledProbeAnalytics(Analytics):
    """The cost probe answers, but only after the interactive wall is spent."""

    def execute_ch_query(self, query, params, **kwargs):
        if "graph_cost_project_id" in query:
            time.sleep(A_STALL_PAST_IT_S)
        return super().execute_ch_query(query, params, **kwargs)


@pytest.mark.unit
@pytest.mark.parametrize(
    "organization_id",
    [
        pytest.param(None, id="no_background_lane_degrades"),
        pytest.param(ORG_ID, id="background_lane_schedules"),
    ],
)
def test_users_graph_probe_that_stalls_past_the_wall_answers_like_the_raw_gate(
    scheduled, users_reader, organization_id
):
    """The reviewer's reproduction, kept: a stalled probe is a verdict, not a 500.

    Both gates are given the identical shape - a probe that sleeps past a
    100 ms wall and then answers a row count every wall affords - and the
    users gate must return byte-for-byte the payload the raw gate returns:
    the degraded ``read_budget_exceeded`` / ``read_cost_gate`` envelope when
    there is no background lane to schedule to, and the pending envelope of
    one scheduled refresh when there is. Neither issues its statement.
    """

    raw = _StalledProbeAnalytics(estimated_rows=A_WEEK_ON_THE_REFERENCE_TENANT)
    raw_response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=raw,
        project_id=PROJECT_ID,
        filters=[_window(), _attribute_filter()],
        interval="day",
        metric_id="traffic",
        observe_type="trace",
        timeout_ms=A_SHORT_WALL_MS,
        organization_id=organization_id,
        workspace_id=None,
    )

    users = _StalledProbeAnalytics(estimated_rows=A_WEEK_ON_THE_REFERENCE_TENANT)
    try:
        users_response = graph_dispatch.fetch_user_system_metric_graph_ch(
            analytics=users,
            project_id=PROJECT_ID,
            filters=[_window()],
            interval="day",
            metric_id="traffic",
            timeout_ms=A_SHORT_WALL_MS,
            organization_id=organization_id,
            workspace_id=None,
        )
    except ReadDeadlineExceeded:
        pytest.fail(
            "the users gate let the expired wall escape as an exception; "
            "the view refuses that as a 503 instead of scheduling the read"
        )

    assert users_response == raw_response, (
        "a stalled probe must produce the same verdict on both gates"
    )
    assert users_reader == [], "the users statement must not be issued"
    assert raw.statements == [], "the raw statement must not be issued"
    assert len(users.cost_probes) == 1
    assert len(raw.cost_probes) == 1
    if organization_id is None:
        assert scheduled.enqueued == []
        assert users_response["query_status"] == "degraded"
        assert users_response["query_error_code"] == "read_budget_exceeded"
        assert users_response["query_provenance"] == "read_cost_gate"
    else:
        assert users_response["query_status"] == "pending"
        assert users_response["query_refreshing"] is True
        assert {call[0] for call in scheduled.enqueued} == {
            "observe-system-graph",
            "observe-user-system-graph",
        }
        assert len(scheduled.enqueued) == 2, "one refresh per gate, no retry"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("estimated_rows", "expected_status", "expected_code"),
    [
        pytest.param(
            A_WEEK_ON_THE_REFERENCE_TENANT,
            200,
            None,
            id="schedulable_read_returns_the_pending_envelope",
        ),
        pytest.param(
            SIX_MONTHS_ON_THE_REFERENCE_TENANT,
            503,
            "service_unavailable",
            id="hopeless_read_is_refused_as_unavailable",
        ),
    ],
)
def test_the_users_graph_view_never_sees_the_expired_wall_from_the_gate(
    monkeypatch, scheduled, users_reader, estimated_rows, expected_status, expected_code
):
    """The public action, the real dispatcher, and a probe that stalls.

    ``ProjectView.get_users_aggregate_graph_data`` re-raises anything the
    dispatcher throws (``logger.warning(...); raise``) into an outer handler
    that answers HTTP 503 ``service_unavailable``. A 503 is also what a
    DEGRADED payload legitimately becomes at this view, so status alone
    cannot tell the escape from the verdict: the guard records the view's
    own logger and requires that neither of its exception handlers fired -
    the deadline must never reach the view as an exception at all. Only the
    request's PostgreSQL scope, its namespace validation and its project
    lookup are stubbed; the dispatcher, its deadline wrapper, the gate and
    the payload contract are the production code. The action's remaining
    budget is injected as 100 ms so the probe's stall spends it.
    """

    from tracer.views import project as project_view

    seen_by_the_view = []

    class _RecordingLogger:
        def __getattr__(self, method):
            def _record(event=None, *_args, **fields):
                seen_by_the_view.append((method, event, fields))

            return _record

    monkeypatch.setattr(project_view, "logger", _RecordingLogger())

    @contextmanager
    def postgres_scope(_deadline):
        yield

    monkeypatch.setattr(project_view, "graph_action_postgres_budget", postgres_scope)
    monkeypatch.setattr(
        project_view,
        "validate_property_graph_namespace",
        lambda *_args, **_kwargs: None,
    )
    analytics = _StalledProbeAnalytics(estimated_rows=estimated_rows)
    monkeypatch.setattr(project_view, "V2AnalyticsQueryService", lambda: analytics)

    view = project_view.ProjectView()
    monkeypatch.setattr(
        view,
        "_get_project_in_scope",
        lambda _project_id: SimpleNamespace(organization_id=ORG_ID),
    )
    request = SimpleNamespace(
        validated_query_data={"allow_sampled": False, "refresh": False},
        validated_data={
            "project_id": PROJECT_ID,
            "filters": [_window()],
            "interval": "day",
            "req_data_config": {"id": "active_users", "type": "SYSTEM_METRIC"},
        },
        workspace=SimpleNamespace(id=None, is_default=False),
        user=SimpleNamespace(organization=SimpleNamespace(id=ORG_ID)),
    )
    view.request = request

    class _RemainingActionWall:
        def remaining_ms(self, cap_ms=None, *, floor_ms=1):
            return A_SHORT_WALL_MS

    action = unwrap(project_view.ProjectView.get_users_aggregate_graph_data)
    response = action(view, request, _graph_action_deadline=_RemainingActionWall())

    assert response.status_code == expected_status
    if expected_code is not None:
        assert response.data["code"] == expected_code
    else:
        assert response.data["result"]["query_status"] == "pending"
    # The deadline never reached the view as an exception: neither the
    # reader's re-raise nor the outer handler that maps it to a 503 fired.
    handlers_fired = [
        record
        for record in seen_by_the_view
        if record[1]
        in {
            "CH user time-series failed",
            "project_users_graph_unavailable",
            "project_users_graph_failed",
        }
        or record[2].get("error_type") == "ReadDeadlineExceeded"
    ]
    assert handlers_fired == [], handlers_fired
    # The guard holds for the right reason: the gate ran, stalled, and ruled;
    # the statement was never issued on the spent wall.
    assert len(analytics.cost_probes) == 1
    assert users_reader == []
