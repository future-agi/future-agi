"""Offline public-wire routing regressions; no DB execution or performance claim.

Explicit SPAN_ATTRIBUTE is a property identity, not a native-metric alias.
Native/omitted-family controls deliberately retain the legacy alias contract.
"""

import json
import socket
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from tracer.serializers.filters import FilterListField
from tracer.serializers.trace import TraceObserveListQuerySerializer
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
    compile_span_filter_plans,
    compile_trace_filter_plans,
    partition_span_filter_plans,
    partition_trace_filter_plans,
    targets_span_filter_domain,
    targets_trace_filter_domain,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000002"
COLLISIONS = (
    "span_id",
    "id",
    "trace_id",
    "project_id",
    "name",
    "trace_name",
    "session",
    "session_id",
    "trace_session_id",
    "span_name",
    "model",
    "provider",
    "status",
    "cost",
    "total_tokens",
    "latency",
    "observation_type",
    "user",
    "user_id",
    "end_user_id",
    "user_id_type",
    "created_at",
    "start_time",
    "end_time",
    "has_eval",
    "has_annotation",
    "annotator",
    "my_annotations",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
    "gen_ai.usage.total_tokens",
    "llm.token_count.total",
    "llm.token_count.prompt",
    "llm.token_count.completion",
)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline compiler test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def leaf(
    key, kind="number", operation="greater_than", value=0.01, family="SPAN_ATTRIBUTE"
):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": family,
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def public(items):
    # The real public serializer does not rename a raw key to its native alias.
    return FilterListField().run_validation(items)


@pytest.mark.parametrize("key", COLLISIONS)
@pytest.mark.parametrize(
    "compiler", [compile_trace_filter_plans, compile_span_filter_plans]
)
def test_explicit_raw_numeric_collision_is_a_typed_map_plan(compiler, key):
    item = public([leaf(key)])[0]
    plans = compiler([item])
    assert len(plans) == 1
    plan = plans[0]
    assert key in plan.params.values()
    assert "span_attr_num" in " ".join(plan.aggregates)
    assert plan.scope == ("any" if compiler is compile_trace_filter_plans else "span")
    assert plan.raw_witness_predicate is not None


@pytest.mark.parametrize("key", COLLISIONS)
@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
def test_explicit_raw_collision_bypasses_alias_and_relational_handlers(
    builder_cls, key
):
    builder = builder_cls(project_id=PROJECT)
    with (
        patch.object(
            builder,
            "_build_has_eval_condition",
            side_effect=AssertionError("relational"),
        ),
        patch.object(
            builder,
            "_build_has_annotation_condition",
            side_effect=AssertionError("relational"),
        ),
        patch.object(
            builder,
            "_build_annotator_condition",
            side_effect=AssertionError("relational"),
        ),
        patch.object(
            builder,
            "_build_my_annotations_condition",
            side_effect=AssertionError("relational"),
        ),
    ):
        sql, params = builder.translate(public([leaf(key)]))
    assert f"'{key}'" in sql  # This compiler escapes keys as SQL literals.
    assert (
        "attrs_number" if builder_cls is ClickHouseFilterBuilderV2 else "span_attr_num"
    ) in sql
    assert "parent_span_id IS NULL" not in sql
    assert " FINAL" not in sql


@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize(
    "key", ["gen_ai.usage.input_tokens", "llm.token_count.prompt", "cost"]
)
def test_exact_graph_raw_alias_keeps_any_child_source(mode, key):
    sql, params = compile_exact_graph_filter_predicates(
        public([leaf(key)]),
        project_id=PROJECT,
        observe_type=mode,
    )
    assert f"'{key}'" in sql
    assert "attrs_number" in sql
    assert "parent_span_id IS NULL" not in sql
    assert ("trace_id IN" in sql) == (mode == "trace")


