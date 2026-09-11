"""Full dashboard compilers bind data keys before V2 rewrites or driver escaping.

No clients, ORM metadata lookups, or Django startup are needed: metric identities
and output types are supplied explicitly, as they are after request resolution.
"""

import re
from copy import deepcopy
from types import SimpleNamespace

import pytest
from clickhouse_connect.driver.binding import finalize_query
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    _sanitize_attr_key,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
ORG = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"
METRIC_ID = "44444444-4444-4444-8444-444444444444"
DRIVER_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)
KEYS = (
    "company_id",
    "span_attr_str",
    "Customer.ID",
    'e2e-obs7-0-mtu6gzdl.客户,"\\key',
    "key'] OR 1=1 --",
    "%(project_ids)s {key:String} '\\; --",
    "control\x00line\n\ttab\u200bkey",
)


@pytest.fixture(
    params=[DashboardQueryBuilder, DashboardQueryBuilderV2], ids=["v1", "v2"]
)
def builder(request):
    return request.param


def custom_filter(key, kind="string", op="equal_to", value="00123"):
    return {
        "metric_type": "custom_attribute",
        "metric_name": key,
        "attribute_type": kind,
        "operator": op,
        "value": value,
        "source": "traces",
    }


def canonical_filter(key, kind="text", op="equals", value="00123"):
    return {
        **custom_filter(key),
        "canonical_filter": {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": kind,
                "filter_op": op,
                "filter_value": value,
            },
        },
    }


def breakdown(key, kind="custom_attribute"):
    return {"name": key, "type": kind, "attribute_type": "string"}


def build(
    builder,
    route,
    *,
    filters=(),
    global_filters=(),
    breakdowns=(),
    metric_options=None,
    latest_state=False,
):
    metric = {
        "type": route,
        "name": "latency" if route == "system_metric" else METRIC_ID,
        "aggregation": "avg",
        "filters": list(filters),
    }
    if route == "eval_metric":
        metric.update(config_id=METRIC_ID, output_type="SCORE")
    elif route == "annotation_metric":
        metric.update(label_id=METRIC_ID, output_type="numeric")
    metric.update(metric_options or {})
    config = {
        "project_ids": [PROJECT],
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "time_range": {
            "custom_start": "2026-09-01T00:00:00+00:00",
            "custom_end": "2026-09-02T00:00:00+00:00",
        },
        "granularity": "day",
        "filters": list(global_filters),
        "breakdowns": list(breakdowns),
    }
    original = deepcopy((metric, config))
    instance = builder(config)
    if latest_state:
        result = instance._build_metric_query_for_snapshot_mode(
            metric, latest_state=True
        )
        assert not instance._latest_state_spans_required
        assert getattr(instance, "_exact_metric_presence", None) is None
    else:
        result = instance.build_metric_query(metric)
    assert (metric, config) == original
    return result


def assert_bound(sql, params, key, name, map_column=None):
    assert params[name] == key
    token = f"%({name})s"
    assert token in sql
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    if map_column:
        assert f"{map_column}[{token}]" in sql
    # Both actual driver formatters preserve the original data after rewriting.
    # Escape the expected literal separately, not with a hand-rolled SQL escape.
    escaped = escape_params(params, DRIVER_CONTEXT)
    rendered_native = sql % escaped
    rendered_http = finalize_query(sql, params)
    for rendered, literal in (
        (rendered_native, escape_params({"key": key}, DRIVER_CONTEXT)["key"]),
        (rendered_http, finalize_query("%(key)s", {"key": key})),
    ):
        expected = f"{map_column}[{literal}]" if map_column else literal
        assert expected in rendered


def filter_key_name(route, index=0):
    return {
        "system_metric": f"_legacy_attr_key_{index}",
        "eval_metric": f"_evf_{index}_attr_key",
        "annotation_metric": f"_ann_span_filter_{index}_key",
    }[route]


def column(builder, kind):
    names = (
        {
            "string": "span_attr_str",
            "number": "span_attr_num",
            "boolean": "span_attr_bool",
        }
        if builder is DashboardQueryBuilder
        else {
            "string": "attrs_string",
            "number": "attrs_number",
            "boolean": "attrs_bool",
        }
    )
    return names[kind]


