"""Exact datetime-filter contract for bounded trace/span/session lists.

These are compiler and request-validation correctness tests. They do not make
database calls and their runtime is not a performance benchmark.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework import serializers

from tracer.models.eval_task import RowType, RunType
from tracer.selectors.eval_tasks import row_resolver
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.serializers.filters import (
    BOUNDED_FILTER_LIST_QUERY_PARAM_SCHEMA,
    BOUNDED_FILTER_LIST_SCHEMA,
    BOUNDED_LIST_DATETIME_FILTER_OPS,
    BoundedFilterListQueryParamField,
    EvalTaskFiltersField,
    ObserveGraphDataRequestSerializer,
)
from tracer.serializers.observation_span import (
    SpanListQuerySerializer,
    SpanObserveListQuerySerializer,
)
from tracer.serializers.trace import (
    TraceListQuerySerializer,
    TraceObserveListQuerySerializer,
)
from tracer.serializers.trace_session import TraceSessionListQuerySerializer
from tracer.services.clickhouse.bounded_graph_reads import _filters_for_window
from tracer.services.clickhouse.graph_dispatch import fetch_system_metric_graph_ch
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

LOWER = datetime(2026, 1, 1, 0, 0, 0, 123456)
VALUE = datetime(2026, 2, 2, 3, 4, 5, 654321)
UPPER = datetime(2026, 3, 3, 6, 7, 8, 987654)
ONE_MICROSECOND = timedelta(microseconds=1)
EPOCH = datetime(1970, 1, 1)


def _micros(value: datetime) -> int:
    delta = value - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _datetime_filter(
    operator: str,
    value=...,
    *,
    column_id: str = "created_at",
    filter_type: str = "datetime",
) -> dict:
    config = {
        "col_type": "SYSTEM_METRIC",
        "filter_type": filter_type,
        "filter_op": operator,
    }
    if value is not ...:
        config["filter_value"] = value
    return {"column_id": column_id, "filter_config": config}


def _iso(value: datetime) -> str:
    return f"{value.isoformat()}Z"


@pytest.mark.parametrize(
    ("operator", "value", "companion", "expected_start", "expected_end"),
    [
        ("equals", _iso(VALUE), None, VALUE, VALUE + ONE_MICROSECOND),
        (
            "greater_than",
            _iso(VALUE),
            _datetime_filter("less_than", _iso(UPPER)),
            VALUE + ONE_MICROSECOND,
            UPPER,
        ),
        (
            "greater_than_or_equal",
            _iso(VALUE),
            _datetime_filter("less_than", _iso(UPPER)),
            VALUE,
            UPPER,
        ),
        (
            "less_than",
            _iso(VALUE),
            _datetime_filter("greater_than_or_equal", _iso(LOWER)),
            LOWER,
            VALUE,
        ),
        (
            "less_than_or_equal",
            _iso(VALUE),
            _datetime_filter("greater_than_or_equal", _iso(LOWER)),
            LOWER,
            VALUE + ONE_MICROSECOND,
        ),
        (
            "between",
            [_iso(LOWER), _iso(UPPER)],
            None,
            LOWER,
            UPPER,
        ),
        (
            "is_not_null",
            ...,
            _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
            LOWER,
            UPPER,
        ),
    ],
)
def test_strict_time_range_compiles_every_supported_operator_exactly(
    operator,
    value,
    companion,
    expected_start,
    expected_end,
):
    filters = [_datetime_filter(operator, value)]
    if companion is not None:
        filters.append(companion)

    assert BaseQueryBuilder.parse_time_range(filters, strict=True) == (
        expected_start,
        expected_end,
    )


@pytest.mark.parametrize(
    ("operator", "value", "expected_exclusions"),
    [
        ("not_equals", _iso(VALUE), ((VALUE, VALUE + ONE_MICROSECOND),)),
        ("not_between", [_iso(VALUE), _iso(UPPER)], ((VALUE, UPPER),)),
    ],
)
def test_strict_time_range_preserves_complements_inside_finite_base(
    operator,
    value,
    expected_exclusions,
):
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter(operator, value),
    ]

    analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(filters)
    predicate, params = BaseQueryBuilder.bounded_datetime_exclusion_sql(filters)

    assert (analyzed.start, analyzed.end) == (LOWER, UPPER)
    assert analyzed.exclusions == expected_exclusions
    assert analyzed.empty is False
    assert (
        "start_time < fromUnixTimestamp64Micro(%(bounded_datetime_0_start)s)"
        in predicate
    )
    assert (
        "start_time >= fromUnixTimestamp64Micro(%(bounded_datetime_0_end)s)"
        in predicate
    )
    assert params == {
        "bounded_datetime_0_start": _micros(expected_exclusions[0][0]),
        "bounded_datetime_0_end": _micros(expected_exclusions[0][1]),
    }


def test_is_null_is_exact_empty_for_non_null_physical_time_column():
    filters = [_datetime_filter("is_null")]

    start, end = BaseQueryBuilder.parse_time_range(filters, strict=True)
    predicate, params = BaseQueryBuilder.bounded_datetime_exclusion_sql(filters)

    assert start == end
    assert predicate == "0 = 1"
    assert params == {}


def test_strict_time_range_intersects_multiple_filters_and_alias_columns():
    filters = [
        _datetime_filter("greater_than_or_equal", _iso(LOWER)),
        _datetime_filter("greater_than", _iso(VALUE), column_id="start_time"),
        _datetime_filter("less_than_or_equal", _iso(UPPER)),
        _datetime_filter(
            "less_than", _iso(UPPER + timedelta(days=1)), column_id="start_time"
        ),
    ]

    assert BaseQueryBuilder.parse_time_range(filters, strict=True) == (
        VALUE + ONE_MICROSECOND,
        UPPER + ONE_MICROSECOND,
    )


def test_strict_time_range_returns_exact_empty_intersection():
    filters = [
        _datetime_filter("greater_than_or_equal", _iso(UPPER)),
        _datetime_filter("less_than", _iso(LOWER)),
    ]

    start, end = BaseQueryBuilder.parse_time_range(filters, strict=True)

    assert start == end == UPPER


def test_mixed_positive_and_multiple_complements_are_conjunctive():
    midpoint = LOWER + (UPPER - LOWER) / 2
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter("not_between", [_iso(LOWER), _iso(midpoint)]),
        _datetime_filter("not_between", [_iso(midpoint), _iso(UPPER)]),
    ]

    analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(filters)
    predicate, params = BaseQueryBuilder.bounded_datetime_exclusion_sql(filters)

    assert analyzed.empty is True
    assert analyzed.start == analyzed.end == LOWER
    assert predicate == "0 = 1"
    assert params == {}


def test_equals_and_not_equals_same_datetime_is_exact_empty():
    filters = [
        _datetime_filter("equals", _iso(VALUE)),
        _datetime_filter("not_equals", _iso(VALUE)),
    ]

    assert BaseQueryBuilder.parse_time_range(filters, strict=True) == (VALUE, VALUE)


def test_list_serializer_accepts_equal_between_as_exact_empty_contradiction():
    payload = json.dumps([_datetime_filter("between", [_iso(VALUE), _iso(VALUE)])])

    result = BoundedFilterListQueryParamField().run_validation(payload)

    assert result[0]["filter_config"]["filter_op"] == "between"


def test_zero_width_not_between_is_a_no_op_inside_base_window():
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter("not_between", [_iso(VALUE), _iso(VALUE)]),
    ]

    assert BaseQueryBuilder.parse_time_range(filters, strict=True) == (LOWER, UPPER)
    assert BaseQueryBuilder.bounded_datetime_exclusion_sql(filters) == ("", {})


@pytest.mark.parametrize(
    ("filter_item", "expected_end"),
    [
        (
            _datetime_filter("less_than_or_equal", _iso(VALUE)),
            VALUE + ONE_MICROSECOND,
        ),
        (_datetime_filter("is_not_null"), None),
    ],
)
def test_strict_time_range_retains_safe_legacy_default_lower_bound(
    filter_item,
    expected_end,
):
    if expected_end is not None:
        recent_value = datetime.utcnow() - timedelta(days=1)
        filter_item["filter_config"]["filter_value"] = _iso(recent_value)
        expected_end = recent_value + ONE_MICROSECOND
    before = datetime.utcnow()
    start, end = BaseQueryBuilder.parse_time_range([filter_item], strict=True)
    after = datetime.utcnow()

    assert before - timedelta(days=30) <= start <= after - timedelta(days=30)
    if expected_end is None:
        assert before <= end <= after
    else:
        assert end == expected_end


def test_strict_lower_only_filter_uses_request_time_now_as_upper_bound():
    before = datetime.utcnow()
    start, end = BaseQueryBuilder.parse_time_range(
        [_datetime_filter("greater_than", _iso(VALUE))], strict=True
    )
    after = datetime.utcnow()

    assert start == VALUE + ONE_MICROSECOND
    assert before <= end <= after


def test_strict_time_range_normalizes_offset_timestamp_to_utc():
    offset_value = "2026-02-02T08:34:05.654321+05:30"

    start, end = BaseQueryBuilder.parse_time_range(
        [_datetime_filter("equals", offset_value)], strict=True
    )

    assert start == VALUE
    assert end == VALUE + ONE_MICROSECOND


def test_positive_datetime_filters_emit_no_residual_sql_or_params():
    filters = [
        _datetime_filter("greater_than_or_equal", _iso(LOWER)),
        _datetime_filter("less_than", _iso(UPPER)),
    ]

    assert BaseQueryBuilder.bounded_datetime_exclusion_sql(filters) == ("", {})


@pytest.mark.parametrize(
    "filter_item, message",
    [
        (_datetime_filter("equals", "not-a-date"), "valid ISO-8601"),
        (
            _datetime_filter("between", [_iso(UPPER), _iso(LOWER)]),
            "start timestamp",
        ),
        (
            _datetime_filter("not_between", [_iso(UPPER), _iso(LOWER)]),
            "start timestamp",
        ),
        (_datetime_filter("between", [_iso(LOWER)]), "requires two"),
        (_datetime_filter("not_between", [_iso(LOWER)]), "requires two"),
        (_datetime_filter("not_equals", "not-a-date"), "valid ISO-8601"),
        (_datetime_filter("equals", _iso(VALUE), filter_type="text"), "datetime"),
        (
            _datetime_filter("equals", "9999-12-31T23:59:59.999999Z"),
            "DateTime64",
        ),
    ],
)
def test_strict_time_range_rejects_malformed_datetime_shapes(filter_item, message):
    with pytest.raises(ValueError, match=message):
        BaseQueryBuilder.parse_time_range([filter_item], strict=True)


@pytest.mark.parametrize(
    "operator",
    sorted(BOUNDED_LIST_DATETIME_FILTER_OPS),
)
def test_bounded_list_field_accepts_every_advertised_datetime_operator(operator):
    if operator == "between":
        value = [_iso(LOWER), _iso(UPPER)]
        companions = []
    elif operator == "not_between":
        value = [_iso(LOWER), _iso(UPPER)]
        companions = [_datetime_filter("between", [_iso(LOWER), _iso(UPPER)])]
    elif operator in {"is_null", "is_not_null"}:
        value = ...
        companions = []
    elif operator in {"greater_than", "greater_than_or_equal"}:
        value = _iso(VALUE)
        companions = [_datetime_filter("less_than", _iso(UPPER))]
    elif operator in {"less_than", "less_than_or_equal"}:
        value = _iso(VALUE)
        companions = []
    else:
        value = _iso(VALUE)
        companions = []
    payload = [_datetime_filter(operator, value), *companions]

    assert BoundedFilterListQueryParamField().run_validation(json.dumps(payload))


@pytest.mark.parametrize(
    ("serializer_class", "required_query"),
    [
        (TraceObserveListQuerySerializer, {}),
        (SpanObserveListQuerySerializer, {}),
        (TraceSessionListQuerySerializer, {}),
        (
            TraceListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
        (
            SpanListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
    ],
)
@pytest.mark.parametrize(
    "filter_item",
    [
        _datetime_filter("less_than", _iso(VALUE)),
        _datetime_filter("less_than_or_equal", _iso(VALUE)),
        _datetime_filter("is_not_null"),
    ],
)
def test_every_list_serializer_retains_safe_legacy_datetime_shapes(
    serializer_class,
    required_query,
    filter_item,
):
    serializer = serializer_class(
        data={**required_query, "filters": json.dumps([filter_item])}
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    ("serializer_class", "required_query"),
    [
        (TraceObserveListQuerySerializer, {}),
        (SpanObserveListQuerySerializer, {}),
        (TraceSessionListQuerySerializer, {}),
        (
            TraceListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
        (
            SpanListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
    ],
)
@pytest.mark.parametrize("page_size", [1, 500])
def test_every_list_serializer_preserves_documented_page_size_bounds(
    serializer_class,
    required_query,
    page_size,
):
    serializer = serializer_class(
        data={
            **required_query,
            "filters": json.dumps(
                [
                    _datetime_filter("less_than", _iso(VALUE)),
                    {
                        "column_id": "final_status",
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": "text",
                            "filter_op": "equals",
                            "filter_value": "Rejected",
                        },
                    },
                ]
            ),
            "page_number": 0,
            "page_size": page_size,
        }
    )

    assert serializer.fields["page_size"].max_value == 500
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    ("serializer_class", "required_query"),
    [
        (TraceObserveListQuerySerializer, {}),
        (SpanObserveListQuerySerializer, {}),
        (TraceSessionListQuerySerializer, {}),
        (
            TraceListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
        (
            SpanListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
    ],
)
@pytest.mark.parametrize("page_size", [0, 501])
def test_every_list_serializer_rejects_page_size_outside_documented_bounds(
    serializer_class,
    required_query,
    page_size,
):
    serializer = serializer_class(data={**required_query, "page_size": page_size})

    assert not serializer.is_valid()
    assert "page_size" in serializer.errors


@pytest.mark.parametrize("operator", ["not_equals", "not_between", "is_null"])
def test_bounded_list_field_accepts_datetime_complement_operator(operator):
    value = (
        [_iso(LOWER), _iso(UPPER)]
        if operator == "not_between"
        else (_iso(VALUE) if operator == "not_equals" else ...)
    )

    assert BoundedFilterListQueryParamField().run_validation(
        json.dumps([_datetime_filter(operator, value)])
    )


@pytest.mark.parametrize(
    ("serializer_class", "required_query"),
    [
        (TraceObserveListQuerySerializer, {}),
        (SpanObserveListQuerySerializer, {}),
        (TraceSessionListQuerySerializer, {}),
        (
            TraceListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
        (
            SpanListQuerySerializer,
            {"project_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ),
    ],
)
def test_every_trace_span_session_list_serializer_accepts_disjoint_datetime_filter(
    serializer_class,
    required_query,
):
    payload = json.dumps([_datetime_filter("not_between", [_iso(LOWER), _iso(UPPER)])])
    serializer = serializer_class(data={**required_query, "filters": payload})

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize("operator", ["not_equals", "not_between", "is_null"])
def test_graph_body_accepts_every_datetime_complement_operator(operator):
    value = (
        [_iso(LOWER), _iso(UPPER)]
        if operator == "not_between"
        else (_iso(VALUE) if operator == "not_equals" else ...)
    )
    serializer = ObserveGraphDataRequestSerializer(
        data={
            "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "filters": [_datetime_filter(operator, value)],
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "filter_item",
    [
        _datetime_filter("less_than", _iso(VALUE)),
        _datetime_filter("less_than_or_equal", _iso(VALUE)),
        _datetime_filter("is_not_null"),
    ],
)
def test_graph_body_retains_safe_legacy_datetime_shapes(filter_item):
    serializer = ObserveGraphDataRequestSerializer(
        data={
            "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "filters": [filter_item],
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize("operator", ["not_equals", "not_between", "is_null"])
@pytest.mark.parametrize("filter_list_key", ["filters", "span_attributes_filters"])
def test_eval_task_filter_lists_accept_datetime_complement_operator(
    operator,
    filter_list_key,
):
    value = (
        [_iso(LOWER), _iso(UPPER)]
        if operator == "not_between"
        else (_iso(VALUE) if operator == "not_equals" else ...)
    )

    assert EvalTaskFiltersField().run_validation(
        {filter_list_key: [_datetime_filter(operator, value)]}
    )


@pytest.mark.parametrize("filter_list_key", ["filters", "span_attributes_filters"])
@pytest.mark.parametrize(
    "filter_item",
    [
        _datetime_filter("less_than", _iso(VALUE)),
        _datetime_filter("less_than_or_equal", _iso(VALUE)),
        _datetime_filter("is_not_null"),
    ],
)
def test_eval_task_filter_lists_retain_safe_legacy_datetime_shapes(
    filter_item,
    filter_list_key,
):
    result = EvalTaskFiltersField().run_validation({filter_list_key: [filter_item]})

    assert result[filter_list_key]


def test_eval_task_top_level_time_wrappers_normalize_offsets_to_utc():
    result = EvalTaskFiltersField().run_validation(
        {
            "date_range": [
                "2026-02-02T08:34:05.654321+05:30",
                "2026-02-03T08:34:05.654321+05:30",
            ],
            "created_at": "2026-02-02T09:34:05.654321+05:30",
        }
    )

    assert result["date_range"] == [
        "2026-02-02T03:04:05.654321Z",
        "2026-02-03T03:04:05.654321Z",
    ]
    assert result["created_at"] == "2026-02-02T04:04:05.654321Z"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"date_range": ["not-a-date", "2026-02-03T00:00:00Z"]},
            "valid ISO-8601",
        ),
        (
            {
                "date_range": [
                    "2026-02-03T00:00:00Z",
                    "2026-02-02T00:00:00Z",
                ]
            },
            "start timestamp",
        ),
        ({"created_at": "not-a-date"}, "valid ISO-8601"),
        ({"created_at": None}, "valid ISO-8601"),
    ],
)
def test_eval_task_top_level_time_wrappers_reject_malformed_or_empty_ranges(
    payload,
    message,
):
    with pytest.raises(serializers.ValidationError, match=message):
        EvalTaskFiltersField().run_validation(payload)


def test_eval_task_equal_date_range_is_a_valid_exact_empty_selection():
    value = "2026-02-02T00:00:00Z"

    result = EvalTaskFiltersField().run_validation({"date_range": [value, value]})

    assert result["date_range"] == [value, value]


def test_eval_task_mixed_positive_wrappers_may_form_exact_empty_selection():
    result = EvalTaskFiltersField().run_validation(
        {
            "date_range": [
                "2026-02-02T00:00:00Z",
                "2026-02-03T00:00:00Z",
            ],
            "created_at": "2026-02-04T00:00:00Z",
        }
    )

    assert result["created_at"] == "2026-02-04T00:00:00Z"


def test_list_filter_openapi_contract_advertises_all_bounded_datetime_operators():
    for schema in (
        BOUNDED_FILTER_LIST_QUERY_PARAM_SCHEMA,
        BOUNDED_FILTER_LIST_SCHEMA,
    ):
        assert schema["x-boundedDatetimeOperators"] == sorted(
            BOUNDED_LIST_DATETIME_FILTER_OPS
        )
        for operator in BOUNDED_LIST_DATETIME_FILTER_OPS:
            assert operator in schema["description"]
        assert "exact empty result" in schema["description"]


@pytest.mark.parametrize(
    "builder_class",
    [TraceListQueryBuilder, SpanListQueryBuilder, SessionListQueryBuilder],
)
def test_direct_list_builders_compile_not_equals_as_microsecond_exclusion(
    builder_class,
):
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter("not_equals", _iso(VALUE)),
    ]
    builder = builder_class(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", filters=filters
    )

    query, params = builder.build()

    assert "start_time < fromUnixTimestamp64Micro(%(" in query
    assert "start_time >= fromUnixTimestamp64Micro(%(" in query
    assert _micros(VALUE) in params.values()
    assert _micros(VALUE + ONE_MICROSECOND) in params.values()


@pytest.mark.parametrize(
    "builder_class",
    [TraceListQueryBuilder, SpanListQueryBuilder, SessionListQueryBuilder],
)
@pytest.mark.parametrize(
    "filter_item",
    [
        _datetime_filter("less_than", _iso(VALUE)),
        _datetime_filter("less_than_or_equal", _iso(VALUE)),
        _datetime_filter("is_not_null"),
    ],
)
def test_direct_list_builders_retain_safe_legacy_datetime_shapes(
    builder_class,
    filter_item,
):
    builder = builder_class(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=[filter_item],
    )

    query, params = builder.build()

    if builder_class is SessionListQueryBuilder:
        assert (
            "start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in query
        )
        assert "start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')" in query
    else:
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
    assert isinstance(params["start_date"], datetime)
    assert isinstance(params["end_date"], datetime)


@pytest.mark.parametrize(
    "builder_class",
    [TraceListQueryBuilder, SpanListQueryBuilder, SessionListQueryBuilder],
)
def test_trace_span_session_sql_keeps_exact_half_open_window(builder_class):
    filters = [_datetime_filter("between", [_iso(LOWER), _iso(UPPER)])]
    builder = builder_class(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=filters,
        page_number=3,
        page_size=25,
    )

    query, params = builder.build()

    if builder_class is SessionListQueryBuilder:
        assert (
            "start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in query
        )
        assert "start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')" in query
        assert params["start_date_us"] == _micros(LOWER)
        assert params["end_date_us"] == _micros(UPPER)
    else:
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
    assert params["start_date"] == LOWER
    assert params["end_date"] == UPPER
    if builder_class is SessionListQueryBuilder:
        assert params["offset"] == 75
    else:
        # Trace/span list page N prefix-fetches through the requested page and
        # slices after stable physical-identity de-duplication in Python.
        assert params["limit"] == 125


@pytest.mark.parametrize(
    "builder_class", [SessionListQueryBuilder, SessionListQueryBuilderV2]
)
def test_session_candidate_equals_keeps_one_microsecond_window(builder_class):
    builder = builder_class(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=[_datetime_filter("equals", _iso(VALUE))],
        page_size=25,
    )

    candidate_sql, candidate_params = builder.build_candidate_page_query()
    metrics_sql, metrics_params = builder.build_page_metrics_query(
        ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
    )

    for query, params in (
        (candidate_sql, candidate_params),
        (metrics_sql, metrics_params),
    ):
        assert "fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in query
        assert "fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')" in query
        assert params["start_date_us"] == _micros(VALUE)
        assert params["end_date_us"] == _micros(VALUE + ONE_MICROSECOND)
        assert params["start_date_us"] < params["end_date_us"]


def test_session_candidate_normalizes_offset_datetime_to_utc_microseconds():
    builder = SessionListQueryBuilderV2(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=[_datetime_filter("equals", "2026-02-02T08:34:05.654321+05:30")],
        page_size=25,
    )

    query, params = builder.build_candidate_page_query()

    assert "fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in query
    assert params["start_date_us"] == _micros(VALUE)
    assert params["end_date_us"] == _micros(VALUE + ONE_MICROSECOND)


def test_session_seed_keyset_keeps_same_second_microsecond_tie_break():
    before_start_time = VALUE + timedelta(microseconds=123)
    builder = SessionListQueryBuilderV2(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=[
            _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    query, params = builder.build_filter_seed_page(
        slice_start=LOWER,
        slice_end=UPPER,
        limit=25,
        before_start_time=before_start_time,
        before_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    assert (
        "start_time < fromUnixTimestamp64Micro("
        "%(filter_before_start_time_us)s, 'UTC')" in query
    )
    assert (
        "start_time = fromUnixTimestamp64Micro("
        "%(filter_before_start_time_us)s, 'UTC')" in query
    )
    assert "toUnixTimestamp64Micro(start_time)" not in query
    assert params["filter_before_start_time_us"] == _micros(before_start_time)


class _NoQueryAnalytics:
    def __init__(self):
        self.calls = 0

    def execute_ch_query(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("exact-empty datetime filters must not query ClickHouse")


@pytest.mark.parametrize(
    ("builder_class", "key_field"),
    [
        (TraceListQueryBuilder, "trace_id"),
        (SpanListQueryBuilder, "id"),
        (SessionListQueryBuilder, "session_id"),
        (VoiceCallListQueryBuilder, "trace_id"),
    ],
)
def test_bounded_list_is_null_returns_exact_empty_without_clickhouse(
    builder_class,
    key_field,
):
    filters = [_datetime_filter("is_null")]
    builder = builder_class(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=filters,
    )
    analytics = _NoQueryAnalytics()

    page = read_bounded_filter_page(
        builder=builder,
        analytics=analytics,
        filters=filters,
        key_field=key_field,
        page_number=0,
        page_size=25,
    )

    assert page.complete is True
    assert page.rows == []
    assert page.query_count == 0
    assert analytics.calls == 0


def test_system_graph_is_null_returns_exact_empty_without_clickhouse(monkeypatch):
    from tracer.services.clickhouse import graph_dispatch

    analytics = _NoQueryAnalytics()
    calls = []

    def exact_read(namespace, identity, **options):
        calls.append((namespace, identity, options))
        return options["pending_payload"]

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )

    response = fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=[_datetime_filter("is_null")],
        interval="day",
        metric_id="latency",
    )

    assert len(response["data"]) == 1
    assert response["data"][0]["value"] == 0
    assert response["data"][0]["primary_traffic"] == 0
    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_sampled"] is False
    assert response["query_count"] == 0
    assert response["query_exact"] is True
    assert response["query_provenance"] == "exact_snapshot"
    assert analytics.calls == 0
    assert calls == []


@pytest.mark.parametrize(
    "filters",
    [
        [_datetime_filter("is_null")],
        [
            _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
            _datetime_filter("greater_than", _iso(UPPER)),
        ],
    ],
)
def test_historical_eval_exact_empty_returns_before_query_construction(
    monkeypatch,
    filters,
):
    task = SimpleNamespace(
        run_type=RunType.HISTORICAL,
        spans_limit=25,
        sampling_rate=100.0,
        filters={"filters": filters},
        row_type=RowType.SPANS,
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    def fail(*args, **kwargs):
        raise AssertionError("exact-empty eval selection must not build or run SQL")

    monkeypatch.setattr(row_resolver, "_build_sample_query", fail)
    monkeypatch.setattr(row_resolver, "_resolve_bounded_historical_span_ids", fail)

    resolved = row_resolver.resolve_desired_rows(task)

    assert resolved.candidate_ids == ()
    assert resolved.matched_ids == ()
    assert resolved.full_state is True


def test_continuous_eval_exact_empty_returns_full_state_without_clickhouse(
    monkeypatch,
):
    task = SimpleNamespace(
        run_type=RunType.CONTINUOUS,
        spans_limit=25,
        sampling_rate=100.0,
        filters={"filters": [_datetime_filter("is_null")]},
        row_type=RowType.SPANS,
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        continuous_cursor=LOWER,
        start_time=LOWER,
        created_at=LOWER,
    )

    def fail(*args, **kwargs):
        raise AssertionError("exact-empty continuous eval must not query ClickHouse")

    monkeypatch.setattr(
        "tracer.selectors.eval_tasks.continuous_candidates.discover_continuous_candidates",
        fail,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        fail,
    )

    resolved = row_resolver.resolve_desired_rows(task)

    assert resolved.candidate_ids == ()
    assert resolved.matched_ids == ()
    assert resolved.full_state is True


def test_long_window_graph_strata_retain_complements_and_replace_positive_bounds():
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter("not_equals", _iso(VALUE)),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
    ]

    narrowed = _filters_for_window(
        filters,
        window_start=VALUE,
        window_end=VALUE + timedelta(minutes=5),
    )

    time_ops = [
        item["filter_config"]["filter_op"]
        for item in narrowed
        if item["column_id"] in {"created_at", "start_time"}
    ]
    assert time_ops == ["not_equals", "between"]
    assert any(item["column_id"] == "status" for item in narrowed)


def test_trace_multifilter_classifier_keeps_attribute_and_datetime_complement():
    filters = [
        _datetime_filter("between", [_iso(LOWER), _iso(UPPER)]),
        _datetime_filter("not_equals", _iso(VALUE)),
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]
    builder = TraceListQueryBuilder(
        project_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        filters=filters,
    )

    query, params = builder.build_filter_match_query(["trace-1"])

    assert (
        "latest_start_time < "
        "fromUnixTimestamp64Micro(%(trace_match_time_exclusion_0_start)s)" in query
    )
    assert (
        "latest_start_time >= "
        "fromUnixTimestamp64Micro(%(trace_match_time_exclusion_0_end)s)" in query
    )
    assert "latest_attr_value_0" in query
    assert params["trace_match_time_exclusion_0_start"] == _micros(VALUE)
    assert params["trace_match_time_exclusion_0_end"] == _micros(
        VALUE + ONE_MICROSECOND
    )