@pytest.mark.parametrize("family", ["SYSTEM_METRIC", "NORMAL"])
@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize(
    "key,column",
    [
        ("gen_ai.usage.input_tokens", "prompt_tokens"),
        ("gen_ai.usage.output_tokens", "completion_tokens"),
        ("llm.token_count.total", "total_tokens"),
    ],
)
def test_native_and_legacy_token_aliases_still_use_root_column(
    family, builder_cls, key, column
):
    sql, params = builder_cls(project_id=PROJECT).translate([leaf(key, family=family)])
    assert column in sql
    assert key not in params.values()
    assert "parent_span_id IS NULL" in sql


@pytest.mark.parametrize("family", ["SYSTEM_METRIC", "NORMAL", "TRACE_END_USER"])
@pytest.mark.parametrize("key", ["user", "user_id", "user_id_type", "end_user_id"])
def test_structural_user_aliases_still_use_dimension_or_native_column(family, key):
    sql, params = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        [
            leaf(key, "text", "equals", "synthetic-user", family),
        ]
    )
    assert key not in params.values()
    assert "attrs_string" not in sql
    assert "end_user" in sql


@pytest.mark.parametrize(
    "key", ["has_eval", "has_annotation", "annotator", "my_annotations", "end_user_id"]
)
@pytest.mark.parametrize(
    "partition", [partition_trace_filter_plans, partition_span_filter_plans]
)
def test_explicit_raw_relational_name_is_not_residual(partition, key):
    plans, residual = partition(public([leaf(key)]))
    assert len(plans) == 1 and not residual


