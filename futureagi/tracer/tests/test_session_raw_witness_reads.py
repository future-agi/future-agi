"""Regression contracts for bounded session latest-state attribute reads."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
END = datetime(2026, 7, 31, 7, 0)
START = END - timedelta(days=7)
CANDIDATE_SESSION_ID = "00000000-0000-4000-8000-000000000002"


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute_filter(
    key: str,
    value: object,
    *,
    filter_type: str = "text",
    operation: str = "equals",
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _builder(*attribute_filters: dict) -> SessionListQueryBuilderV2:
    return SessionListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), *attribute_filters],
        bounded_internal_scan=True,
    )


@pytest.mark.unit
def test_session_seed_stays_root_ordered_and_exact_scalar_replay_is_bounded() -> None:
    builder = _builder(_attribute_filter("final_status", ["Rechazado"], operation="in"))

    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
    )

    # A qualifying attribute may live on any child span.  Filtering the root
    # seed by a raw attribute witness would therefore create false negatives.
    assert "attrs_string" not in sql
    assert "mapValues" not in sql
    assert not any(key.startswith("latest_filter_") for key in params)
    assert "GROUP BY seed_spans.trace_session_id" in sql
    assert "LIMIT %(filter_seed_limit)s" in sql

    match_sql, match_params = builder.build_filter_match_query([CANDIDATE_SESSION_ID])
    assert "candidate_scalar_span_identities AS" in match_sql
    assert "matching_scalar_sessions AS" in match_sql
    assert "HAVING countIf(latest_attr_exists_0 AND" in match_sql
    assert "lowerUTF8(toString(latest_attr_value_0)) IN" in match_sql
    assert match_params["latest_filter_param_0"] == ("rechazado",)


@pytest.mark.unit
def test_session_seed_defers_json_and_scalar_filters_to_exact_all_span_replay() -> None:
    builder = _builder(
        _attribute_filter(
            "customer_context",
            {"country": "CO"},
            filter_type="map",
            operation="contains",
        ),
        _attribute_filter("final_status", ["Rechazado"], operation="in"),
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
    )

    assert "attrs_string" not in sql
    assert "attributes_extra" not in sql
    assert not any(key.startswith("latest_filter_") for key in params)

    match_sql, match_params = builder.build_filter_match_query([CANDIDATE_SESSION_ID])
    assert "latest_json_map_exists_0" in match_sql
    assert "latest_attr_exists_1" in match_sql
    assert match_sql.count("countIf(") == 2
    assert "matching_scalar_sessions AS" in match_sql
    assert match_params["latest_filter_key_0"] == "customer_context"
    assert match_params["latest_filter_key_1"] == "final_status"


@pytest.mark.unit
def test_one_year_multi_attribute_session_filter_stays_candidate_scoped() -> None:
    """Long windows change the number of bounded slices, not classifier width."""

    long_start = END - timedelta(days=365)
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [long_start.isoformat(), END.isoformat()],
            },
        },
        _attribute_filter("final_status", ["Rechazado"], operation="in"),
        _attribute_filter(
            "customer_context",
            {"country": "CO"},
            filter_type="map",
            operation="contains",
        ),
        _attribute_filter(
            "quality_score",
            0.75,
            filter_type="number",
            operation="greater_than_or_equal",
        ),
        _attribute_filter(
            "reviewed",
            True,
            filter_type="boolean",
            operation="equals",
        ),
    ]
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
    )
    match_sql, match_params = builder.build_filter_match_query([CANDIDATE_SESSION_ID])

    assert builder.parse_time_range(filters) == (long_start, END)
    assert seed_params["filter_slice_start"] == END - timedelta(minutes=5)
    assert seed_params["filter_slice_end"] == END
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert "JSONExtract" not in seed_sql
    assert "candidate_filter_session_ids" in match_params
    assert match_params["candidate_filter_session_ids"] == (CANDIDATE_SESSION_ID,)
    assert "latest_json_map_exists_1" in match_sql
    assert "latest_attr_exists_0" in match_sql
    assert "latest_attr_exists_2" in match_sql
    assert "latest_attr_exists_3" in match_sql
    assert "LIMIT %(bounded_match_limit)s" in match_sql


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_type", "value", "raw_expression"),
    [
        (
            "map",
            {"country": "CO"},
            "JSONExtractRaw(attributes_extra, %(latest_filter_key_0)s)",
        ),
        (
            "array",
            ["vip", 3, True],
            "JSONExtractArrayRaw(attributes_extra, %(latest_filter_key_0)s)",
        ),
    ],
)
def test_positive_json_only_filter_defers_parsing_to_exact_classifier(
    filter_type: str,
    value: object,
    raw_expression: str,
) -> None:
    builder = _builder(
        _attribute_filter(
            "customer_context",
            value,
            filter_type=filter_type,
            operation="contains",
        )
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
    )

    assert "JSONHas(attributes_extra, %(latest_filter_key_0)s)" not in sql
    assert raw_expression not in sql
    assert "latest_filter_key_0" not in params

    plans, residual = builder._bounded_span_filter_parts()
    assert residual == []
    assert plans[0].raw_witness_predicate is None
    match_sql, match_params = builder.build_filter_match_query([CANDIDATE_SESSION_ID])
    assert raw_expression in match_sql
    assert match_params["latest_filter_key_0"] == "customer_context"


@pytest.mark.unit
def test_negative_only_session_filter_does_not_claim_a_raw_witness() -> None:
    builder = _builder(
        _attribute_filter(
            "final_status",
            "Rechazado",
            operation="not_equals",
        )
    )

    plans, residual = builder._bounded_span_filter_parts()
    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
    )

    assert residual == []
    assert len(plans) == 1
    assert plans[0].raw_witness_predicate is None
    assert "mapContains(attrs_string" not in sql
    assert "latest_filter_key_0" not in params


@pytest.mark.unit
def test_session_match_applies_exact_all_span_filters_before_session_page() -> None:
    builder = _builder(_attribute_filter("final_status", ["Rechazado"], operation="in"))

    sql, _ = builder.build_filter_match_query([CANDIDATE_SESSION_ID])

    resolved_roots = sql.split("resolved_root_sessions AS (", 1)[1].split(
        "\n        )", 1
    )[0]
    scalar_spans = sql.split("latest_candidate_scalar_spans AS (", 1)[1].split(
        "resolved_candidate_scalar_spans AS (", 1
    )[0]
    matching_traces = sql.split("matching_scalar_traces AS (", 1)[1].split(
        "matching_scalar_sessions AS (", 1
    )[0]
    matching_sessions = sql.split("matching_scalar_sessions AS (", 1)[1].split(
        "sessions AS (", 1
    )[0]
    sessions = sql.split("sessions AS (", 1)[1]
    assert "latest_attr_exists_0" not in resolved_roots
    assert "latest_attr_exists_0" in scalar_spans
    assert "lowerUTF8(toString(latest_attr_value_0)) IN" in matching_traces
    assert "HAVING countIf(" in matching_traces
    assert "FROM matching_scalar_traces" in matching_sessions
    assert "FROM resolved_root_sessions" in sessions
    assert "session_id IN (SELECT session_id FROM matching_scalar_sessions)" in sessions
