"""Hermetic session list/count/graph filter-contract parity tests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.serializers.trace_session import (
    TraceSessionGraphDataRequestSerializer,
    TraceSessionListQuerySerializer,
    TraceSessionRetrieveQuerySerializer,
)
from tracer.services.clickhouse.exact_graph_reads import (
    read_exact_session_system_graph,
)
from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.query_builders.filters import (
    build_numeric_filter_predicate,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 1, 1)
EXCLUDED = datetime(2026, 1, 2, 3, 4, 5, 654321)
END = datetime(2026, 1, 4)


def _time_filter(
    operator: str = "between",
    value: object | None = None,
) -> dict:
    if value is None and operator == "between":
        value = [START.isoformat(), END.isoformat()]
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": operator,
            "filter_value": value,
        },
    }


def _numeric_filter(operator: str, value: object | None) -> dict:
    return {
        "column_id": "traces_count",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": operator,
            "filter_value": value,
        },
    }


class _RecordingAnalytics:
    supports_per_query_read_settings = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), dict(settings)))
        return SimpleNamespace(
            data=[],
            columns=["time_bucket", "value", "primary_traffic"],
        )


def _session_queries(filters: list[dict]):
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        eval_config_ids=[],
        annotation_label_ids=[],
    )
    list_query, list_params = builder.build()
    count_query, count_params = builder.build_count_query()
    analytics = _RecordingAnalytics()
    graph = read_exact_session_system_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="session_count",
    )
    return (list_query, list_params), (count_query, count_params), analytics, graph


def _assert_bound_clause(
    query: str,
    params: dict,
    *,
    expression: str,
    sql_operator: str,
    expected_value: object,
) -> None:
    match = re.search(
        rf"\b{re.escape(expression)}\s+{re.escape(sql_operator)}\s+%\(([^)]+)\)s",
        query,
    )
    assert match, query
    assert params[match.group(1)] == expected_value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operator", "value", "expected_sql", "expected_params"),
    [
        ("in", [2, 3], "session_traces IN %(value)s", {"value": (2, 3)}),
        (
            "not_in",
            [2, 3],
            "session_traces NOT IN %(value)s",
            {"value": (2, 3)},
        ),
        ("in", [], "0 = 1", {}),
        ("not_in", [], "1 = 1", {}),
        ("is_null", None, "session_traces IS NULL", {}),
        ("is_not_null", None, "session_traces IS NOT NULL", {}),
    ],
)
def test_numeric_aggregate_compiler_covers_membership_and_null_contract(
    operator,
    value,
    expected_sql,
    expected_params,
):
    params: dict = {}
    predicate = build_numeric_filter_predicate(
        "session_traces",
        operator,
        value,
        param_prefix="value",
        params=params,
    )
    assert predicate == expected_sql
    assert params == expected_params


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operator", "sql_operator"),
    [("in", "IN"), ("not_in", "NOT IN")],
)
def test_session_list_count_and_exact_graph_share_numeric_membership(
    operator,
    sql_operator,
):
    filters = [_time_filter(), _numeric_filter(operator, [2, 3])]
    list_serializer = TraceSessionListQuerySerializer(
        data={"filters": json.dumps(filters), "page_number": 0, "page_size": 50}
    )
    graph_serializer = TraceSessionGraphDataRequestSerializer(
        data={
            "project_id": PROJECT_ID,
            "filters": filters,
            "req_data_config": {"id": "session_count", "type": "SYSTEM_METRIC"},
        }
    )
    assert list_serializer.is_valid(), list_serializer.errors
    assert graph_serializer.is_valid(), graph_serializer.errors
    assert (
        list_serializer.validated_data["filters"]
        == graph_serializer.validated_data["filters"]
    )

    list_result, count_result, analytics, graph = _session_queries(
        list_serializer.validated_data["filters"]
    )
    graph_query, graph_params, _settings = analytics.calls[0]
    for query, params, expression in (
        (*list_result, "traces_count"),
        (*count_result, "traces_count"),
        (graph_query, graph_params, "session_traces"),
    ):
        _assert_bound_clause(
            query,
            params,
            expression=expression,
            sql_operator=sql_operator,
            expected_value=(2, 3),
        )
    assert graph["query_complete"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_item",
    [
        {
            "column_id": "cost",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "in",
                "filter_value": [1, 2],
            },
        },
        {
            "column_id": "traces_count",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "in",
                "filter_value": [1, 2],
            },
        },
    ],
)
def test_numeric_membership_is_scoped_to_session_aggregates(filter_item):
    serializer = TraceSessionListQuerySerializer(
        data={
            "filters": json.dumps([_time_filter(), filter_item]),
            "page_number": 0,
            "page_size": 50,
        }
    )
    assert not serializer.is_valid()
    assert "filters" in serializer.errors


@pytest.mark.unit
@pytest.mark.parametrize("column_id", [[], {}])
def test_session_surfaces_reject_non_string_column_ids(column_id):
    malformed = {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "in",
            "filter_value": [1, 2],
        },
    }
    serializers = [
        TraceSessionListQuerySerializer(
            data={"filters": json.dumps([malformed]), "page_number": 0}
        ),
        TraceSessionRetrieveQuerySerializer(
            data={"filters": json.dumps([malformed]), "page_number": 0}
        ),
        TraceSessionGraphDataRequestSerializer(
            data={
                "project_id": PROJECT_ID,
                "filters": [malformed],
                "req_data_config": {
                    "id": "session_count",
                    "type": "SYSTEM_METRIC",
                },
            }
        ),
    ]

    for serializer in serializers:
        assert not serializer.is_valid()
        assert "filters" in serializer.errors


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operator", "value", "expected_ranges"),
    [
        (
            "not_equals",
            EXCLUDED.isoformat(),
            ((EXCLUDED, EXCLUDED + timedelta(microseconds=1)),),
        ),
        (
            "not_between",
            [EXCLUDED.isoformat(), (EXCLUDED + timedelta(hours=1)).isoformat()],
            ((EXCLUDED, EXCLUDED + timedelta(hours=1)),),
        ),
    ],
)
def test_session_datetime_complement_matches_list_count_and_exact_graph(
    operator,
    value,
    expected_ranges,
):
    filters = [
        _time_filter(),
        _time_filter(operator, value),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "OK",
            },
        },
    ]
    list_result, count_result, analytics, _graph = _session_queries(filters)
    graph_query, graph_params, _settings = analytics.calls[0]
    for query, params in (*[list_result, count_result], (graph_query, graph_params)):
        assert "fromUnixTimestamp64Micro" in query
        for lower, upper in expected_ranges:
            assert _unix_microseconds(lower) in params.values()
            assert _unix_microseconds(upper) in params.values()
    assert "exact_session_time_exclusion_0_start" in graph_query
    assert "exact_session_scalar_time_exclusion_0_start" in graph_query


@pytest.mark.unit
def test_session_datetime_is_null_is_exact_empty_without_database_read():
    filters = [_time_filter(), _time_filter("is_null", None)]
    list_result, count_result, analytics, graph = _session_queries(filters)
    assert "0 = 1" in list_result[0]
    assert "0 = 1" in count_result[0]
    assert analytics.calls == []
    assert graph["data"] == []
    assert graph["query_complete"] is True
    assert graph["query_count"] == 0