@pytest.mark.parametrize("route", ["system_metric", "eval_metric", "annotation_metric"])
@pytest.mark.parametrize(
    "kind,value", [("string", "00123"), ("number", 12.5), ("boolean", True)]
)
@pytest.mark.parametrize("key", KEYS)
def test_full_legacy_filter_builders_bind_exact_scalar_keys(
    builder, route, kind, value, key
):
    sql, params = build(builder, route, filters=[custom_filter(key, kind, value=value)])
    name = filter_key_name(route)
    assert_bound(sql, params, key, name, column(builder, kind))
    value_name = {
        "system_metric": "f_0_val",
        "eval_metric": "_evf_0_val",
        "annotation_metric": "_ann_span_filter_0_value",
    }[route]
    assert params[value_name] == value
    assert type(params[value_name]) is type(value)
    assert f"[{f'%({name})s'}] = %({value_name})s" in sql
    assert params["project_ids"] == [PROJECT]
    assert "%(start_date)s" in sql and "%(end_date)s" in sql


@pytest.mark.parametrize("key", [*KEYS, "latency", "source", "dataset", "MODEL"])
def test_eval_custom_breakdown_precedes_native_dispatch_and_retains_raw_key(
    builder, key
):
    sql, params = build(builder, "eval_metric", breakdowns=[breakdown(key)])
    name = "_ev_bd_attr_key_0"
    assert_bound(sql, params, key, name, column(builder, "string"))
    expr = f"s.{column(builder, 'string')}[%({name})s]"
    assert f"if({expr} != '', {expr}, '(not set)') AS breakdown_value" in sql
    assert sql.count(f"%({name})s") == 2


@pytest.fixture(
    params=[
        (DashboardQueryBuilder, False),
        (DashboardQueryBuilderV2, False),
        (DashboardQueryBuilderV2, True),
    ],
    ids=["v1", "v2-raw", "v2-latest"],
)
def snapshot_builder(request):
    return request.param


def custom_metric(key, kind="number"):
    return {
        "attribute_key": key,
        "attribute_type": kind,
        "aggregation": "avg" if kind == "number" else "count_distinct",
    }


def assert_map_binding(sql, params, key, name, map_column):
    assert_bound(sql, params, key, name)
    # V2's scalar projection contains newlines inside the Map subscript.
    assert re.search(
        re.escape(map_column) + r"\[\s*" + re.escape(f"%({name})s") + r"\s*\]",
        sql,
    )


@pytest.mark.parametrize("key", [*KEYS, "latency", "span_attr_num", "span_attr_bool"])
@pytest.mark.parametrize("kind", ["string", "number", "boolean"])
def test_custom_metric_exact_data_keys_in_raw_scalar_and_fallback_paths(
    snapshot_builder, key, kind
):
    builder, latest = snapshot_builder
    sql, params = build(
        builder,
        "custom_attribute",
        metric_options=custom_metric(key, kind),
        latest_state=latest,
    )
    attr_map = column(builder, kind)
    assert_map_binding(sql, params, key, "custom_metric_attr_key", attr_map)
    assert "mapContains(" in sql
    assert params["project_ids"] == [PROJECT]
    if latest and kind == "number":
        assert "latest_custom_metric_spans" in sql
        assert "avg(metric_value) AS value" in sql
        assert "tupleElement(latest_metric_state, 3) = 1" in sql
    else:
        aggregate = "avg" if kind == "number" else "uniqExact"
        assert f"{aggregate}({attr_map}[%(custom_metric_attr_key)s]) AS value" in sql
        if latest:
            assert_bound(sql, params, key, "dashboard_candidate_metric_key")
            assert "dashboard_filter_candidate_identities" in sql
            assert "mapContains(dashboard_candidate_source." + attr_map in sql


