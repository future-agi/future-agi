"""Entity-level attribute matching; synthetic regression, not a benchmark."""

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000002"
END = datetime(2026, 9, 5)


def leaf(key, operation="in", value=None, kind="text"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": ["match"] if value is None else value,
        },
    }


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_user_leaves_are_aggregated_independently_without_narrowing_metrics(
    days, count
):
    filters = [leaf(f"customer.key_{i}") for i in range(count)]
    rows, having, params = graph._user_membership_having(filters, project_id=PROJECT)
    assert len(rows) == count
    for index in range(count):
        assert f"countIf(user_member_match_{index}) > 0" in having
        assert (
            f"customer.key_{index}" in rows[index]
            or f"customer.key_{index}" in params.values()
        )
    sql, _, _ = graph._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters,
        start_date=END - timedelta(days=days),
        end_date=END,
        include_trace_ids=False,
        all_snapshot_users=True,
    )
    candidate = sql.split("candidate_user_spans AS (", 1)[1].split(
        "candidate_user_session_ids AS (", 1
    )[0]
    assert "attrs_string[" not in candidate
    assert "user_member_match" not in candidate
    assert "HAVING " + having in sql
    assert "sum(ifNull(cost, 0)) AS total_cost" in sql
    assert "SELECT toString(trace_id) FROM candidate_trace_ids" not in sql
    assert "SAMPLE " not in sql


@pytest.mark.parametrize(
    "operation,positive",
    [
        ("not_in", "in"),
        ("not_equals", "equals"),
        ("not_contains", "contains"),
        ("not_between", "between"),
    ],
)
def test_user_negative_requires_present_domain_and_no_positive_witness(
    operation, positive
):
    kind = "number" if operation == "not_between" else "text"
    value = (
        [1, 2]
        if kind == "number"
        else (["match"] if operation == "not_in" else "match")
    )
    rows, having, params = graph._user_membership_having(
        [leaf("customer.key", operation, value, kind)], project_id=PROJECT
    )
    assert len(rows) == 2
    assert (
        having
        == "countIf(user_member_match_0) > 0 AND countIf(user_member_match_1) = 0"
    )
    assert "mapContains" in rows[0] and "mapContains" in rows[1]
    assert params and "user_member_0_forbidden" in str(params)


@pytest.mark.parametrize("kind,value", [("text", None), ("array", None), ("map", None)])
def test_null_means_no_typed_value_on_any_user_span(kind, value):
    item = leaf("customer.key", "is_null", value, kind)
    item["filter_config"]["filter_value"] = None
    rows, having, _ = graph._user_membership_having([item], project_id=PROJECT)
    assert len(rows) == 1
    assert having == "countIf(user_member_match_0) = 0"


@pytest.mark.parametrize("family", ["ANNOTATION", "EVAL_METRIC"])
@pytest.mark.parametrize(
    "operation,positive",
    [
        ("is_null", "is_not_null"),
        ("not_equals", "equals"),
        ("not_in", "in"),
        ("not_contains", "contains"),
        ("not_between", "between"),
    ],
)
def test_user_relation_absence_and_exclusions_match_complete_user(
    monkeypatch, family, operation, positive
):
    """A missing/allowed sibling cannot conceal another matching relation."""
    compiled_ops = []

    def compile_leaf(filters, **kwargs):
        op = filters[0]["filter_config"]["filter_op"]
        compiled_ops.append(op)
        return f"relation_{op}", "1 = 1", {}, False

    monkeypatch.setattr(graph, "_user_filter_clauses", compile_leaf)
    item = leaf("relation-label", operation)
    item["filter_config"]["col_type"] = family
    rows, having, _ = graph._user_membership_having([item], project_id=PROJECT)
    if operation == "is_null":
        assert compiled_ops == ["is_not_null"]
        assert having == "countIf(user_member_match_0) = 0"
    else:
        assert compiled_ops == ["is_not_null", positive]
        assert (
            having
            == "countIf(user_member_match_0) > 0 AND countIf(user_member_match_1) = 0"
        )
    assert len(rows) == len(compiled_ops)


@pytest.mark.parametrize(
    "key", ["total_cost", "total_tokens", "first_message", "end_time", "session_id"]
)
def test_session_raw_names_stay_scalar_and_outside_native_having(key):
    item = leaf(key, "equals", "raw")
    assert graph._session_having_clause([item], {}) == ""
    sql, params = graph._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=[item],
        start_date=END - timedelta(days=7),
        end_date=END,
        include_trace_ids=False,
        anchor_by_session_start=True,
    )
    assert key in params.values()
    assert "matching_scalar_sessions" in sql


def test_session_null_uses_group_absence_not_any_missing_child():
    item = leaf("customer.key", "is_null")
    item["filter_config"]["filter_value"] = None
    plan = graph._session_membership_plan(project_id=PROJECT, filters=[item])
    assert plan.scalar_group_predicates == ("countIf(latest_attr_exists_0) = 0",)
    assert plan.scalar_witness_predicate is None


def test_full_snapshot_population_cannot_be_combined_with_partition_seed():
    with pytest.raises(ValueError, match="only one candidate"):
        graph._user_aggregate_source_sql(
            project_id=PROJECT,
            filters=[],
            start_date=END - timedelta(days=7),
            end_date=END,
            include_trace_ids=False,
            all_snapshot_users=True,
            candidate_trace_ids_param="candidate_trace_ids",
        )
