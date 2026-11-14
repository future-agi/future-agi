"""Root-derived trace names stay consistent across list and graph filters."""

import pytest

from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
    partition_span_filter_plans,
)
from tracer.services.clickhouse.trace_name_sql import TRACE_NAME_SQL
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)

pytestmark = pytest.mark.unit


def _leaf(op="equals", value="Root Name"):
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def test_trace_name_expression_keeps_case_insensitive_search():
    builder = ClickHouseFilterBuilder(project_id="00000000-0000-0000-0000-000000000001")
    sql, params = builder.translate([_leaf("contains", "rOoT")])
    assert f"positionUTF8(lowerUTF8(toString({TRACE_NAME_SQL}))" in sql
    assert "parent_span_id IS NULL OR parent_span_id = ''" in sql
    assert params["col_1"] == "rOoT"


def test_span_mode_resolves_root_without_accepting_child_name():
    builder = ClickHouseFilterBuilderV2(
        project_id="00000000-0000-0000-0000-000000000001",
        query_mode="span",
    )
    sql, params = builder.translate([_leaf()])
    assert (
        "(project_id, trace_id) IN (SELECT project_id, trace_id FROM spans FINAL" in sql
    )
    assert "WHERE is_deleted = 0" in sql
    # Whole storage hours before FINAL: an exact start_time cut in PREWHERE
    # could drop the live version of a root corrected within its hour.
    assert (
        "toStartOfHour(start_time) > %(start_date)s - INTERVAL 1 DAY - INTERVAL 1 HOUR"
        in sql
    )
    assert "toStartOfHour(start_time) <= %(end_date)s" in sql
    # ...and the exact window on the live root only after FINAL (WHERE).
    prewhere, where = sql.split("WHERE is_deleted = 0", 1)
    assert "start_time >= %(start_date)s" not in prewhere
    assert "AND start_time >= %(start_date)s - INTERVAL 1 DAY" in where
    assert "AND start_time < %(end_date)s" in where
    assert f"lowerUTF8(toString({TRACE_NAME_SQL}))" in sql
    assert params["col_1"] == "root name"


def test_span_list_delegates_trace_name_to_root_membership():
    plans, residual = partition_span_filter_plans([_leaf()])
    assert plans == []
    assert residual == [_leaf()]
    root_plan = compile_span_filter_plans([_leaf()])[0]
    assert root_plan.scope == "root"
    assert TRACE_NAME_SQL in root_plan.aggregates[0]


def test_latest_trace_root_snapshot_replays_expression():
    plan = compile_trace_filter_plans([_leaf()])[0]
    assert plan.scope == "root"
    assert f"argMax({TRACE_NAME_SQL}, _peerdb_version)" in plan.aggregates[0]
    assert TRACE_NAME_SQL in plan.seed_predicate


def test_exact_graph_trace_root_only_and_span_root_membership():
    trace = compile_exact_graph_row_predicates(
        [_leaf()],
        project_id="00000000-0000-0000-0000-000000000001",
        observe_type="trace",
    )
    assert "parent_span_id" in trace.predicates[0]
    assert TRACE_NAME_SQL in trace.predicates[0]
    assert "FROM spans" not in trace.predicates[0]
    span = compile_exact_graph_row_predicates(
        [_leaf()],
        project_id="00000000-0000-0000-0000-000000000001",
        observe_type="span",
    )
    assert "SELECT project_id, trace_id FROM spans FINAL" in span.predicates[0]