@pytest.mark.parametrize("key", [*KEYS, "latency", "model"])
@pytest.mark.parametrize("kind", ["string", "number", "boolean"])
@pytest.mark.parametrize("route", ["system_metric", "custom_attribute"])
def test_ordinary_and_scalar_custom_breakdowns_keep_exact_key_and_type(
    snapshot_builder, key, kind, route
):
    builder, latest = snapshot_builder
    bd = {**breakdown(key), "attribute_type": kind}
    sql, params = build(
        builder,
        route,
        metric_options=custom_metric("metric.key")
        if route == "custom_attribute"
        else None,
        breakdowns=[bd],
        latest_state=latest,
    )
    assert_map_binding(sql, params, key, "_custom_bd_key_0", column(builder, kind))
    assert "mapContains(" in sql
    if latest and route == "custom_attribute":
        assert "latest_custom_metric_spans" in sql
        assert "tupleElement(latest_metric_state, 5) = 1" in sql
        assert "tupleElement(latest_metric_state, 6) AS breakdown_value" in sql
    else:
        assert (
            f"{column(builder, kind)}[%(_custom_bd_key_0)s] AS breakdown_value" in sql
        )


def test_latest_fallback_multiple_keys_and_candidate_namespaces_remain_distinct():
    sql, params = build(
        DashboardQueryBuilderV2,
        "custom_attribute",
        latest_state=True,
        metric_options=custom_metric("metric.客户"),
        breakdowns=[
            breakdown("Customer.客户\u200b"),
            {**breakdown("model"), "attribute_type": "boolean"},
        ],
        filters=[canonical_filter("filter.客户"), custom_filter("legacy.客户")],
    )
    for name, key in {
        "custom_metric_attr_key": "metric.客户",
        "dashboard_candidate_metric_key": "metric.客户",
        "_custom_bd_key_0": "Customer.客户\u200b",
        "dashboard_candidate_breakdown_key_0": "Customer.客户\u200b",
        "_custom_bd_key_1": "model",
        "dashboard_candidate_breakdown_key_1": "model",
        "latest_filter_key_0": "filter.客户",
        "_legacy_attr_key_1": "legacy.客户",
    }.items():
        assert_bound(sql, params, key, name)
    assert params["f_1_val"] == "00123"
    assert "LIMIT 1 BY" in sql and "dashboard_replay_source" in sql
    # Only the existing first custom-metric breakdown is selected; all presence
    # predicates and candidate key witnesses still participate, unchanged.
    assert "attrs_string[%(_custom_bd_key_0)s] AS breakdown_value" in sql
    assert "mapContains(attrs_bool, %(_custom_bd_key_1)s)" in sql
    assert (
        "mapContains(dashboard_candidate_source.attrs_bool, %(dashboard_candidate_breakdown_key_1)s)"
        in sql
    )


@pytest.mark.parametrize(
    "target", ["metric", "ordinary-breakdown", "scalar-breakdown", "fallback-breakdown"]
)
@pytest.mark.parametrize(
    "key",
    ["", "\ud800", "a" * 4097, "é" * 2049],
    ids=["empty", "invalid-utf8", "oversize-ascii", "oversize-utf8"],
)
def test_bound_custom_routes_reject_invalid_key_boundaries(
    snapshot_builder, target, key
):
    with pytest.raises(ValueError):
        build_custom_key_target(snapshot_builder, target, key)


def build_custom_key_target(snapshot_builder, target, key):
    builder, latest = snapshot_builder
    route = "system_metric" if target == "ordinary-breakdown" else "custom_attribute"
    metric_key = key if target == "metric" else "metric.key"
    breakdowns = [] if target == "metric" else [breakdown(key)]
    if target == "fallback-breakdown":
        breakdowns.append(breakdown("second.key"))
    return build(
        builder,
        route,
        latest_state=latest,
        metric_options=custom_metric(metric_key)
        if route == "custom_attribute"
        else None,
        breakdowns=breakdowns,
    )


@pytest.mark.parametrize(
    "target", ["metric", "ordinary-breakdown", "scalar-breakdown", "fallback-breakdown"]
)
@pytest.mark.parametrize(
    "key", ["a" * 4096, "É" * 2048], ids=["ascii-4096-bytes", "utf8-4096-bytes"]
)
def test_bound_custom_routes_accept_exact_4096_byte_keys(snapshot_builder, target, key):
    sql, params = build_custom_key_target(snapshot_builder, target, key)
    name = "custom_metric_attr_key" if target == "metric" else "_custom_bd_key_0"
    assert_bound(sql, params, key, name)


