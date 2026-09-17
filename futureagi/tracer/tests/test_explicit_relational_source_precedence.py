"""Offline dispatch regressions: explicit relational sources are not native aliases."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tracer.serializers.filters import EvalTaskFiltersField
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests.test_explicit_attribute_source_precedence import public
from tracer.tests.test_session_entity_filter_membership import (
    SESSION,
    _builder,
    _filter,
)

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
METHODS = (
    "_build_system_metric_condition",
    "_build_eval_condition",
    "_build_annotation_condition",
    "_build_span_attr_condition",
    "_build_annotator_condition",
    "_build_my_annotations_condition",
    "_build_has_eval_condition",
    "_build_has_annotation_condition",
)


@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("source", ["EVAL_METRIC", "ANNOTATION"])
@pytest.mark.parametrize(
    "key",
    [
        "cost",
        "user_id",
        "trace_id",
        "session_id",
        "created_at",
        "start_time",
        "annotator",
        "my_annotations",
        "has_eval",
        "has_annotation",
    ],
)
def test_explicit_relation_dispatch_uses_its_own_source(builder_cls, source, key):
    item = public([_filter(key, ["excluded"], operation="not_in", source=source)])[0]
    method = (
        "_build_eval_condition"
        if source == "EVAL_METRIC"
        else "_build_annotation_condition"
    )
    if source == "ANNOTATION" and key in {
        "annotator",
        "my_annotations",
        "has_annotation",
    }:
        method = f"_build_{key}_condition"
    builder = builder_cls(project_id=PROJECT)
    with ExitStack() as stack:
        handlers = {
            name: stack.enter_context(patch.object(builder, name, return_value="1 = 1"))
            for name in METHODS
        }
        sql, _ = builder.translate([item])
    assert sql == "1 = 1"
    assert [name for name, handler in handlers.items() if handler.called] == [method]
    if method == "_build_eval_condition":
        handlers[method].assert_called_once_with(key, "not_in", ["excluded"])
    elif method == "_build_annotation_condition":
        handlers[method].assert_called_once_with(key, "text", "not_in", ["excluded"])


@pytest.mark.parametrize("source", [None, "NORMAL", "SYSTEM_METRIC", "ANNOTATION"])
@pytest.mark.parametrize("key", ["annotator", "my_annotations", "has_annotation"])
def test_annotation_controls_keep_legacy_and_explicit_annotation_dispatch(source, key):
    builder = ClickHouseFilterBuilder(project_id=PROJECT)
    item = _filter(key, True, kind="boolean", source=source)
    with patch.object(
        builder, f"_build_{key}_condition", return_value="1 = 1"
    ) as handler:
        assert builder.translate([item])[0] == "1 = 1"
    handler.assert_called_once()


@pytest.mark.parametrize("source", [None, "NORMAL", "SYSTEM_METRIC"])
@pytest.mark.parametrize("key", ["trace_id", "session_id"])
def test_native_negative_ids_keep_native_sql(source, key):
    sql, params = ClickHouseFilterBuilder(project_id=PROJECT).translate(
        public([_filter(key, ["excluded"], operation="not_in", source=source)])
    )
    assert "NOT IN" in sql and "span_attr" not in sql
    assert ("excluded",) in params.values()


@pytest.mark.parametrize("source", ["EVAL_METRIC", "ANNOTATION"])
@pytest.mark.parametrize(
    "key", ["session_id", "user", "total_cost", "first_message", "has_eval"]
)
@pytest.mark.parametrize("org", [False, True])
def test_session_relation_collision_is_not_native_or_dropped(source, key, org):
    item = public([_filter(key, ["excluded"], operation="not_in", source=source)])[0]
    builder = _builder(item, org=org)
    assert item not in builder._native_session_filters()
    assert item in builder._extract_span_filters()
    assert item in builder._bounded_scalar_span_filters()
    plans, residual = builder._bounded_span_filter_parts()
    assert not plans and residual == [item]
    assert builder._validate_bounded_relational_filters(residual) == ([], [item])
    assert builder._build_resolved_session_clause({}) == ""
    assert builder._build_having_clauses() == ""
    assert not builder._has_message_filters()
    method = (
        "_build_eval_condition"
        if source == "EVAL_METRIC"
        else "_build_annotation_condition"
    )
    # Compile the real finite session classifier as well as its routing helpers.
    # Only the terminal relation predicate is spied; no rows/DB are simulated.
    with patch.object(
        builder._FILTER_BUILDER_CLS, method, return_value="1 = 1"
    ) as handler:
        sql, _ = builder.build_filter_match_query([SESSION])
    assert "matching_relational_sessions" in sql
    assert handler.call_count == (2 if org else 1)
    assert all(call.args[0] == key for call in handler.call_args_list)


@pytest.mark.parametrize("source", ["EVAL_METRIC", "ANNOTATION"])
@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_relational_dates_do_not_become_session_time_scope(source, key):
    item = _filter(
        key,
        ["2020-01-01", "2020-01-02"],
        kind="datetime",
        operation="between",
        source=source,
    )
    builder = _builder(item)
    control = _builder()
    assert builder.parse_time_range(builder.filters) == control.parse_time_range(
        control.filters
    )
    assert builder.bounded_datetime_exclusion_sql([item]) == ("", {})
    assert builder._bounded_span_filter_parts() == ([], [item])
    with patch.object(
        builder._FILTER_BUILDER_CLS,
        "_build_eval_condition"
        if source == "EVAL_METRIC"
        else "_build_annotation_condition",
        return_value="1 = 1",
    ) as handler:
        assert (
            builder._FILTER_BUILDER_CLS(project_id=PROJECT).translate([item])[0]
            == "1 = 1"
        )
    handler.assert_called_once()


def test_mixed_session_native_raw_and_annotation_clauses_stay_separate():
    native = _filter(
        "session_id", ["excluded-native"], operation="not_in", source="SYSTEM_METRIC"
    )
    raw = _filter("session_id", ["excluded-raw"], operation="not_in")
    annotation = _filter(
        "session_id", ["excluded-annotation"], operation="not_in", source="ANNOTATION"
    )
    builder = _builder(native, raw, annotation)
    plans, residual = builder._bounded_span_filter_parts()
    assert len(plans) == 1 and residual == [annotation]
    # Session grouped absence does not promise a raw presence-only seed.
    # Keep the unchanged latest typed-key/value plan, not a new pruning rule.
    aggregates = " ".join(plans[0].aggregates)
    assert "mapContains(span_attr_str" in aggregates
    assert "NOT IN" in plans[0].predicate
    assert ("excluded-raw",) in plans[0].params.values()
    params = {}
    assert "NOT IN" in builder._build_resolved_session_clause(params)
    assert list(params.values()) == [("excluded-native",)]


@pytest.mark.parametrize("source", [None, "NORMAL", "SYSTEM_METRIC"])
def test_session_has_eval_legacy_membership_lane_is_retained(source):
    item = _filter("has_eval", False, kind="boolean", source=source)
    assert _builder()._validate_bounded_relational_filters([item]) == ([item], [])


def test_eval_named_has_annotation_does_not_require_annotation_metadata():
    item = _filter(
        "has_annotation", ["excluded"], operation="not_in", source="EVAL_METRIC"
    )
    builder = _builder(item, org=True)
    builder.annotation_label_ids_by_project = None
    assert builder._validate_bounded_relational_filters([item]) == ([], [item])


@pytest.mark.parametrize("source", ["SPAN_ATTRIBUTE", "EVAL_METRIC", "ANNOTATION"])
@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_saved_task_relational_date_name_does_not_become_native_window(source, key):
    native = _filter(
        "created_at",
        ["2026-09-01", "2026-09-07"],
        kind="datetime",
        operation="between",
        source="SYSTEM_METRIC",
    )
    item = _filter(key, 1, kind="number", operation="greater_than", source=source)
    validated = EvalTaskFiltersField().run_validation({"filters": [native, item]})
    assert validated["filters"][1]["filter_config"]["col_type"] == source
    assert not BaseQueryBuilder.is_datetime_filter(validated["filters"][1])
    assert BaseQueryBuilder.parse_time_range(validated["filters"], strict=True) == (
        BaseQueryBuilder.parse_time_range([native], strict=True)
    )


@pytest.mark.parametrize("source", [None, "NORMAL", "SYSTEM_METRIC", "METADATA"])
@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_base_legacy_native_and_metadata_dates_retain_strict_handling(source, key):
    native = _filter(
        key,
        ["2026-09-01", "2026-09-07"],
        kind="datetime",
        operation="between",
        source=source,
    )
    control = _filter(
        "created_at",
        ["2026-09-01", "2026-09-07"],
        kind="datetime",
        operation="between",
        source="SYSTEM_METRIC",
    )
    assert BaseQueryBuilder.is_datetime_filter(native)
    assert BaseQueryBuilder.parse_time_range([native], strict=True) == (
        BaseQueryBuilder.parse_time_range([control], strict=True)
    )
    malformed = _filter(key, 1, kind="number", operation="greater_than", source=source)
    with pytest.raises(ValueError, match="must use the datetime filter type"):
        BaseQueryBuilder.parse_time_range([malformed], strict=True)
