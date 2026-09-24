"""Bounded list text-null semantics for non-nullable CH25 columns."""

import pytest

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
)


def _model_null_filter(operation: str) -> dict:
    return {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": None,
        },
    }


def _session_null_filter(operation: str) -> dict:
    return {
        "column_id": "trace_session_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": None,
        },
    }


def _project_null_filter(operation: str) -> dict:
    return {
        "column_id": "project_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": None,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("compiler", "operation", "latest_fragment", "seed_fragment"),
    [
        (
            compile_span_filter_plans,
            "is_null",
            "latest_column_value_0 IS NULL OR latest_column_value_0 = ''",
            "model IS NULL OR model = ''",
        ),
        (
            compile_span_filter_plans,
            "is_not_null",
            "latest_column_value_0 IS NOT NULL AND latest_column_value_0 != ''",
            "model IS NOT NULL AND model != ''",
        ),
        (
            compile_trace_filter_plans,
            "is_null",
            "latest_column_value_0 IS NULL OR latest_column_value_0 = ''",
            "model IS NULL OR model = ''",
        ),
        (
            compile_trace_filter_plans,
            "is_not_null",
            "latest_column_value_0 IS NOT NULL AND latest_column_value_0 != ''",
            "model IS NOT NULL AND model != ''",
        ),
    ],
)
def test_model_null_filters_include_empty_string_sentinel(
    compiler,
    operation,
    latest_fragment,
    seed_fragment,
):
    plan = compiler([_model_null_filter(operation)])[0]

    assert latest_fragment in plan.predicate
    assert seed_fragment in plan.seed_predicate
    assert plan.params == {}


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
@pytest.mark.parametrize(
    "compiler", [compile_span_filter_plans, compile_trace_filter_plans]
)
def test_nullable_uuid_null_filters_do_not_compare_uuid_to_empty_string(
    compiler,
    operation,
):
    plan = compiler([_session_null_filter(operation)])[0]

    expected_operator = "IS NULL" if operation == "is_null" else "IS NOT NULL"
    assert f"latest_column_value_0 {expected_operator}" in plan.predicate
    assert f"trace_session_id {expected_operator}" in plan.seed_predicate
    assert "= ''" not in plan.predicate
    assert "!= ''" not in plan.predicate
    assert "= ''" not in plan.seed_predicate
    assert "!= ''" not in plan.seed_predicate
    assert plan.params == {}


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
@pytest.mark.parametrize(
    "compiler", [compile_span_filter_plans, compile_trace_filter_plans]
)
def test_non_nullable_project_uuid_null_filters_do_not_compare_to_empty_string(
    compiler,
    operation,
):
    plan = compiler([_project_null_filter(operation)])[0]

    expected_operator = "IS NULL" if operation == "is_null" else "IS NOT NULL"
    assert f"latest_column_value_0 {expected_operator}" in plan.predicate
    assert f"project_id {expected_operator}" in plan.seed_predicate
    assert "= ''" not in plan.predicate
    assert "!= ''" not in plan.predicate
    assert "= ''" not in plan.seed_predicate
    assert "!= ''" not in plan.seed_predicate
    assert plan.params == {}
