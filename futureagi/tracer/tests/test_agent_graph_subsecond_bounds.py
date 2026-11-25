"""Agent Graph keeps sub-second request bounds.

Whole-second windows keep the historical statement text; a fractional bound
is sent as epoch microseconds so the driver cannot truncate it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.v2.query_builders.agent_graph import (
    AgentGraphQueryBuilderV2,
)
from tracer.tests import test_graph_native_cost_coverage as graph_fixtures
from tracer.tests.test_graph_native_cost_coverage import (
    PROJECT,
    NativeAnalytics,
    insert_span,
)

native_graph_db = graph_fixtures.native_graph_db

DEV_OUTPUT_WINDOW = "start_time >= %(start_date)s AND start_time < %(end_date)s"
DEV_PHYSICAL_WINDOW = (
    "\n                  AND toStartOfHour(start_time) >= "
    "toStartOfHour(toDateTime64(%(start_date)s, 6, 'UTC'))"
    "\n                  AND toStartOfHour(start_time) < "
    "toDateTime64(%(end_date)s, 6, 'UTC')"
)
EXACT_START = "fromUnixTimestamp64Micro(%(start_date_exact_us)s, 'UTC')"
EXACT_END = "fromUnixTimestamp64Micro(%(end_date_exact_us)s, 'UTC')"


def _window_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _name_filter() -> dict:
    return {
        "column_id": "span_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "chat",
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize("filtered", [False, True])
def test_whole_second_agent_graph_keeps_dev_statement(filtered):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    filters = [_window_filter(start, start + timedelta(hours=1))]
    if filtered:
        filters.append(_name_filter())

    query, params = AgentGraphQueryBuilderV2(
        project_id=PROJECT, filters=filters
    ).build()

    assert DEV_OUTPUT_WINDOW in query
    assert "_exact_us" not in query
    assert not [name for name in params if name.endswith("_exact_us")]
    assert (DEV_PHYSICAL_WINDOW in query) is not filtered


@pytest.mark.unit
def test_fractional_agent_graph_bounds_are_exact():
    start = datetime(2026, 10, 20, 12, 59, 59, 500000)
    end = datetime(2026, 10, 20, 13, 0, 0, 1)

    query, params = AgentGraphQueryBuilderV2(
        project_id=PROJECT, filters=[_window_filter(start, end)]
    ).build()

    assert f"start_time >= {EXACT_START} AND start_time < {EXACT_END}" in query
    assert DEV_OUTPUT_WINDOW not in query
    assert (
        f"toStartOfHour(start_time) >= toStartOfHour(toDateTime64({EXACT_START}, 6, 'UTC'))"
        in query
    )
    assert f"toStartOfHour(start_time) < toDateTime64({EXACT_END}, 6, 'UTC')" in query
    assert params["start_date_exact_us"] == _unix_microseconds(start)
    assert params["end_date_exact_us"] == _unix_microseconds(end)


@pytest.mark.integration
def test_agent_graph_counts_the_exact_window_across_an_hour_edge(native_graph_db):
    client = native_graph_db
    edge = datetime(2026, 10, 20, 13, tzinfo=UTC)
    # Inside [floor(lo), lo): outside the window.
    insert_span(
        client,
        span_id="pre",
        name="pre-node",
        start=edge - timedelta(milliseconds=750),
    )
    # The first microsecond of the next hour sits inside [lo, hi).
    insert_span(client, span_id="edge", name="edge-node", start=edge)
    # Exactly the exclusive upper bound.
    insert_span(
        client, span_id="late", name="late-node", start=edge + timedelta(microseconds=1)
    )

    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=NativeAnalytics(client),
        project_id=PROJECT,
        filters=[
            _window_filter(
                edge - timedelta(milliseconds=500),
                edge + timedelta(microseconds=1),
            )
        ],
    )

    assert {node["name"]: node["span_count"] for node in graph["nodes"]} == {
        "edge-node": 1
    }
