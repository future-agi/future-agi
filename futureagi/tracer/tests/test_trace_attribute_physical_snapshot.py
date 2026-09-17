"""Page attribute hydration: actual compiler + optional in-process ClickHouse.

Only constant VALUES are read. No network, tables, DDL, FINAL oracle, or
production latency claim. Conflicting equal versions have no unique winner.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.server_readonly import without_query_settings
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
FOREIGN = "33333333-3333-4333-8333-333333333333"
TIME = datetime(2026, 8, 31, 12)
CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))


def builder(*, projects=None):
    scope = {"project_ids": projects} if projects else {"project_id": PROJECT}
    return TraceListQueryBuilderV2(
        **scope,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [TIME, TIME + timedelta(hours=1)],
                },
            }
        ],
    )


def row(
    *,
    trace="trace",
    span="span",
    at=TIME,
    version=1,
    deleted=0,
    project=PROJECT,
    service="svc",
    kind="SPAN",
    extra="{}",
    strings=None,
    numbers=None,
    booleans=None,
    parent="root",
):
    strings, numbers, booleans = strings or {}, numbers or {}, booleans or {}
    return (
        project,
        kind,
        service,
        trace,
        span,
        at.isoformat(sep=" "),
        parent,
        version,
        deleted,
        extra,
        list(strings),
        list(strings.values()),
        list(numbers),
        list(numbers.values()),
        list(booleans),
        list(booleans.values()),
    )


@pytest.fixture(scope="module")
def engine():
    return pytest.importorskip("chdb", reason="optional isolated ClickHouse engine")


def execute(
    engine,
    rows,
    keys=("k",),
    *,
    projects=None,
    identities=None,
    trace_ids=("trace",),
    fanout=1,
):
    query_builder = builder(projects=projects)
    query_builder.TABLE = "fixture_spans"
    sql, params = query_builder.build_span_attributes_query(
        list(trace_ids),
        attribute_keys=keys,
        trace_identities=identities,
    )
    # This method has exactly one PREWHERE and no same-depth WHERE: the
    # original immutable scope predicate is retained in full for VALUES.
    assert sql.count("PREWHERE") == 1
    sql = without_query_settings(sql).replace("PREWHERE", "WHERE", 1)
    rendered = sql % escape_params(params, CONTEXT)
    encoded_rows = escape_params({"rows": tuple(rows)}, CONTEXT)["rows"][1:-1]
    # Constant-generated high fanout avoids a giant literal AST. Only the
    # explicit fanout fixture uses numbers(); it never reads a server table.
    span_id = "concat(base.id, toString(number))" if fanout > 1 else "base.id"
    start = (
        "base.start_time + toIntervalSecond(number)"
        if fanout > 1
        else "base.start_time"
    )
    strings = (
        "arrayMap(v -> if(v = '__index__', toString(number), v), string_values)"
        if fanout > 1
        else "string_values"
    )
    expand = f"CROSS JOIN numbers({fanout}) AS generated" if fanout > 1 else ""
    # Pin the deployed storage timezone even when chdb was imported before
    # Django initialized TZ; quoting is owned by the actual driver encoder.
    columns = """project_id UUID, observation_type String, service_name String,
        trace_id String, id String, start_time DateTime64(6, 'UTC'), parent_span_id String,
        _version UInt64, is_deleted UInt8, attributes_extra Nullable(String),
        string_keys Array(String), string_values Array(String),
        number_keys Array(String), number_values Array(Float64),
        bool_keys Array(String), bool_values Array(UInt8)"""
    encoded_columns = escape_params({"columns": columns}, CONTEXT)["columns"]
    fixture = f"""WITH fixture_spans AS (
        SELECT project_id, observation_type, service_name, trace_id, {span_id} AS id,
            {start} AS start_time, parent_span_id, _version, is_deleted, attributes_extra,
            mapFromArrays(string_keys, {strings}) AS attrs_string,
            mapFromArrays(number_keys, number_values) AS attrs_number,
            mapFromArrays(bool_keys, bool_values) AS attrs_bool
        FROM values({encoded_columns}, {encoded_rows}) AS base {expand}
    ) {rendered}
    SETTINGS max_threads=1, max_execution_time=5, max_memory_usage=268435456,
        max_result_rows=1000, max_result_bytes=1048576,
        result_overflow_mode='throw'
    """
    result = [
        json.loads(line)
        for line in str(engine.query(fixture, "JSONEachRow")).splitlines()
        if line
    ]
    unique = {(r["project_id"], r["trace_id"], r["attribute_key"]) for r in result}
    assert len(unique) == len(result)
    page_size = len(set(identities)) if identities is not None else len(set(trace_ids))
    assert len(result) <= page_size * len(set(keys))
    return {
        (r["project_id"], r["trace_id"], r["attribute_key"]): json.loads(
            r["attribute_value_json"]
        )
        for r in result
    }


def expected(**values):
    return {(PROJECT, "trace", key): value for key, value in values.items()}


def test_query_uses_one_coherent_full_storage_key_winner_and_presentation_order():
    sql, params = builder().build_span_attributes_query(["trace"], ["k", "k", "b"])
    compact = " ".join(sql.split())
    assert (
        "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in compact
    )
    assert (
        "argMax(tuple(start_time, attributes_extra, attrs_string, attrs_number, attrs_bool, is_deleted), _version) AS latest_span"
        in compact
    )
    assert sql.count("argMax(") == 2  # Physical winner + final page/key presentation.
    assert "tuple(latest_start_time, id, observation_type, service_name)" in compact
    assert "WHERE latest_is_deleted = 0" in sql
    assert "WHERE notEmpty(candidate_attribute_value_json)" in sql
    assert "PREWHERE (toString(project_id), trace_id)" in sql
    assert "GROUP BY project_id, trace_id, attribute_key" in sql
    assert params["requested_attribute_keys"] == ["k", "b"]
    for forbidden in (
        "groupArray",
        "LIMIT",
        "start_time >=",
        "start_time <",
        "SAMPLE",
        " FINAL",
    ):
        assert forbidden not in sql


@pytest.mark.parametrize("old_minute,new_minute", [(10, 40), (40, 10)])
def test_timestamp_corrections_collapse_before_key_presence(
    engine, old_minute, new_minute
):
    rows = [
        row(
            at=TIME + timedelta(minutes=old_minute),
            strings={"k": "old", "removed": "stale"},
        ),
        row(at=TIME + timedelta(minutes=new_minute), version=2, strings={"k": "new"}),
    ]
    assert execute(engine, rows, ("k", "removed")) == expected(k="new")


@pytest.mark.parametrize(
    "old_minute,new_minute,winner", [(10, 40, "corrected"), (40, 10, "middle")]
)
def test_presentation_uses_winning_timestamp_not_old_timestamp(
    engine, old_minute, new_minute, winner
):
    rows = [
        row(at=TIME + timedelta(minutes=old_minute), strings={"k": "stale"}),
        row(
            at=TIME + timedelta(minutes=new_minute),
            version=2,
            strings={"k": "corrected"},
        ),
        row(span="middle", at=TIME + timedelta(minutes=25), strings={"k": "middle"}),
    ]
    assert execute(engine, rows) == expected(k=winner)


@pytest.mark.parametrize("old_minute,new_minute", [(10, 40), (40, 10)])
def test_latest_tombstone_after_timestamp_correction_does_not_resurrect(
    engine, old_minute, new_minute
):
    rows = [
        row(at=TIME + timedelta(minutes=old_minute), strings={"k": "deleted"}),
        row(
            at=TIME + timedelta(minutes=new_minute),
            version=2,
            deleted=1,
            strings={"k": "deleted"},
        ),
    ]
    assert execute(engine, rows) == {}
    rows.append(row(span="survivor", strings={"k": "live"}))
    assert execute(engine, rows) == expected(k="live")


@pytest.mark.parametrize(
    "change",
    [
        {"service": "other"},
        {"kind": "GENERATION"},
        {"at": TIME + timedelta(hours=1)},
    ],
)
def test_storage_identity_collisions_remain_independent(engine, change):
    rows = [row(strings={"k": "retained"}), row(version=99, deleted=1, **change)]
    assert execute(engine, rows) == expected(k="retained")


@pytest.mark.parametrize(
    "low,high",
    [
        ({"service": "a"}, {"service": "z"}),
        ({"kind": "GENERATION"}, {"kind": "SPAN"}),
        ({"span": "a"}, {"span": "z"}),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_presentation_ties_use_all_stable_coordinates(engine, low, high, reverse):
    rows = [
        row(version=99, strings={"k": "low"}, **low),
        row(strings={"k": "high"}, **high),
    ]
    assert execute(engine, list(reversed(rows)) if reverse else rows) == expected(
        k="high"
    )


@pytest.mark.parametrize(
    "extra,booleans,numbers,strings,value",
    [
        ('{"k":{"nested":2}}', {"k": 1}, {"k": 2.0}, {"k": "s"}, {"nested": 2}),
        ('{"k":null}', {"k": 1}, {"k": 2.0}, {"k": "s"}, None),
        ("{}", {"k": 0}, {"k": 2.0}, {"k": "s"}, False),
        ("{}", {}, {"k": 0.0}, {"k": "s"}, 0),
        ("{}", {}, {}, {"k": ""}, ""),
        (None, {}, {}, {"k": "new"}, "new"),
        ("", {}, {}, {"k": "new"}, "new"),
    ],
)
def test_newest_row_type_precedence_json_null_and_nullable_extra(
    engine, extra, booleans, numbers, strings, value
):
    rows = [
        row(
            extra='{"k":"stale-extra"}',
            booleans={"k": 1},
            numbers={"k": 99},
            strings={"k": "stale"},
        ),
        row(
            version=2, extra=extra, booleans=booleans, numbers=numbers, strings=strings
        ),
    ]
    assert execute(engine, rows) == expected(k=value)


@pytest.mark.parametrize("extra", [None, "{}", ""])
def test_absent_newest_key_is_not_json_null_and_does_not_use_old_maps(engine, extra):
    rows = [
        row(extra='{"k":null}', strings={"k": "stale"}),
        row(version=2, extra=extra),
    ]
    assert execute(engine, rows) == {}


def test_all_history_children_outside_root_window_are_hydrated(engine):
    rows = [
        row(span="root", parent="", strings={"k": "root"}),
        row(span="late", at=TIME + timedelta(days=4), strings={"k": "late"}),
        row(span="early", at=TIME - timedelta(days=4), strings={"early": "retained"}),
    ]
    assert execute(engine, rows, ("k", "early")) == expected(k="late", early="retained")


def test_org_scope_is_exact_pairs_not_project_trace_cross_product(engine):
    rows = [
        row(strings={"k": "a"}),
        row(project=OTHER, trace="other", strings={"k": "b"}),
        row(project=OTHER, strings={"k": "wrong-pair"}, at=TIME + timedelta(days=1)),
        row(trace="other", strings={"k": "wrong-pair"}, at=TIME + timedelta(days=1)),
        row(project=FOREIGN, strings={"k": "foreign"}, at=TIME + timedelta(days=2)),
    ]
    assert execute(
        engine,
        rows,
        projects=[PROJECT, OTHER],
        identities=[(PROJECT, "trace"), (OTHER, "other")],
        trace_ids=["trace", "other"],
    ) == {
        (PROJECT, "trace", "k"): "a",
        (OTHER, "other", "k"): "b",
    }
    assert execute(engine, rows) == expected(k="a")


def test_equal_version_conflict_is_coherent_but_has_no_final_tie_oracle(engine):
    # Either row may win. Never assert equivalence to FINAL's unspecified tie.
    rows = [
        row(extra='{"a":"first"}', strings={"b": "first"}),
        row(extra=None, strings={"a": "second", "b": "second"}),
    ]
    result = execute(engine, rows, ("a", "b"))
    assert result in (expected(a="first", b="first"), expected(a="second", b="second"))


def test_many_physical_spans_emit_only_page_times_requested_keys(engine):
    rows = [row(strings={"k": "__index__", "b": "v", "ignored": "x"})]
    assert execute(engine, rows, ("k", "b", "k"), fanout=5002) == expected(
        k="5001", b="v"
    )