@pytest.mark.parametrize("key", ["created_at", "start_time"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "operation,value",
    [
        ("equals", "customer-clock"),
        ("not_equals", "customer-clock"),
        ("is_null", None),
    ],
)
def test_raw_date_name_never_consumes_or_widens_native_window(
    key, days, operation, value
):
    end = datetime(2026, 9, 5)
    start = end - timedelta(days=days)
    window = leaf(
        "created_at",
        "datetime",
        "between",
        [start.isoformat(), end.isoformat()],
        "SYSTEM_METRIC",
    )
    raw = leaf(key, "text", operation, value)
    items = public([window, raw])
    assert BaseQueryBuilder.parse_time_range(items, strict=True) == (start, end)
    assert not BaseQueryBuilder.is_datetime_complement_filter(raw)
    analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(items)
    assert not analyzed.empty and not analyzed.exclusions
    assert targets_trace_filter_domain([raw])
    assert targets_span_filter_domain([raw])
    assert len(compile_trace_filter_plans(items)) == 1


@pytest.mark.parametrize("family", ["SYSTEM_METRIC", "NORMAL"])
def test_native_date_type_validation_is_not_bypassed(family):
    with pytest.raises(ValueError, match="datetime filter type"):
        BaseQueryBuilder.parse_time_range(
            [leaf("created_at", family=family)], strict=True
        )


@pytest.mark.parametrize("key", ["model", "cost", "created_at", "has_eval"])
@pytest.mark.parametrize(
    "operation,value", [("not_equals", "blocked"), ("is_null", None)]
)
def test_raw_collisions_keep_negative_and_group_missing_safety(key, operation, value):
    plan = compile_trace_filter_plans(public([leaf(key, "text", operation, value)]))[0]
    assert plan.raw_witness_predicate is None
    assert plan.exclude_group_matches == (operation == "is_null")
    assert key in plan.params.values()


@pytest.mark.parametrize("key", ["model", "cost", "created_at", "has_eval"])
def test_mixed_type_raw_collision_is_one_or_leaf_not_native(key):
    item = leaf(key, "text", "in", ["1", 1, True])
    item["filter_config"]["attribute_value_types"] = ["string", "number", "boolean"]
    plans = compile_trace_filter_plans(public([item, leaf("other")]))
    assert len(plans) == 2
    assert " OR " in plans[0].predicate
    assert all(
        name in " ".join(plans[0].aggregates)
        for name in ("span_attr_str", "span_attr_num", "span_attr_bool")
    )


@pytest.mark.parametrize(
    "operation,value",
    [
        ("contains", ["synthetic-tag"]),
        ("not_contains", ["synthetic-tag"]),
        ("is_null", None),
        ("is_not_null", None),
    ],
)
def test_native_trace_tags_uses_latest_trace_dimension_not_raw_span_map(
    operation, value
):
    item = public([leaf("tags", "array", operation, value, "SYSTEM_METRIC")])[0]
    builder = ClickHouseFilterBuilderV2(
        project_id=PROJECT, candidate_ids_param="candidate_trace_ids"
    )
    sql, params = builder.translate([item])
    assert "FROM traces" in sql
    assert "argMax(" in sql and "_version" in sql
    assert "is_deleted" in sql
    assert "%(candidate_trace_ids)s" in sql
    assert "%(project_id)s" in sql
    assert "span_attr" not in sql and "attrs_" not in sql
    assert " FINAL" not in sql
    assert "LIMIT" not in sql
    plans, residual = partition_trace_filter_plans([item])
    assert not plans and residual == [item]


def test_raw_tags_array_is_not_native_trace_tags():
    plans, residual = partition_trace_filter_plans(
        public([leaf("tags", "array", "contains", ["synthetic-tag"])])
    )
    assert not residual
    assert "span_attributes_raw" in " ".join(plans[0].aggregates)


@pytest.mark.parametrize(
    "builder_cls,table,version,deleted",
    [
        (
            ClickHouseFilterBuilder,
            "tracer_trace",
            "_peerdb_version",
            "(_peerdb_is_deleted OR deleted)",
        ),
        (ClickHouseFilterBuilderV2, "traces", "_version", "is_deleted"),
    ],
)
@pytest.mark.parametrize("org_scope", [False, True])
def test_native_trace_tags_preserves_tenant_identity_and_tombstone_replay(
    builder_cls, table, version, deleted, org_scope
):
    scope = {"project_ids": [PROJECT]} if org_scope else {"project_id": PROJECT}
    builder = builder_cls(
        **scope, candidate_ids_param="candidate_trace_ids", span_date_scope=True
    )
    sql, params = builder.translate(
        public(
            [
                leaf(
                    "tags", "array", "not_contains", ["obsolete-tag"], "SYSTEM_METRIC"
                ),
            ]
        )
    )
    assert f"FROM {table}" in sql
    assert f"argMax(tuple(tags, {deleted})," in sql
    assert version in sql
    assert "latest_native_trace_tags.2 = 0" in sql
    assert "NOT IN (" in sql and "arrayExists" in sql
    assert "NOT (toString(JSONType(latest_native_trace_tags.1)) = 'Array') OR" in sql
    assert "start_date" not in sql and "end_date" not in sql
    assert "FINAL" not in sql and "LIMIT" not in sql
    before_group = sql.split("GROUP BY project_id, id")[0]
    assert "tags =" not in before_group and "JSONExtract" not in before_group
    assert ("(project_id, toString(trace_id)) NOT IN" in sql) == org_scope
    assert ("%(project_ids)s" in sql) == org_scope
    assert ("%(project_id)s" in sql) != org_scope


def test_multiple_native_tags_and_raw_tag_bind_independently():
    sql, params = compile_exact_graph_filter_predicates(
        public(
            [
                leaf("tags", "array", "contains", ["first-tag"], "SYSTEM_METRIC"),
                leaf("tags", "array", "not_contains", ["second-tag"], "SYSTEM_METRIC"),
                leaf("tag", "text", "equals", "raw-singular-tag"),
                leaf("tags", "array", "contains", ["raw-array-tag"]),
            ]
        ),
        project_id=PROJECT,
        observe_type="span",
    )
    assert ("first-tag",) in params.values()
    assert ("second-tag",) in params.values()
    assert ("raw-array-tag",) in params.values()
    assert "raw-singular-tag" in params.values()
    assert sql.count("FROM traces") == 2
    assert "attrs_string" in sql and "attributes_extra" in sql
    assert "'tag'" in sql


@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
def test_native_tags_empty_array_is_null_but_raw_empty_array_is_present(operation):
    sql, _ = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        public(
            [
                leaf("tags", "array", operation, None, "SYSTEM_METRIC"),
            ]
        )
    )
    assert "notEmpty(JSONExtractArrayRaw(latest_native_trace_tags.1))" in sql
    assert ("NOT IN (" in sql) == (operation == "is_null")
    plans = compile_span_filter_plans(public([leaf("tags", "array", operation, None)]))
    assert "notEmpty" not in " ".join(plans[0].aggregates)


