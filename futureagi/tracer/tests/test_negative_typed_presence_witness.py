"""Necessary typed presence is acquisition metadata, never negative truth.

Engine fixtures use an isolated CH25 ReplacingMergeTree, not a database socket.
"""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine


@pytest.fixture
def negative_engine(request):
    return request.getfixturevalue("engine")


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def leaf(kind="number", op="not_between", value=(1, 3), *, types=None, **config):
    item = {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": list(value) if isinstance(value, tuple) else value,
            **config,
        },
    }
    if types is not None:
        item["filter_config"]["attribute_value_types"] = types
    return item


NEGATIVES = [
    ("text", "not_equals", "Rejected", "span_attr_str"),
    ("text", "not_in", ["Rejected", "Approved"], "span_attr_str"),
    ("text", "not_contains", "ject", "span_attr_str"),
    ("number", "not_equals", 7, "span_attr_num"),
    ("number", "not_in", [7, 9], "span_attr_num"),
    ("number", "not_between", [1, 3], "span_attr_num"),
    ("boolean", "not_equals", True, "span_attr_bool"),
    ("boolean", "not_in", [True], "span_attr_bool"),
]


@pytest.mark.parametrize("kind,op,value,column", NEGATIVES)
def test_negative_presence_changes_only_span_acquisition_metadata(
    kind, op, value, column
):
    item = leaf(kind, op, value)
    plan = compile_span_filter_plans([item])[0]
    unchanged = compile_trace_filter_plans([item])[0]
    for name in ("aggregates", "predicate", "seed_predicate", "params"):
        assert getattr(plan, name) == getattr(unchanged, name)
    witness = (
        f"(indexHint(has(mapKeys({column}), %(latest_filter_key_0)s)) AND "
        f"has({column}.keys, %(latest_filter_key_0)s))"
    )
    assert plan.raw_witness_predicate == plan.raw_key_witness_predicate == witness
    assert plan.raw_witness_rank == 10
    assert plan.raw_graph_value_witness_predicate is None
    assert unchanged.raw_witness_predicate is None

    grouped = compile_span_filter_plans([item], group_attribute_nulls=True)[0]
    assert grouped.raw_witness_predicate is None
    assert grouped.raw_key_witness_predicate is None
    assert grouped.raw_graph_value_witness_predicate is None
    assert unchanged.raw_key_witness_predicate is None
    assert not plan.exclude_group_matches


@pytest.mark.parametrize(
    "values,types,columns",
    [
        ([1], ["number"], ["span_attr_num"]),
        ([True], ["boolean"], ["span_attr_bool"]),
        ([1, True], ["number", "boolean"], ["span_attr_num", "span_attr_bool"]),
        (["x", 1], ["string", "number"], ["span_attr_str", "span_attr_num"]),
        (
            ["x", 1, True],
            ["string", "number", "boolean"],
            ["span_attr_str", "span_attr_num", "span_attr_bool"],
        ),
    ],
)
def test_picker_not_in_witness_is_union_of_selected_domains(values, types, columns):
    item = leaf("text", "not_in", values, types=types)
    plan = compile_span_filter_plans([item])[0]
    unchanged = compile_trace_filter_plans([item])[0]
    for name in ("aggregates", "predicate", "seed_predicate", "params"):
        assert getattr(plan, name) == getattr(unchanged, name)
    branches = [
        f"(indexHint(has(mapKeys({column}), %(latest_filter_key_0)s)) "
        f"AND has({column}.keys, %(latest_filter_key_0)s))"
        for column in columns
    ]
    assert plan.raw_witness_predicate == f"({' OR '.join(branches)})"
    assert plan.raw_key_witness_predicate == plan.raw_witness_predicate
    assert plan.raw_witness_rank == 10
    assert plan.raw_graph_value_witness_predicate is None
    assert unchanged.raw_witness_predicate is None


@pytest.mark.parametrize("kind,op,value,column", NEGATIVES)
def test_grouped_scalar_negatives_keep_existing_acquisition(kind, op, value, column):
    plan = compile_span_filter_plans(
        [leaf(kind, op, value)], group_attribute_nulls=True
    )[0]
    assert plan.raw_witness_predicate is None
    assert plan.raw_key_witness_predicate is None
    assert plan.raw_graph_value_witness_predicate is None


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("kind", ["number", "text", "boolean"])
def test_absence_does_not_gain_presence_witness(grouped, kind):
    plan = compile_span_filter_plans(
        [leaf(kind, "is_null", None)], group_attribute_nulls=grouped
    )[0]
    assert plan.raw_witness_predicate is None
    assert plan.raw_key_witness_predicate is None
    assert plan.exclude_group_matches is grouped


@pytest.mark.parametrize(
    "key,kind,value",
    [
        ("model", "text", "blocked"),
        ("latency", "number", 7),
        ("raw_system_fallback", "number", 7),
    ],
)
def test_native_negative_alias_does_not_gain_raw_witness(key, kind, value):
    item = leaf(kind, "not_equals", value, col_type="SYSTEM_METRIC")
    item["column_id"] = key
    plan = compile_span_filter_plans([item])[0]
    assert plan.raw_witness_predicate is None
    assert plan.raw_key_witness_predicate is None