@pytest.mark.parametrize("kind", ["string", "boolean", "array", "map"])
def test_custom_key_validation_does_not_relax_metric_type_or_aggregation_rules(
    snapshot_builder, kind
):
    builder, latest = snapshot_builder
    with pytest.raises(
        ValueError, match="can't be applied|Structured array/map attributes"
    ):
        build(
            builder,
            "custom_attribute",
            latest_state=latest,
            metric_options={**custom_metric("metric.客户", kind), "aggregation": "avg"},
        )


@pytest.mark.parametrize(
    "key", ["country", "final_status", "Customer.ID", "abc-123_xyz"]
)
def test_strict_sql_literal_key_helper_still_accepts_only_safe_ascii_examples(key):
    assert _sanitize_attr_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "客户",
        "key'",
        "key\\",
        "line\nkey",
        "tab\tkey",
        "control\x00key",
        "zero\u200bwidth",
        "\ud800",
    ],
)
def test_strict_sql_literal_key_helper_still_rejects_data_only_keys(key):
    with pytest.raises(ValueError, match="Invalid attribute key"):
        _sanitize_attr_key(key)


@pytest.mark.parametrize("key", ["latency", "source", "dataset", "model"])
def test_eval_native_breakdown_aliases_remain_case_insensitive(builder, key):
    lower = build(builder, "eval_metric", breakdowns=[breakdown(key, "system_metric")])
    upper = build(
        builder, "eval_metric", breakdowns=[breakdown(key.upper(), "system_metric")]
    )
    assert lower == upper
    assert "_ev_bd_attr_key_0" not in lower[1]


def test_eval_multiple_breakdowns_legacy_and_canonical_filters_do_not_collide(builder):
    sql, params = build(
        builder,
        "eval_metric",
        breakdowns=[breakdown("span_attr_str"), breakdown("Customer.ID")],
        filters=[
            custom_filter("first", op="is_set"),
            custom_filter("second", op="contains", value=["00123", "ABC"]),
        ],
        global_filters=[
            canonical_filter("canonical.客户"),
            custom_filter("third", "number", "greater_than", 3),
        ],
    )
    for name, key in {
        "_ev_bd_attr_key_0": "span_attr_str",
        "_ev_bd_attr_key_1": "Customer.ID",
        "_evf_1_attr_key": "second",
        "latest_filter_key_2": "canonical.客户",
        "_evf_3_attr_key": "third",
    }.items():
        assert_bound(sql, params, key, name)
    # Legacy eval no-value operators remain skipped; canonical semantics remain independent.
    assert "_evf_0_attr_key" not in params
    assert params["_evf_1_val"] == ["00123", "ABC"]
    assert params["_evf_3_val"] == 3
    assert "concat(toString(if(" in sql and ", ' / ', " in sql


@pytest.mark.parametrize("route", ["system_metric", "annotation_metric"])
def test_no_value_filters_have_distinct_keys_without_advancing_value_counters(
    builder, route
):
    filters = [
        custom_filter("first", op="is_set", value=None),
        custom_filter("second", op="is_not_set", value=None),
        custom_filter("third", "number", "is_numeric", None),
        custom_filter("fourth", "number", "is_not_numeric", None),
        canonical_filter("canonical.客户"),
        custom_filter("sixth", "number", "not_between", [2, 7]),
        custom_filter("seventh", op="not_contains", value=["00123", "ABC"]),
    ]
    sql, params = build(builder, route, global_filters=filters[:2], filters=filters[2:])
    for index, (key, kind, op) in enumerate(
        [
            ("first", "string", "!= ''"),
            ("second", "string", "= ''"),
            ("third", "number", "!= 0"),
            ("fourth", "number", "= 0"),
        ]
    ):
        name = filter_key_name(route, index)
        assert_bound(sql, params, key, name, column(builder, kind))
        assert f"[%({name})s] {op}" in sql
    canonical_index = 0 if route == "system_metric" else 4
    assert_bound(sql, params, "canonical.客户", f"latest_filter_key_{canonical_index}")
    assert_bound(
        sql, params, "sixth", filter_key_name(route, 5), column(builder, "number")
    )
    assert_bound(
        sql, params, "seventh", filter_key_name(route, 6), column(builder, "string")
    )
    low, high, values = (
        ("f_1_lo", "f_1_hi", "f_2_val")
        if route == "system_metric"
        else (
            "_ann_span_filter_5_low",
            "_ann_span_filter_5_high",
            "_ann_span_filter_6_value",
        )
    )
    assert params[low] == 2 and params[high] == 7
    assert params[values] == ["00123", "ABC"]
    assert f"NOT BETWEEN %({low})s AND %({high})s" in sql
    assert f"NOT IN %({values})s" in sql