def test_tags_identity_classifier_keeps_root_window_and_finite_residual_scope():
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=public(
            [
                leaf(
                    "created_at",
                    "datetime",
                    "between",
                    ["2026-09-01T00:00:00Z", "2026-09-05T00:00:00Z"],
                    "SYSTEM_METRIC",
                ),
                leaf("tags", "array", "contains", ["latest-tag"], "SYSTEM_METRIC"),
            ]
        ),
        page_size=25,
    )
    sql, params = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {"trace_id": "00000000-0000-4000-8000-000000000003"},
        ]
    )
    assert "FROM traces" in sql
    assert "toString(id) IN %(candidate_trace_ids)s" in sql
    assert "(project_id, toString(trace_id)) IN" not in sql
    assert_coherent_classifier(sql)
    assert "_physical_winner.3 AS latest_is_deleted" in sql
    assert (
        "GROUP BY observation_type, service_name, "
        "toStartOfHour(start_time), trace_id, id"
    ) in " ".join(sql.split())
    assert "PREWHERE project_id = %(project_id)s" in sql
    assert params["project_id"] == PROJECT
    assert "WHERE latest_is_deleted = 0" in sql
    assert params["start_date"] == datetime(2026, 9, 1)
    assert params["end_date"] == datetime(2026, 9, 5)
    assert "FINAL" not in sql


@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_bounded_public_wire_accepts_raw_dates_without_replacing_native_window(key):
    items = [
        leaf(
            "created_at",
            "datetime",
            "between",
            ["2026-09-01T00:00:00Z", "2026-09-05T00:00:00Z"],
            "SYSTEM_METRIC",
        ),
        leaf(key, "text", "equals", "customer-clock"),
    ]
    serializer = TraceObserveListQuerySerializer(
        data={"project_id": PROJECT, "filters": json.dumps(items)}
    )
    assert serializer.is_valid(), serializer.errors
    filters = serializer.validated_data["filters"]
    assert filters[1]["column_id"] == key
    assert filters[1]["filter_config"]["col_type"] == "SPAN_ATTRIBUTE"
    assert BaseQueryBuilder.parse_time_range(filters, strict=True) == (
        datetime(2026, 9, 1),
        datetime(2026, 9, 5),
    )


@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_legacy_camel_case_raw_date_is_not_a_window(key):
    item = {
        "columnId": key,
        "filterConfig": {
            "colType": "SPAN_ATTRIBUTE",
            "filterType": "text",
            "filterOp": "is_null",
        },
    }
    assert not BaseQueryBuilder.is_datetime_filter(item)
    assert not BaseQueryBuilder.is_datetime_complement_filter(item)
    assert len(compile_trace_filter_plans([item])) == 1


@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_raw_only_dates_retain_finite_default_window(key):
    with patch("tracer.services.clickhouse.query_builders.base.datetime") as clock:
        clock.utcnow.return_value = datetime(2026, 9, 5)
        assert BaseQueryBuilder.parse_time_range([leaf(key)], strict=True) == (
            datetime(2026, 8, 6),
            datetime(2026, 9, 5),
        )
