"""Adversarial guards for trace/span list string filters.

These tests intentionally inspect the compiled ClickHouse query.  They pin the
properties that keep the US-scale list endpoints bounded without changing the
meaning of selective filters.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)


def _date_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _string_filter(
    value,
    *,
    op: str = "equals",
    column_id: str = "customer.segment",
    col_type: str = "SPAN_ATTRIBUTE",
) -> dict:
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _long_window_filters(string_filter: dict) -> list[dict]:
    return [
        _date_filter(
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2026, 7, 30, tzinfo=UTC),
        ),
        string_filter,
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder_cls", "is_trace"),
    [(TraceListQueryBuilderV2, True), (SpanListQueryBuilderV2, False)],
)
@pytest.mark.parametrize(
    ("op", "value", "expected_index_expr"),
    [
        (
            "equals",
            "selective-account-4815162342",
            "has(arrayMap(x -> lower(x), mapValues(attrs_string))",
        ),
        (
            "in",
            ["common", "selective-account-4815162342"],
            "hasAny(arrayMap(x -> lower(x), mapValues(attrs_string))",
        ),
        (
            "contains",
            "synthetic_prompt",
            "positionUTF8(lowerUTF8",
        ),
    ],
)
def test_long_window_string_filters_are_time_scoped_bounded_and_topk(
    builder_cls, is_trace, op, value, expected_index_expr
):
    builder = builder_cls(
        project_id=str(uuid4()),
        filters=_long_window_filters(_string_filter(value, op=op)),
        page_number=0,
        page_size=25,
    )

    sql, params = builder.build()
    compact_sql = " ".join(sql.split())

    assert params["start_date"] == datetime(2025, 1, 1)
    assert params["end_date"] == datetime(2026, 7, 30)
    assert "start_time >= %(start_date)s" in compact_sql
    assert "start_time < %(end_date)s" in compact_sql
    assert expected_index_expr in compact_sql
    if op == "contains":
        assert "arrayStringConcat" not in compact_sql
    assert "LIMIT %(limit)s" in compact_sql
    assert params["limit"] == 50
    assert "LIMIT 1 BY" not in compact_sql

    if is_trace:
        # Trace attributes may live on a child span.  The membership probe must
        # use the same bounded window as the root-span page scan.
        assert "trace_id IN (SELECT trace_id FROM spans" in compact_sql
        assert (
            "start_time >= %(start_date)s - INTERVAL 1 DAY "
            "AND start_time < %(end_date)s + INTERVAL 1 DAY"
        ) in compact_sql
    else:
        # A span list applies the predicate to the row itself; building a
        # project-wide trace-id set would add work and change semantics.
        assert "trace_id IN (SELECT trace_id FROM spans" not in compact_sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
def test_unicode_exact_attribute_filter_uses_utf8_fold_without_ascii_bloom(
    builder_cls,
):
    builder = builder_cls(
        project_id=str(uuid4()),
        filters=_long_window_filters(_string_filter("ÉLITE-東京")),
    )

    sql, params = builder.build()

    assert "lowerUTF8(attrs_string['customer.segment'])" in sql
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in sql
    assert "élite-東京" in params.values()


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
def test_unicode_physical_string_filter_uses_utf8_fold(builder_cls):
    builder = builder_cls(
        project_id=str(uuid4()),
        filters=_long_window_filters(
            _string_filter(
                ["ÉLITE", "東京"],
                op="in",
                column_id="service_name",
                col_type="SYSTEM_METRIC",
            )
        ),
    )

    sql, params = builder.build()

    assert "lowerUTF8(service_name) IN" in sql
    assert ("élite", "東京") in params.values()


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
def test_empty_string_is_an_explicit_present_attribute_match(builder_cls):
    builder = builder_cls(
        project_id=str(uuid4()),
        filters=_long_window_filters(_string_filter("")),
    )

    sql, params = builder.build()

    assert "mapContains(attrs_string, 'customer.segment')" in sql
    assert "attrs_string['customer.segment']" in sql
    assert "" in params.values()


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
def test_empty_filter_list_does_not_emit_attribute_scan(builder_cls):
    sql, _ = builder_cls(
        project_id=str(uuid4()),
        filters=[],
        page_number=0,
        page_size=25,
    ).build()

    assert "mapContains(attrs_string" not in sql
    assert "mapValues(attrs_string)" not in sql


@pytest.mark.unit
def test_list_paths_have_no_postgres_telemetry_fallback():
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.trace import TraceView

    trace_source = inspect.getsource(TraceView._list_traces_of_session_clickhouse)
    span_source = inspect.getsource(ObservationSpanView._list_spans_clickhouse)

    assert "Trace.objects" not in trace_source
    assert "EvalLogger.objects" not in trace_source
    assert "ObservationSpan.objects" not in span_source
    assert "EvalLogger.objects" not in span_source


@pytest.mark.unit
@pytest.mark.parametrize(
    "method",
    [
        pytest.param(
            "tracer.views.trace.TraceView._list_traces_clickhouse",
            id="trace-list",
        ),
        pytest.param(
            "tracer.views.trace.TraceView._list_traces_of_session_clickhouse",
            id="session-trace-list",
        ),
        pytest.param(
            "tracer.views.observation_span.ObservationSpanView._list_spans_clickhouse",
            id="span-list",
        ),
    ],
)
def test_every_list_clickhouse_read_has_hard_budget_and_settings(method):
    module_path, class_name, method_name = method.rsplit(".", 2)
    module = __import__(module_path, fromlist=[class_name])
    function = getattr(getattr(module, class_name), method_name)
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))

    execute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_ch_query"
    ]
    assignments = {
        node.targets[0].id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    def hard_timeout_cap(expr, seen=frozenset()):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, int):
            return expr.value
        if isinstance(expr, ast.Name) and expr.id not in seen:
            assigned = assignments.get(expr.id)
            if assigned is not None:
                return hard_timeout_cap(assigned, seen | {expr.id})
        if (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "min"
        ):
            caps = [
                cap
                for arg in expr.args
                if (cap := hard_timeout_cap(arg, seen)) is not None
            ]
            return min(caps) if caps else None
        return None

    assert execute_calls
    for call in execute_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        timeout_cap = hard_timeout_cap(keywords["timeout_ms"])
        assert timeout_cap is not None
        assert timeout_cap <= 750
        assert "settings" in keywords