def test_legacy_skipped_and_empty_filters_do_not_reuse_key_namespace(builder):
    filters = [
        custom_filter("empty", value=None),
        custom_filter("first", op="is_set"),
        custom_filter("empty-list", value=[]),
        custom_filter("bad-range", op="between", value=[1]),
        custom_filter("second", op="is_not_set"),
        custom_filter("unused", op="unknown"),
        custom_filter("third"),
        {**custom_filter("foreign-source"), "source": "simulation"},
    ]
    sql, params = build(builder, "system_metric", filters=filters)
    for index, key in ((1, "first"), (4, "second"), (6, "third")):
        assert_bound(sql, params, key, filter_key_name("system_metric", index))
    for index in (0, 2, 3, 5, 7):
        assert f"%(_legacy_attr_key_{index})s" not in sql
    assert params["f_0_val"] == "00123"
    assert "f_1_val" not in params


@pytest.mark.parametrize("route", ["system_metric", "eval_metric", "annotation_metric"])
@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("text", "is_null", None),
        ("array", "contains", ["00123", 2, True]),
        ("map", "contains", {"客户'": "00123", "enabled": True}),
    ],
)
def test_canonical_null_and_structured_filters_keep_their_compiler_path(
    builder, route, kind, op, value
):
    key = "canonical.客户"
    sql, params = build(
        builder,
        route,
        filters=[canonical_filter(key, kind, op, value), custom_filter("legacy.after")],
    )
    assert_bound(sql, params, key, "latest_filter_key_0")
    assert_bound(sql, params, "legacy.after", filter_key_name(route, 1))
    assert filter_key_name(route, 0) not in params
    if kind in ("array", "map"):
        assert "JSONHas(" in sql and "JSONType(" in sql
    else:
        assert "mapContains(" in sql


@pytest.mark.parametrize("route", ["system_metric", "eval_metric", "annotation_metric"])
def test_canonical_array_null_member_remains_invalid(builder, route):
    with pytest.raises(ValueError, match="requires non-empty JSON scalar values"):
        build(
            builder,
            route,
            filters=[canonical_filter("array.key", "array", "contains", [None])],
        )


@pytest.mark.parametrize(
    "route", ["system_metric", "eval_metric", "annotation_metric", "eval_breakdown"]
)
@pytest.mark.parametrize(
    "key",
    ["", "\ud800", "a" * 4097, "é" * 2049],
    ids=["empty", "invalid-utf8", "oversize-ascii", "oversize-utf8"],
)
def test_invalid_data_keys_fail_in_full_builder(builder, route, key):
    with pytest.raises(ValueError):
        if route == "eval_breakdown":
            build(builder, "eval_metric", breakdowns=[breakdown(key)])
        else:
            build(builder, route, filters=[custom_filter(key)])


@pytest.mark.parametrize(
    "route", ["system_metric", "eval_metric", "annotation_metric", "eval_breakdown"]
)
def test_utf8_byte_limit_inclusive_without_case_or_unicode_normalization(
    builder, route
):
    key = "É" * 2048
    if route == "eval_breakdown":
        sql, params = build(builder, "eval_metric", breakdowns=[breakdown(key)])
        name = "_ev_bd_attr_key_0"
    else:
        sql, params = build(builder, route, filters=[custom_filter(key)])
        name = filter_key_name(route)
    assert_bound(sql, params, key, name, column(builder, "string"))
