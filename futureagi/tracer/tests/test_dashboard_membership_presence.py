"""Service-free SQL contracts for dashboard annotation/eval memberships.

Compile the real base and V2 builders without ORM fixtures or query execution.
The dashboard normalizer maps UI is_not_null/is_null to is_set/is_not_set;
these tests exercise that canonical builder input. SQL predicates and bindings
are evidence of compilation only, not ClickHouse result or browser behavior.
Eval presence comes from typed raw output in double-encoded config: the real
materialized eval_score is nonnullable Float64 and cannot distinguish no output
from numeric zero. No nullable synthetic eval schema is used here.
"""

import re
import socket
from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.eval_expressions import (
    EVAL_FALSY_OUTPUTS,
    EVAL_TRUTHY_OUTPUTS,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.schema import (
    CDC_USAGE_APICALLLOG,
    CH_EVAL_SCORE_EXPR,
    EVAL_OUTPUT_JSON_ARGS,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = "11111111-1111-4111-8111-111111111111"
ORGANIZATION = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"
LABEL = "44444444-4444-4444-8444-444444444444"
TEMPLATE = "55555555-5555-4555-8555-555555555555"
MODES = ("base", "v2-physical", "v2-latest")
ANNOTATION_KINDS = ("numeric", "star", "text", "thumbs_up_down", "categorical")
EVAL_KINDS = ("SCORE", "CHOICE", "CHOICES", "PASS_FAIL")
MEMBERSHIP_KINDS = tuple(("annotation_metric", k) for k in ANNOTATION_KINDS) + tuple(
    ("eval_metric", k) for k in EVAL_KINDS
)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("dashboard membership compilation cannot use sockets")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def membership_filter(family, kind, operator="is_set", **extra):
    return {
        "metric_type": family,
        "metric_name": LABEL if family == "annotation_metric" else TEMPLATE,
        "source": "traces",
        "output_type": kind,
        "operator": operator,
        **extra,
    }


def config_for(item=None, placement="global"):
    config = {
        "organization_id": ORGANIZATION,
        "workspace_id": WORKSPACE,
        "project_ids": [PROJECT],
        "time_range": {
            "custom_start": "2026-09-09T00:00:00Z",
            "custom_end": "2026-09-11T00:00:00Z",
        },
        "granularity": "day",
        "metrics": [
            {
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
                "filters": [],
            }
        ],
        "filters": [],
    }
    if item is not None:
        target = config if placement == "global" else config["metrics"][0]
        target["filters"] = [item]
    return config


def builder_for(config, mode):
    cls = DashboardQueryBuilder if mode == "base" else DashboardQueryBuilderV2
    builder = cls(config)
    builder._latest_state_spans_required = mode == "v2-latest"
    return builder


def compact(sql):
    return " ".join(sql.split())


def compile_metric(config, mode):
    before = deepcopy(config)
    try:
        sql, params = builder_for(config, mode).build_metric_query(config["metrics"][0])
    finally:
        # Includes source, operator, nested values and the caller's filter lists,
        # even when compilation rejects the request.
        assert config == before
    assert set(re.findall(r"%\(([^)]+)\)s", sql)) <= params.keys()
    return compact(sql), params


def numeric_expression(alias):
    # Keep this expected nullable rating/value contract independent of the
    # production expression helper: a default-zero extraction would hide absence.
    return (
        f"if(JSONHas({alias}.value, 'rating'), "
        f"JSONExtract({alias}.value, 'rating', 'Nullable(Float64)'), "
        f"JSONExtract({alias}.value, 'value', 'Nullable(Float64)'))"
    )


def assert_nonempty(sql, expression):
    assert any(
        predicate in sql
        for predicate in (f"notEmpty({expression})", f"{expression} != ''")
    ), f"missing nonempty-value predicate for {expression}"


def assert_annotation_presence(sql, kind, prefix="s_", index=0):
    alias = f"annotation_{prefix}filter_{index}"
    if kind in ("numeric", "star"):
        expression = numeric_expression(alias)
        assert f"{expression} IS NOT NULL" in sql
        # Zero is present; neither score/rating may be tested for truthiness.
        assert f"{expression} != 0" not in sql
        assert f"{expression} > 0" not in sql
        assert "JSONExtractFloat" not in sql
    elif kind == "categorical":
        expression = f"JSONExtract({alias}.value, 'selected', 'Array(String)')"
        assert f"notEmpty({expression})" in sql
        assert f"has({expression}," not in sql
    else:
        key = "value" if kind == "thumbs_up_down" else "text"
        expression = f"JSONExtract({alias}.value, '{key}', 'Nullable(String)')"
        assert f"{expression} IS NOT NULL" in sql
        assert_nonempty(sql, expression)
    assert f"{expression} IS NULL" not in sql


def assert_annotation_scope(sql, params, mode, prefix="s_", index=0):
    alias = f"annotation_{prefix}filter_{index}"
    assert f"FROM model_hub_score AS {alias} FINAL PREWHERE" in sql
    assert f"{alias}.label_id = toUUID(%({prefix}label_id_{index})s)" in sql
    assert f"{alias}.organization_id = toUUID(%({prefix}ann_org_id_{index})s)" in sql
    assert params[f"{prefix}label_id_{index}"] == LABEL
    assert params[f"{prefix}ann_org_id_{index}"] == ORGANIZATION
    for predicate in (
        "created_at >= %(start_date)s",
        "created_at < %(end_date)s",
        "_peerdb_is_deleted = 0",
        "deleted = 0",
    ):
        assert f"{alias}.{predicate}" in sql
    assert f"{alias}.is_deleted" not in sql  # Score retains the legacy CDC schema.
    assert "SELECT DISTINCT annotation_membership.trace_id" in sql
    assert "WHERE annotation_membership.trace_id != ''" in sql
    scan = f"annotation_{prefix}span_filter_scan_{index}"
    latest = f"annotation_{prefix}span_filter_latest_{index}"
    assert f"{scan}.project_id IN %(project_ids)s" in sql
    assert f"GROUP BY {scan}.id" in sql
    assert f"{latest}.identity_count = 1" in sql
    assert f"tupleElement( {latest}.latest_state, 2 ) = 0" in sql
    assert "UNION ALL" in sql  # Trace-attached and child-span-attached Scores.
    if mode == "base":
        assert "'trace_dict', 'project_id', direct_annotation.trace_id" in sql
        assert ") IN %(project_ids)s" in sql
        assert f"{scan}._peerdb_is_deleted" in sql
        assert f"{scan}._peerdb_version" in sql
    else:
        assert "trace_project_scan.project_id IN %(project_ids)s" in sql
        assert "WHERE project_identity_count = 1" in sql
        assert "AND tupleElement(latest_state, 2) = 0" in sql
        assert f"{scan}.is_deleted" in sql
        assert f"{scan}._version" in sql


def assert_eval_presence(sql, kind, prefix="s_", index=0):
    alias = f"usage_{prefix}eval_filter_latest_{index}"
    score = f"{alias}.eval_score"
    json_args = f"JSONExtractString({alias}.config), 'output', 'output'"
    number_types = "('Double', 'Int64', 'UInt64')"
    numeric = (
        f"(JSONType({json_args}) IN {number_types} OR "
        f"(JSONType({json_args}, 'score') IN {number_types}))"
    )
    live = sql.split(f"WHERE {alias}._peerdb_is_deleted = 0", 1)[1]
    if kind == "SCORE":
        condition = numeric
    elif kind in ("CHOICE", "CHOICES"):
        condition = (
            f"((JSONType({json_args}) = 'String' AND "
            f"notEmpty(JSONExtractString({json_args}))) OR "
            f"(JSONType({json_args}, 'choice') = 'String' AND "
            f"notEmpty(JSONExtractString({json_args}, 'choice'))) OR "
            f"(JSONType({json_args}) = 'Array' AND arrayExists(choice_value -> "
            "JSONType(choice_value) = 'String' AND "
            "notEmpty(JSONExtractString(choice_value)), "
            f"JSONExtractArrayRaw({json_args}))) OR "
            f"(JSONType({json_args}, 'choices') = 'Array' AND "
            "arrayExists(choice_value -> JSONType(choice_value) = 'String' AND "
            "notEmpty(JSONExtractString(choice_value)), "
            f"JSONExtractArrayRaw({json_args}, 'choices'))))"
        )
        # Array(String) can stringify numeric elements. Each raw element must
        # instead be an actual nonempty JSON string; null/numeric/empty-string
        # only arrays provide no membership witness.
        assert f"JSONExtract({json_args}, 'Array(String)')" not in live
        assert f"JSONExtract({json_args}, 'choices', 'Array(String)')" not in live
    else:
        tokens = (
            "("
            + ", ".join(
                f"'{token}'" for token in EVAL_TRUTHY_OUTPUTS + EVAL_FALSY_OUTPUTS
            )
            + ")"
        )
        condition = (
            f"({numeric} OR JSONType({json_args}) = 'Bool' OR "
            f"lower(JSONExtractString({json_args})) IN {tokens})"
        )
    # Require the entire grouped type predicate after latest-live/status
    # filtering. Checking isolated JSONType substrings would permit an extra
    # OR eval_score IS NOT NULL branch to admit every default-zero row.
    marker = f"AND {alias}.eval_trace_id != '' AND "
    compiled = live.split(marker, 1)[1]
    assert compiled.startswith(condition)
    suffix = compiled[len(condition) :].lstrip()
    assert suffix.startswith(
        (")", f"AND {score} BETWEEN ", f"AND {score} NOT BETWEEN ")
    ), "typed presence must be complete, with no additional OR fallback"
    assert f"{score} IS NOT NULL" not in live
    assert f"isNotNull({score})" not in live
    assert f"{score} > 0" not in live
    assert f"{score} != 0" not in live
    assert f"notEmpty({alias}.eval_output_str)" not in live
    assert f"JSONHas({json_args}, 'score')" not in live
    return condition


def assert_eval_scope(sql, params, workspace=True, prefix="s_", index=0):
    scan = f"usage_{prefix}eval_filter_scan_{index}"
    latest = f"usage_{prefix}eval_filter_latest_{index}"
    scope = "workspace_id" if workspace else "organization_id"
    assert f"FROM usage_apicalllog AS {scan} PREWHERE" in sql
    assert f"{scan}.{scope} = toUUID(%({prefix}scope_id_{index})s)" in sql
    assert f"{scan}.source_id = %({prefix}eval_id_{index})s" in sql
    assert params[f"{prefix}scope_id_{index}"] == (
        WORKSPACE if workspace else ORGANIZATION
    )
    assert params[f"{prefix}eval_id_{index}"] == TEMPLATE
    assert f"{scan}.created_at >= %(start_date)s" in sql
    assert f"{scan}.created_at < %(end_date)s" in sql
    boundary = f"ORDER BY {scan}._peerdb_version DESC LIMIT 1 BY {scan}.id"
    assert boundary in sql
    replay, live = sql.split(boundary, 1)
    projection = replay.split(f"FROM usage_apicalllog AS {scan}", 1)[0].rsplit(
        "SELECT ", 1
    )[1]
    projected = {column.strip() for column in projection.split(",")}
    assert f"{scan}.config" in projected
    assert f"{scan}.eval_score" in projected
    assert f"{scan}.eval_output_str" in projected
    assert f"{scan}.*" not in projected
    assert f"JSONExtractString({latest}.config), 'output', 'output'" in live
    assert f"JSONType(JSONExtractString({scan}.config)" not in replay
    for predicate in ("_peerdb_is_deleted = 0", "deleted = 0", "status = 'success'"):
        assert f"{scan}.{predicate}" not in replay
        assert f"{latest}.{predicate}" in live
    assert f"{latest}.eval_trace_id != ''" in live
    assert f"{latest}.is_deleted" not in sql
    assert f"{scan}._version" not in sql


def test_eval_presence_contract_matches_nonnullable_materialized_physical_schema():
    schema = compact(CDC_USAGE_APICALLLOG)
    assert "config String DEFAULT '{}'" in schema
    assert f"eval_score Float64 MATERIALIZED {CH_EVAL_SCORE_EXPR}" in schema
    assert "eval_score Nullable(" not in schema
    assert EVAL_OUTPUT_JSON_ARGS == "JSONExtractString(config), 'output', 'output'"
    assert (
        "JSONType(JSONExtractString(config), 'output', 'output', 'score') "
        "IN ('Double', 'Int64', 'UInt64')"
    ) in CH_EVAL_SCORE_EXPR


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("placement", ("global", "metric"))
@pytest.mark.parametrize("operator", ("is_set", "is_not_set"))
@pytest.mark.parametrize("family,kind", MEMBERSHIP_KINDS)
def test_presence_compiles_typed_scoped_membership(
    mode, placement, operator, family, kind
):
    config = config_for(membership_filter(family, kind, operator), placement)
    sql, params = compile_metric(config, mode)
    membership = "NOT IN" if operator == "is_not_set" else "IN"
    assert f"trace_id {membership} (" in sql
    assert "project_id IN %(project_ids)s" in sql
    assert "start_time >= %(start_date)s" in sql
    assert "start_time < %(end_date)s" in sql
    assert params["project_ids"] == [PROJECT]
    assert params["start_date"] == datetime(2026, 9, 9, tzinfo=UTC)
    assert params["end_date"] == datetime(2026, 9, 11, tzinfo=UTC)
    assert not any(key.endswith("_val") for key in params)
    assert "%(s_0_val)s" not in sql
    if family == "annotation_metric":
        assert_annotation_presence(sql, kind)
        assert_annotation_scope(sql, params, mode)
    else:
        assert_eval_presence(sql, kind)
        assert_eval_scope(sql, params)
    if mode == "v2-latest":
        assert "FROM spans AS finalized FINAL" in sql
    elif mode == "v2-physical":
        assert "FROM spans AS finalized FINAL" not in sql


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family,kind", MEMBERSHIP_KINDS)
def test_not_set_is_complement_of_present_trace_membership(mode, family, kind):
    present, present_params = compile_metric(
        config_for(membership_filter(family, kind, "is_set")), mode
    )
    absent, absent_params = compile_metric(
        config_for(membership_filter(family, kind, "is_not_set")), mode
    )
    assert "trace_id IN (" in present
    assert "trace_id NOT IN (" in absent
    # The complete scoped latest-live present-value relation is identical.
    # A row-level IS NULL cannot admit traces with no Score/eval row at all.
    assert absent == present.replace("trace_id IN (", "trace_id NOT IN (", 1)
    assert absent_params == present_params


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operator", ("is_set", "is_not_set"))
@pytest.mark.parametrize("kind", EVAL_KINDS)
def test_eval_presence_without_workspace_uses_organization_scope(mode, operator, kind):
    config = config_for(membership_filter("eval_metric", kind, operator))
    config.pop("workspace_id")
    sql, params = compile_metric(config, mode)
    assert_eval_presence(sql, kind)
    assert_eval_scope(sql, params, workspace=False)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("grouped", (False, True))
def test_global_and_metric_presence_conjoin_without_alias_or_parameter_collisions(
    mode, grouped
):
    config = config_for(membership_filter("eval_metric", "PASS_FAIL"))
    config["metrics"][0]["filters"] = [
        membership_filter("annotation_metric", "text", "is_not_set")
    ]
    if grouped:
        config["breakdowns"] = [
            {"name": LABEL, "type": "annotation_metric", "output_type": "text"}
        ]
    sql, params = compile_metric(config, mode)
    outer = "s.trace_id" if grouped else "trace_id"
    assert f"{outer} IN ( SELECT usage_s_eval_filter_latest_0.eval_trace_id" in sql
    assert f"{outer} NOT IN ( WITH annotation_s_candidates_1 AS" in sql
    assert_eval_presence(sql, "PASS_FAIL")
    assert_eval_scope(sql, params)
    assert_annotation_presence(sql, "text", index=1)
    assert_annotation_scope(sql, params, mode, index=1)
    assert "AS s.trace_id" not in sql
    assert "AS s.project_id" not in sql


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "family,kind", (("annotation_metric", "text"), ("eval_metric", "SCORE"))
)
@pytest.mark.parametrize("placement", ("global", "metric"))
@pytest.mark.parametrize("carrier", ("custom_attribute", "annotation_metric"))
def test_presence_reaches_other_membership_helper_callers(
    mode, family, kind, placement, carrier
):
    config = config_for(membership_filter(family, kind, "is_not_set"), placement)
    metric = config["metrics"][0]
    metric.update(type=carrier, name=LABEL)
    if carrier == "custom_attribute":
        metric.update(attribute_key="customer.score", attribute_type="number")
        prefix = "ca_"
    else:
        metric.update(label_id=LABEL, output_type="numeric")
        prefix = "ann_metric_"
    sql, params = compile_metric(config, mode)
    assert "NOT IN (" in sql
    if family == "annotation_metric":
        assert_annotation_presence(sql, kind, prefix=prefix)
        assert_annotation_scope(sql, params, mode, prefix=prefix)
    else:
        assert_eval_presence(sql, kind, prefix=prefix)
        assert_eval_scope(sql, params, prefix=prefix)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("placement", ("global", "metric"))
@pytest.mark.parametrize("family,kind", MEMBERSHIP_KINDS)
def test_unknown_membership_operator_raises_instead_of_dropping_filter(
    mode, placement, family, kind
):
    config = config_for(
        membership_filter(
            family, kind, "unsupported_membership_operator", value=[0, 5]
        ),
        placement,
    )
    with pytest.raises(InvalidMetricCombinationError):
        compile_metric(config, mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("placement", ("global", "metric"))
@pytest.mark.parametrize("operator", ("between", "not_between"))
@pytest.mark.parametrize(
    "family,kind",
    (
        ("annotation_metric", "numeric"),
        ("annotation_metric", "star"),
        ("eval_metric", "SCORE"),
    ),
)
def test_numeric_range_binds_both_finite_endpoints_without_dropping_filter(
    mode, placement, operator, family, kind
):
    config = config_for(
        membership_filter(family, kind, operator, value=["0", "5"]), placement
    )
    sql, params = compile_metric(config, mode)
    assert "trace_id IN (" in sql
    lower = [key for key, value in params.items() if value == 0]
    upper = [key for key, value in params.items() if value == 5]
    assert lower and upper, "both range endpoints must be separate bound values"
    expression = (
        numeric_expression("annotation_s_filter_0")
        if family == "annotation_metric"
        else "usage_s_eval_filter_latest_0.eval_score"
    )
    token = "NOT BETWEEN" if operator == "not_between" else "BETWEEN"
    assert any(
        f"{expression} {token} %({lo})s AND %({hi})s" in sql
        for lo in lower
        for hi in upper
    )
    for key in (*lower, *upper):
        assert isinstance(params[key], float) and isfinite(params[key])
    assert "s_0_val" not in params
    if family == "annotation_metric":
        assert f"{expression} IS NOT NULL" in sql
        assert_annotation_scope(sql, params, mode)
    else:
        presence = assert_eval_presence(sql, "SCORE")
        assert f"{presence} AND {expression} {token}" in sql
        assert_eval_scope(sql, params)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operator", ("is_set", "is_not_set", "between", "not_between"))
def test_score_presence_excludes_json_null_and_string_scores_by_type(mode, operator):
    extra = {"value": [0, 5]} if operator in ("between", "not_between") else {}
    sql, params = compile_metric(
        config_for(membership_filter("eval_metric", "SCORE", operator, **extra)), mode
    )
    assert_eval_presence(sql, "SCORE")
    assert_eval_scope(sql, params)
    # These are SQL-domain assertions, not Python/nullable-fixture simulations:
    # JSON null, "0", and {"score": "0"} cannot enter either numeric branch.
    json_args = (
        "JSONExtractString(usage_s_eval_filter_latest_0.config), 'output', 'output'"
    )
    type_sets = re.findall(
        rf"JSONType\({re.escape(json_args)}(?:, 'score')?\) IN \(([^)]*)\)", sql
    )
    assert len(type_sets) == 2
    for type_set in type_sets:
        admitted_types = set(re.findall(r"'([^']+)'", type_set))
        assert admitted_types == {"Double", "Int64", "UInt64"}
        assert admitted_types.isdisjoint({"Null", "String", "Bool", "Object", "Array"})
    alias = "usage_s_eval_filter_latest_0"
    assert f"JSONExtractFloat(JSONExtractString({alias}.config)" not in sql


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operator", ("between", "not_between"))
@pytest.mark.parametrize(
    "family,kind", (("annotation_metric", "numeric"), ("eval_metric", "SCORE"))
)
@pytest.mark.parametrize(
    "value",
    (
        None,
        [],
        [0],
        [0, 1, 2],
        "0,5",
        (0, 5),
        [False, 5],
        [0, True],
        [None, 5],
        ["not-a-number", 5],
        ["NaN", 5],
        [0, "Infinity"],
        ["-Infinity", 5],
    ),
)
def test_invalid_numeric_ranges_raise_without_mutating_inputs(
    mode, operator, family, kind, value
):
    config = config_for(membership_filter(family, kind, operator, value=value))
    with pytest.raises(InvalidMetricCombinationError):
        compile_metric(config, mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operator", ("between", "not_between"))
@pytest.mark.parametrize(
    "family,kind",
    (
        ("annotation_metric", "text"),
        ("annotation_metric", "thumbs_up_down"),
        ("annotation_metric", "categorical"),
        ("eval_metric", "CHOICE"),
        ("eval_metric", "CHOICES"),
        ("eval_metric", "PASS_FAIL"),
    ),
)
def test_ranges_reject_nonnumeric_output_types(mode, operator, family, kind):
    config = config_for(membership_filter(family, kind, operator, value=[0, 5]))
    with pytest.raises(InvalidMetricCombinationError):
        compile_metric(config, mode)


@pytest.mark.parametrize("mode", MODES)
def test_membership_helper_ignores_unrelated_filters_and_preserves_all_inputs(mode):
    unrelated = [
        {
            "metric_type": family,
            "metric_name": "latency",
            "source": "datasets",
            "operator": "not_a_membership_operator",
            "value": {"nested": [0, "keep"]},
        }
        for family in ("system_metric", "custom_attribute")
    ]
    filters = [
        unrelated[0],
        membership_filter("annotation_metric", "numeric", value={"unused": [0]}),
        unrelated[1],
        membership_filter("eval_metric", "CHOICE", "is_not_set"),
    ]
    config = config_for()
    params = {"project_ids": [PROJECT], "sentinel": {"nested": ["keep"]}}
    before = deepcopy((config, filters, params))
    builder = builder_for(config, mode)
    try:
        ignored, ignored_params = builder._build_subquery_filters(
            unrelated, params, "probe_"
        )
        clauses, extra = builder._build_subquery_filters(
            filters, params, "probe_", trace_id_expr="subject.trace_id"
        )
    finally:
        assert (config, filters, params) == before
    assert (ignored, ignored_params) == ([], {})
    assert len(clauses) == 2
    assert clauses[0].startswith("subject.trace_id IN (")
    assert clauses[1].startswith("subject.trace_id NOT IN (")
    assert extra == {
        "probe_label_id_0": LABEL,
        "probe_ann_org_id_0": ORGANIZATION,
        "probe_eval_id_1": TEMPLATE,
        "probe_scope_id_1": WORKSPACE,
    }


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "family,alias,canonical",
    (
        ("annotation_metric", "number", "numeric"),
        ("annotation_metric", "score", "numeric"),
        ("annotation_metric", "rating", "star"),
        ("annotation_metric", "string", "text"),
        ("annotation_metric", "choice", "categorical"),
        ("annotation_metric", "choices", "categorical"),
        ("annotation_metric", "TEXT", "text"),
        ("eval_metric", "score", "SCORE"),
        ("eval_metric", "pass_fail", "PASS_FAIL"),
    ),
)
def test_output_type_aliases_preserve_presence_sql_and_bindings(
    mode, family, alias, canonical
):
    alias_sql, alias_params = compile_metric(
        config_for(membership_filter(family, alias, "is_not_set")), mode
    )
    canonical_sql, canonical_params = compile_metric(
        config_for(membership_filter(family, canonical, "is_not_set")), mode
    )
    assert "trace_id NOT IN (" in alias_sql
    assert alias_sql == canonical_sql
    assert alias_params == canonical_params


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family,kind", MEMBERSHIP_KINDS)
@pytest.mark.parametrize(
    "alias,canonical", (("is_null", "is_not_set"), ("is_not_null", "is_set"))
)
def test_null_operator_aliases_match_canonical_presence_without_binding_values(
    mode, family, kind, alias, canonical
):
    config = config_for(membership_filter(family, kind, alias, value=None))
    alias_sql, alias_params = compile_metric(config, mode)
    canonical_sql, canonical_params = compile_metric(
        config_for(membership_filter(family, kind, canonical)), mode
    )
    membership = "NOT IN" if canonical == "is_not_set" else "IN"
    assert f"trace_id {membership} (" in alias_sql
    assert alias_sql == canonical_sql
    assert alias_params == canonical_params
    assert not any(key.endswith("_val") for key in alias_params)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("placement", ("global", "metric"))
@pytest.mark.parametrize(
    "kind,operator,value,comparison",
    (
        ("SCORE", "equal_to", 0.0, "="),
        ("SCORE", "greater_than", 0.6, ">"),
        ("CHOICE", "contains", ["approved", "pending"], "IN"),
        ("PASS_FAIL", "equal_to", "Failed", "="),
    ),
)
def test_ordinary_eval_comparisons_do_not_project_or_read_config(
    mode, placement, kind, operator, value, comparison
):
    config = config_for(
        membership_filter("eval_metric", kind, operator, value=value), placement
    )
    sql, params = compile_metric(config, mode)
    scan = "usage_s_eval_filter_scan_0"
    latest = "usage_s_eval_filter_latest_0"
    assert f"trace_id IN ( SELECT {latest}.eval_trace_id" in sql
    projection = sql.split(f"FROM usage_apicalllog AS {scan}", 1)[0].rsplit(
        "SELECT ", 1
    )[1]
    projected = {column.strip() for column in projection.split(",")}
    assert {f"{scan}.eval_score", f"{scan}.eval_output_str"} <= projected
    assert f"{scan}.config" not in projected
    assert "*" not in projection  # A wildcard would silently read config too.
    assert re.search(r"\bconfig\b", sql, flags=re.IGNORECASE) is None
    assert params["s_0_val"] == value
    assert "s_0_val_low" not in params and "s_0_val_high" not in params
    assert params["s_eval_id_0"] == TEMPLATE
    assert params["s_scope_id_0"] == WORKSPACE
    assert f"{scan}.source_id = %(s_eval_id_0)s" in sql
    assert f"{scan}.workspace_id = toUUID(%(s_scope_id_0)s)" in sql
    assert f"{scan}.created_at >= %(start_date)s" in sql
    assert f"{scan}.created_at < %(end_date)s" in sql
    boundary = f"ORDER BY {scan}._peerdb_version DESC LIMIT 1 BY {scan}.id"
    replay, live = sql.split(boundary, 1)
    for predicate in ("_peerdb_is_deleted = 0", "deleted = 0", "status = 'success'"):
        assert f"{scan}.{predicate}" not in replay
        assert f"{latest}.{predicate}" in live
    if kind == "PASS_FAIL":
        expression = (
            "if((eval_score >= 1.0 OR lower(eval_output_str) IN "
            "('passed', 'pass', 'true', '1')), 'Passed', 'Failed')"
        )
    else:
        column = "eval_score" if kind == "SCORE" else "eval_output_str"
        expression = f"{latest}.{column}"
    assert (
        f"AND {latest}.eval_trace_id != '' AND {expression} {comparison} %(s_0_val)s"
    ) in live


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "kind,data,key,json_type",
    (("CHOICE", "A", "choice", "String"), ("CHOICES", ["A"], "choices", "Array")),
)
def test_choice_presence_covers_real_formatter_nested_output(
    mode, kind, data, key, json_type
):
    from evaluations.engine.formatting import format_eval_value

    template = SimpleNamespace(
        config={"output": "choices"},
        choice_scores={"A": 0.0},
        multi_choice=kind == "CHOICES",
        choices=["A"],
    )
    result = {"output": "choices", "data": data}
    before = deepcopy((result, vars(template)))
    formatted = format_eval_value(result, template)
    assert formatted == {"score": 0.0, key: data}
    assert (result, vars(template)) == before
    sql, params = compile_metric(
        config_for(membership_filter("eval_metric", kind)), mode
    )
    assert_eval_presence(sql, kind)
    assert_eval_scope(sql, params)
    json_args = (
        "JSONExtractString(usage_s_eval_filter_latest_0.config), 'output', 'output'"
    )
    # Tie actual formatter output keys to typed, nonempty SQL branches. This
    # does not execute SQL over the formatted value or prove runtime results.
    assert f"JSONType({json_args}, '{key}') = '{json_type}'" in sql
    if json_type == "String":
        assert f"notEmpty(JSONExtractString({json_args}, '{key}'))" in sql
    else:
        assert (
            "arrayExists(choice_value -> JSONType(choice_value) = 'String' AND "
            "notEmpty(JSONExtractString(choice_value)), "
            f"JSONExtractArrayRaw({json_args}, '{key}'))"
        ) in sql
