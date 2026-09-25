"""Every Observe latency chart publishes the t-digest median, on every path.

The unfiltered graphs merge stored ``quantilesTDigestState`` states, so they
have always shown the median. The filtered, background, ChartsView, session
and users paths used to compute a mean (or a mean of means, or a mean of
medians) under the same "latency" label. These tests pin the rendered SQL of
every live producer:

* each one emits a median helper expression from ``latency_statistic``;
* none emits a mean of latency, a latency sum, or an average of per-group
  latency values;
* each median is wrapped so an empty bucket reads 0 rather than NaN.

The internal result alias stays ``avg_latency`` (owner decision recorded in
the change); these assertions are what guard against a mean producer
creeping back under it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.query_builders.latency_statistic import (
    LATENCY_STATISTIC,
    latency_values_sql,
    median_latency_from_arrays_sql,
    median_latency_from_states_sql,
    median_latency_sql,
)
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)

PROJECT_ID = "3f1d4b7a-0c2e-4a58-9f6b-1d2c3e4f5a6b"
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 7, 8, tzinfo=UTC)

# A mean of latency in any spelling a builder has used.
_MEAN_OF_LATENCY = re.compile(
    r"avg\s*\(\s*(?:rs\.)?(?:latency_ms|session_latency|session_avg_latency"
    r"|user_avg_latency|span_avg_latency)\b"
    r"|avgIf\s*\(\s*(?:rs\.)?latency_ms"
    r"|graph_latency_sum|\blatency_sum\b",
)

_MODEL_FILTER = {
    "column_id": "model",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gpt-4",
        "col_type": "SYSTEM_METRIC",
    },
}


def _assert_no_mean_of_latency(query: str) -> None:
    match = _MEAN_OF_LATENCY.search(query)
    assert match is None, f"mean-of-latency producer survived: {match.group(0)!r}"


def _normalized(query: str) -> str:
    return re.sub(r"\s+", " ", query)


class TestLatencyStatisticHelpers:
    def test_statistic_is_median(self):
        assert LATENCY_STATISTIC == "median"

    def test_row_median_is_guarded_tdigest(self):
        assert median_latency_sql("latency_ms") == (
            "coalesce(ifNotFinite(quantileTDigest(0.5)(latency_ms), NULL), 0)"
        )
        assert median_latency_sql("latency_ms", "x = 1") == (
            "coalesce(ifNotFinite(quantileTDigestIf(0.5)(latency_ms, x = 1), NULL), 0)"
        )

    def test_array_carrier_skips_null_latency_and_uses_int32(self):
        assert latency_values_sql("latency_ms", "c") == (
            "groupArrayIf(toInt32(latency_ms), (c) AND isNotNull(latency_ms))"
        )
        assert median_latency_from_arrays_sql("a") == (
            "coalesce(ifNotFinite(quantileTDigestArray(0.5)(a), NULL), 0)"
        )

    def test_state_merge_keeps_the_stored_level_list(self):
        assert median_latency_from_states_sql("latency_q") == (
            "coalesce(ifNotFinite("
            "(quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q))[1], NULL), 0)"
        )


class TestTraceAndSpanGraphMedian:
    def test_unfiltered_rollup_merges_states_under_the_finite_guard(self):
        query, _ = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID, filters=[], interval="hour"
        ).build()

        assert median_latency_from_states_sql("latency_q") in query
        # Byte-identical level list, so the aggregate projections still match.
        assert "quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q)" in query
        _assert_no_mean_of_latency(query)

    @pytest.mark.parametrize("resolve_span_versions", [True, False])
    def test_filtered_span_graph_is_row_median(self, resolve_span_versions):
        query, _ = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_MODEL_FILTER],
            interval="day",
            exact_snapshot=True,
            resolve_span_versions=resolve_span_versions,
            observe_type="span",
            start_date=START,
            end_date=END,
        ).build()

        assert f"{median_latency_sql('latency_ms')} AS avg_latency" in query
        _assert_no_mean_of_latency(query)

    def test_chartsview_date_only_span_graph_is_row_median(self):
        # ChartsView (read_exact_all_system_metrics) builds this shape with no
        # filter at all; it never reads the rollup.
        query, _ = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[],
            interval="day",
            exact_snapshot=True,
            observe_type="span",
            start_date=START,
            end_date=END,
        ).build()

        assert f"{median_latency_sql('latency_ms')} AS avg_latency" in query
        _assert_no_mean_of_latency(query)

    @pytest.mark.parametrize("resolve_span_versions", [True, False])
    def test_filtered_trace_graph_carries_arrays_not_sums_or_states(
        self, resolve_span_versions
    ):
        query, _ = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_MODEL_FILTER],
            interval="day",
            exact_snapshot=True,
            resolve_span_versions=resolve_span_versions,
            observe_type="trace",
            start_date=START,
            end_date=END,
        ).build()
        flat = _normalized(query)

        assert "groupArrayIf(toInt32(latency_ms), (" in flat
        assert "AND isNotNull(latency_ms)) AS graph_latencies" in flat
        # The packed per-trace tuple carries the array in slot 2 ...
        assert "tuple( graph_bucket, graph_latencies," in flat
        # ... and the outer query takes one median over the union per bucket.
        assert (
            f"{median_latency_from_arrays_sql('tupleElement(graph_output_bucket, 2)')}"
            " AS avg_latency" in flat
        )
        # Memory choice: raw Int32 arrays, never per-(trace, bucket) states.
        assert "quantileTDigestState" not in query
        assert "quantileTDigestStateIf" not in query
        _assert_no_mean_of_latency(query)

    def test_legacy_raw_query_is_median_too(self):
        builder = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID, filters=[], interval="day"
        )
        builder.start_date, builder.end_date = START, END
        query, _ = builder._build_raw_query("1 = 1")

        assert f"{median_latency_sql('latency_ms')} AS avg_latency" in query
        _assert_no_mean_of_latency(query)


# ---------------------------------------------------------------------------
# Session graphs: the pooled median of the sessions that start in the bucket.
# ---------------------------------------------------------------------------

_WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [START.isoformat(), END.isoformat()],
    },
}


class _CapturingAnalytics:
    def __init__(self):
        self.statements: list[str] = []

    def execute_ch_query(self, query, params, **_options):
        from types import SimpleNamespace

        self.statements.append(query)
        return SimpleNamespace(data=[], columns=[])


class TestSessionGraphMedian:
    def _rollup(self, metric_id: str) -> str:
        from tracer.services.clickhouse.query_builders.session_time_series import (
            SessionRollupTimeSeriesQueryBuilder,
        )

        query, _ = SessionRollupTimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_WINDOW_FILTER],
            interval="day",
            metric_id=metric_id,
        ).build()
        return query

    def test_rollup_merges_session_states_into_a_pooled_median(self):
        query = self._rollup("latency")
        flat = _normalized(query)

        assert (
            "quantilesTDigestMergeState(0.5, 0.95, 0.99)(sps.latency_q)"
            " AS session_latency_state" in flat
        )
        assert (
            f"{median_latency_from_states_sql('session_latency_state')} AS avg_latency"
            in flat
        )
        # No per-session median is materialised, so none can be averaged.
        assert "AS session_latency," not in flat
        assert "AS session_latency\n" not in query
        _assert_no_mean_of_latency(query)

    def _exact_statement(self, metric_id: str) -> str:
        from tracer.services.clickhouse import exact_graph_reads

        analytics = _CapturingAnalytics()
        exact_graph_reads.read_exact_session_system_graph(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[_WINDOW_FILTER],
            interval="day",
            metric_id=metric_id,
        )
        (statement,) = analytics.statements
        return statement

    def test_exact_session_graph_is_pooled_median_over_root_latencies(self):
        query = self._exact_statement("latency")
        flat = _normalized(query)

        assert (
            "groupArrayIf(toInt32(rs.latency_ms), isNotNull(rs.latency_ms))"
            " AS session_latencies" in flat
        )
        assert f"{median_latency_from_arrays_sql('session_latencies')} AS value" in flat
        assert "avg(session_avg_latency)" not in query

    @pytest.mark.parametrize("metric_id", ["tokens", "cost", "session_count"])
    def test_only_the_latency_graph_carries_latency_values(self, metric_id):
        assert "session_latencies" not in self._exact_statement(metric_id)

    def test_session_membership_selector_carries_no_latency_values(self):
        from tracer.services.clickhouse import exact_graph_reads

        sql, _ = exact_graph_reads._session_trace_membership_sql(
            project_id=PROJECT_ID,
            filters=[_WINDOW_FILTER],
            start_date=START,
            end_date=END,
            candidate_trace_ids_param="candidate_traces",
        )
        assert "session_latencies" not in sql


# ---------------------------------------------------------------------------
# Users aggregate graph: the pooled median of every span latency of the
# bucket's user traces, carried as values then merged as t-digest states.
# ---------------------------------------------------------------------------


class TestUsersGraphMedian:
    def _statement(self, metric_id: str) -> str:
        from tracer.services.clickhouse import exact_graph_reads

        analytics = _CapturingAnalytics()
        exact_graph_reads.read_exact_user_system_graph(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[_WINDOW_FILTER],
            interval="day",
            metric_id=metric_id,
        )
        (statement,) = analytics.statements
        return statement

    def test_users_latency_is_pooled_median_of_merged_states(self):
        query = self._statement("latency")
        flat = _normalized(query)

        assert (
            "groupArrayIf(toInt32(rs.latency_ms), isNotNull(rs.latency_ms))"
            " AS trace_latencies" in flat
        )
        assert (
            "quantileTDigestStateArray(0.5)(trace_latencies) AS user_latency_state"
            in flat
        )
        assert (
            "coalesce(ifNotFinite(quantileTDigestMerge(0.5)(user_latency_state),"
            " NULL), 0) AS avg_latency" in flat
        )
        _assert_no_mean_of_latency(query)

    @pytest.mark.parametrize(
        "metric_id", ["tokens", "active_users", "total_cost", "error_rate"]
    )
    def test_only_the_latency_graph_carries_latency_values(self, metric_id):
        query = self._statement(metric_id)

        assert "trace_latencies" not in query
        assert "user_latency_state" not in query
        assert "toFloat64(0) AS avg_latency" in _normalized(query)
        _assert_no_mean_of_latency(query)