def _builder(item, **kwargs):
    return SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter(), item],
        bounded_internal_scan=True,
        **kwargs,
    )


def _seed_query(target):
    return target.build_filter_seed_page(
        slice_start=START, slice_end=START + timedelta(hours=1), limit=100
    )


@pytest.mark.parametrize(
    "kind,op,value,column,passing,blocked",
    [
        ("number", "not_equals", 7, "attrs_number", 0, 7),
        ("number", "not_in", [7, 9], "attrs_number", 0, 7),
        ("number", "not_between", [1, 3], "attrs_number", 0, 2),
        # The physical boolean Map stores UInt8; the public wire remains bool.
        ("boolean", "not_equals", True, "attrs_bool", 0, 1),
        ("boolean", "not_in", [True], "attrs_bool", 0, 1),
    ],
)
def test_engine_negative_presence_retains_latest_winner_and_full_identity(
    negative_engine, kind, op, value, column, passing, blocked
):
    execute, insert = negative_engine

    def add(identity, attr_value=None, **kwargs):
        maps = {"attrs_number": {}, "attrs_bool": {}, "attrs_string": {}}
        if attr_value is not None:
            maps[column] = {"tag": attr_value}
        insert(id=identity, **(maps | kwargs))

    add("missing")
    add("wrong-type", attrs_string={"tag": "0"})
    add("passing", passing)
    add("blocked", blocked)
    for identity, replacement in [
        ("removed", {column: {}}),
        ("deleted", {"is_deleted": 1}),
        ("changed-value", {column: {"tag": blocked}}),
        ("changed-type", {column: {}, "attrs_string": {"tag": "0"}}),
    ]:
        add(identity, passing)
        add(
            identity,
            passing,
            _version=2,
            start_time=START + timedelta(minutes=25),
            **replacement,
        )
    add("new-match", blocked)
    add("new-match", passing, _version=2, start_time=START + timedelta(minutes=25))
    # Same external IDs, distinct six-part physical identities must survive.
    add("collision", passing)
    add("collision", passing, service_name="service-b")
    add("collision", passing, observation_type="other")
    add("outside", passing, project_id=OTHER_PROJECT)

    target = _builder(leaf(kind, op, value))
    sql, params = _seed_query(target)
    assert "SELECT DISTINCT project_id" in sql
    assert f"has({column}.keys," in sql
    raw_prefix = sql.split(") AS latest_seed_spans", 1)[0]
    assert "latest_filter_param_" not in raw_prefix
    assert any(key.startswith("latest_filter_param_") for key in params)
    candidates = execute(sql, params)
    # Raw prefix acquisition is necessary key presence only. The ordinary V2
    # seed then excludes negative-value rejects after resolving all versions.
    assert {row["id"] for row in candidates} == {
        "passing",
        "new-match",
        "collision",
    }
    rows = execute(*target.build_filter_match_query_from_seed_rows(candidates))
    expected = {
        ("passing", "span", "service-a"),
        ("new-match", "span", "service-a"),
        ("collision", "span", "service-a"),
        ("collision", "span", "service-b"),
        ("collision", "other", "service-a"),
    }
    assert {
        (row["id"], row["observation_type"], row["service_name"]) for row in rows
    } == expected
    assert all(row["project_id"] == PROJECT for row in rows)
    assert next(row for row in rows if row["id"] == "new-match")["_version"] == 2


def test_engine_picker_negative_union_rejects_any_selected_positive_and_missing(
    negative_engine,
):
    execute, insert = negative_engine
    payloads = {
        "missing": {},
        "wrong-type": {"attrs_string": {"tag": "0"}},
        "number-only": {"attrs_number": {"tag": 0}},
        "bool-only": {"attrs_bool": {"tag": 0}},
        "both-outside": {"attrs_number": {"tag": 0}, "attrs_bool": {"tag": 0}},
        "number-blocks": {"attrs_number": {"tag": 1}, "attrs_bool": {"tag": 0}},
        "bool-blocks": {"attrs_number": {"tag": 0}, "attrs_bool": {"tag": 1}},
    }
    for identity, payload in payloads.items():
        insert(id=identity, **({"attrs_number": {}} | payload))
    for identity, replacement in [
        ("removed", {"attrs_number": {}}),
        ("deleted", {"attrs_number": {"tag": 0}, "is_deleted": 1}),
        ("type-corrected", {"attrs_number": {}, "attrs_bool": {"tag": 0}}),
        ("newly-blocked", {"attrs_number": {}, "attrs_bool": {"tag": 1}}),
    ]:
        insert(id=identity, attrs_number={"tag": 0})
        insert(id=identity, _version=2, **replacement)
    target = _builder(leaf("text", "not_in", [1, True], types=["number", "boolean"]))
    candidates = execute(*_seed_query(target))
    assert {row["id"] for row in candidates} == {
        "number-only",
        "bool-only",
        "both-outside",
        "type-corrected",
    }
    rows = execute(*target.build_filter_match_query_from_seed_rows(candidates))
    assert {row["id"] for row in rows} == {
        "number-only",
        "bool-only",
        "both-outside",
        "type-corrected",
    }
