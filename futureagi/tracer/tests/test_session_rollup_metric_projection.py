"""The session rollup graph reads only the states the requested metric needs."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse import session_graph
from tracer.services.clickhouse.query_builders.session_time_series import (
    SessionRollupTimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.read_budget import ReadDeadline

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"

WINDOW_FILTERS = [
    {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-06-15T00:00:00Z", "2026-09-15T00:00:00Z"],
        },
    }
]

# metric_id -> (merge functions the statement may call, projected metric column)
EXPECTED_PROJECTION: dict[str, tuple[set[str], str]] = {
    "traffic": ({"minMerge"}, "traffic_count"),
    "session_count": ({"minMerge"}, "session_count"),
    "cost": ({"minMerge", "sumMerge"}, "avg_cost"),
    "total_cost": ({"minMerge", "sumMerge"}, "total_cost_sum"),
    "tokens": ({"minMerge", "sumMerge"}, "total_tokens"),
    "total_tokens": ({"minMerge", "sumMerge"}, "total_tokens"),
    "prompt_tokens": ({"minMerge", "sumMerge"}, "prompt_tokens"),
    "input_tokens": ({"minMerge", "sumMerge"}, "prompt_tokens"),
    "completion_tokens": ({"minMerge", "sumMerge"}, "completion_tokens"),
    "output_tokens": ({"minMerge", "sumMerge"}, "completion_tokens"),
    "error_rate": ({"minMerge", "countIfMerge"}, "error_rate"),
    "avg_duration": ({"minMerge", "maxMerge"}, "avg_duration"),
    "latency": ({"minMerge", "quantilesTDigestMerge"}, "avg_latency"),
}


def _build(metric_id: str) -> str:
    builder = SessionRollupTimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=WINDOW_FILTERS,
        interval="day",
        metric_id=metric_id,
    )
    query, _params = builder.build()
    return query


def _merge_calls(query: str) -> set[str]:
    return set(re.findall(r"\b(\w+Merge)\s*\(", query))


@pytest.mark.parametrize("metric_id", sorted(EXPECTED_PROJECTION))
def test_rollup_merges_only_the_states_the_metric_needs(metric_id):
    expected_merges, metric_column = EXPECTED_PROJECTION[metric_id]

    query = _build(metric_id)

    assert _merge_calls(query) == expected_merges
    # The session key and its start are structural: the outer window predicate
    # binds session_start, so minMerge(first_seen) is always read.
    assert "minMerge(sps.first_seen) AS session_start" in query
    assert "count() AS traffic_count" in query
    assert f"AS {metric_column}" in query


@pytest.mark.parametrize("metric_id", sorted(set(EXPECTED_PROJECTION) - {"latency"}))
def test_only_latency_pays_for_the_per_session_tdigest(metric_id):
    assert "quantilesTDigestMerge" not in _build(metric_id)
    assert "sps.latency_q" not in _build(metric_id)


@pytest.mark.parametrize(
    ("metric_id", "state"),
    [
        ("cost", "sumMerge(sps.cost_sum) AS session_total_cost"),
        ("total_cost", "sumMerge(sps.cost_sum) AS session_total_cost"),
        ("tokens", "sumMerge(sps.total_tokens_sum) AS session_total_tokens"),
        (
            "prompt_tokens",
            "sumMerge(sps.prompt_tokens_sum) AS session_prompt_tokens",
        ),
        (
            "completion_tokens",
            "sumMerge(sps.completion_tokens_sum) AS session_completion_tokens",
        ),
        ("error_rate", "countIfMerge(sps.error_count) AS session_error_count"),
        ("avg_duration", "maxMerge(sps.last_seen) AS session_end"),
    ],
)
def test_metric_reads_its_own_state_column(metric_id, state):
    assert state in _build(metric_id)


def test_traffic_projects_two_columns_only():
    query = _build("traffic")

    outer_select = query.split("FROM (")[0]
    assert outer_select.count(" AS ") == 2
    assert "avg_traces_per_session" not in query
    assert "CAST(NULL" not in query


def test_every_rollup_metric_is_answerable():
    for metric_id in session_graph._SESSION_ROLLUP_METRICS:
        assert metric_id in EXPECTED_PROJECTION


def test_unsupported_metric_is_rejected():
    with pytest.raises(ValueError, match="cannot answer"):
        SessionRollupTimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=WINDOW_FILTERS,
            metric_id="avg_traces_per_session",
        )


def test_result_columns_describe_the_projected_statement():
    builder = SessionRollupTimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=WINDOW_FILTERS,
        metric_id="cost",
    )

    assert builder.result_columns == frozenset(
        {"time_bucket", "traffic_count", "avg_cost"}
    )


def test_format_result_defaults_the_columns_the_statement_did_not_compute():
    builder = SessionRollupTimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=WINDOW_FILTERS,
        interval="day",
        metric_id="cost",
    )
    builder.build()
    rows = [
        {
            "time_bucket": datetime(2026, 6, 15),
            "traffic_count": 7,
            "avg_cost": 1.5,
        }
    ]

    formatted = builder.format_result(
        rows, ["time_bucket", "traffic_count", "avg_cost"]
    )

    assert set(formatted) == {
        "latency",
        "tokens",
        "cost",
        "traffic",
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "error_rate",
        "session_count",
        "avg_duration",
        "avg_traces_per_session",
        "total_cost",
    }
    assert formatted["cost"][0]["value"] == 1.5
    assert formatted["traffic"][0]["traffic"] == 7
    # Absent keys must read as zero rather than raising or shifting a column.
    assert formatted["latency"][0]["value"] == 0
    assert formatted["error_rate"][0]["value"] == 0
    assert formatted["session_count"][0]["value"] == 0


class _RecordingAnalytics:
    """Capture the statement and the per-query settings the dispatcher sends."""

    def __init__(self, rows: list[dict[str, Any]], columns: list[str]) -> None:
        self.rows = rows
        self.columns = columns
        self.calls: list[dict[str, Any]] = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        return SimpleNamespace(data=self.rows, columns=self.columns)


def _fetch(metric_id: str, rows, columns):
    analytics = _RecordingAnalytics(rows, columns)
    # Go through the dispatcher's own wrapper: it is what merges the phase
    # settings against the read caps before they reach ClickHouse.
    bounded = session_graph._DeadlineBoundAnalytics(
        analytics,
        ReadDeadline.start(60_000),
    )
    response = session_graph._fetch_rollup_system_metric_graph(
        analytics=bounded,
        project_id=PROJECT_ID,
        filters=WINDOW_FILTERS,
        interval="day",
        metric_id=metric_id,
        started=0.0,
    )
    return analytics, response


def test_dispatch_asks_for_the_requested_metric_and_aggregates_in_order():
    rows = [{"time_bucket": datetime(2026, 6, 15), "traffic_count": 3, "avg_cost": 2.0}]

    analytics, response = _fetch(
        "cost", rows, ["time_bucket", "traffic_count", "avg_cost"]
    )

    call = analytics.calls[0]
    assert call["settings"]["optimize_aggregation_in_order"] == 1
    assert call["settings"]["max_threads"] == 4
    assert "quantilesTDigestMerge" not in call["query"]
    assert "sumMerge(sps.cost_sum)" in call["query"]
    assert response["metric_name"] == "cost"
    assert response["data"][0]["value"] == 2.0
    assert response["data"][0]["primary_traffic"] == 3
    assert response["query_provenance"] == "materialized_rollup"
    assert response["query_exact"] is False


@pytest.mark.parametrize(
    ("metric_id", "column", "value"),
    [
        ("traffic", "traffic_count", 4),
        ("session_count", "session_count", 4),
        ("total_cost", "total_cost_sum", 9.5),
        ("avg_duration", "avg_duration", 12.0),
    ],
)
def test_dispatch_reads_the_column_the_statement_projected(metric_id, column, value):
    """The metrics with no _SYSTEM_METRIC_FIELDS entry read their own key."""

    row = {"time_bucket": datetime(2026, 6, 15), "traffic_count": 4}
    row[column] = value

    _analytics, response = _fetch(metric_id, [row], list(row))

    assert response["data"][0]["value"] == value
    assert response["data"][0]["primary_traffic"] == 4


def test_dispatch_still_rejects_a_statement_missing_its_metric_column():
    from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError

    rows = [{"time_bucket": datetime(2026, 6, 15), "traffic_count": 4}]

    with pytest.raises(BoundedGraphReadError):
        _fetch("cost", rows, ["time_bucket", "traffic_count"])
