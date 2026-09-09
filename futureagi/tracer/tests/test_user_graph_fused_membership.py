"""Offline fused Users graph contracts; no ClickHouse/SLO qualification.

The SQLite cases execute the generated metric/window SQL on synthetic,
already-latest rows and precompiled boolean flags. They do not prove CH typed
predicate execution or physical replacement; those have separate SQL contracts.
"""

import re
import socket
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserGraphMembershipPlan,
    UserTimeSeriesQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
NIL = "00000000-0000-0000-0000-000000000000"
END = datetime(2026, 9, 4, 12, 30, 0, 654321)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Disable repository integration DDL for this offline module."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline fused Users graph test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _leaf(
    key="company_id",
    value="10000001",
    *,
    kind="text",
    op="equals",
    source="SPAN_ATTRIBUTE",
):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def _date(days=7):
    return _leaf(
        "created_at",
        [END - timedelta(days=days), END],
        kind="datetime",
        op="between",
        source="SYSTEM_METRIC",
    )


def _read(filters):
    calls = []

    def execute(query, params, *, timeout_ms, settings):
        calls.append((query, params, settings))
        return SimpleNamespace(data=[], columns=[])

    graph.read_exact_user_system_graph(
        analytics=SimpleNamespace(execute_ch_query=execute),
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="active_users",
    )
    assert len(calls) == 1
    return calls[0]


def _cte(sql, name):
    start = re.search(r"\b" + re.escape(name) + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return sql[start:end], end + 1
    raise AssertionError("unclosed CTE")


def _build(filters, *, fused, start=None, domain=True):
    rows, having, params = graph._user_membership_having(filters, project_id=PROJECT)
    if fused:
        plan = UserGraphMembershipPlan.from_compiled(rows, having)
        membership = graph._active_user_dimension_membership_sql() if domain else None
    else:
        plan = None
        # Test-only oracle: expected canonical users are declared explicitly
        # in the SQLite fixture, independently of the fused flag reducer.
        membership = "SELECT end_user_id FROM fixture_selected_users"
    return UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        filters=filters,
        interval="day",
        user_membership_sql=membership,
        user_membership_params=params,
        user_membership_plan=plan,
        exact_snapshot_start=start or END - timedelta(days=7),
        exact_snapshot_end=END,
    ).build()


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_explicit_raw_flags_fuse_without_recompiling_or_pruning_spans(days, count):
    leaves = [_leaf(f"key_{index}") for index in range(count)]
    leaves[0] = _leaf()
    filters = [_date(days), *leaves]
    original = deepcopy(filters)
    rows, having, compiled_params = graph._user_membership_having(
        filters, project_id=PROJECT
    )
    plan = UserGraphMembershipPlan.from_compiled(rows, having)
    sql, params, settings = _read(filters)
    assert "AS user_window_rows" in sql
    assert "AS scalar_user_rows" not in sql
    # Count references, not only the single CTE definition: one remap seed
    # plus one flags/metrics consumer. This is NOT a one-physical-scan claim.
    assert len(re.findall(r"\bFROM latest_spans\b", sql)) == 2
    assert "FROM latest_spans" in _cte(sql, "candidate_end_user_ids")[0]
    assert "FROM candidate_end_user_ids" in _cte(sql, "eu_survivor_map")[0]
    assert graph._active_user_dimension_membership_sql() in sql
    for index, predicate in enumerate(plan.predicates):
        assert f"countIf({predicate}) AS user_member_trace_{index}" in sql
        assert f"sum(user_member_trace_{index}) AS user_member_bucket_{index}" in sql
        assert (
            f"sum(user_member_bucket_{index}) OVER (PARTITION BY end_user_id) "
            f"AS user_member_window_{index}"
        ) in sql
        assert f"user_member_window_{index} > 0" in sql
    span_where = sql.split("WHERE rs.end_user_id IS NOT NULL", 1)[1].split(
        "GROUP BY end_user_id, trace_id", 1
    )[0]
    assert "attrs_" not in span_where and "user_member_" not in span_where
    assert "PARTITION BY end_user_id ORDER BY" not in sql
    assert "PARTITION BY end_user_id, time_bucket" not in sql
    assert "user_span_metrics AS" not in sql and "user_eval_metrics AS" not in sql
    assert "trace_session_id_remap" not in sql
    assert "SAMPLE " not in sql and "LIMIT " not in sql
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert all(params[key] == value for key, value in compiled_params.items())
    assert "10000001" in params.values()
    assert (
        params["start_date"]
        == params["snapshot_start_date"]
        == END - timedelta(days=days)
    )
    assert params["end_date"] == params["snapshot_end_date"] == END
    assert settings["optimize_move_to_prewhere_if_final"] == 0
    assert settings["use_skip_indexes_if_final"] == 0
    assert filters == original


@pytest.mark.parametrize(
    "kind,value",
    [
        ("text", "10000001"),
        ("number", 10000001),
        ("boolean", True),
        ("array", ["10000001", 1, True]),
        ("map", {"key": "10000001"}),
        ("json", {"key": [1, "10000001"]}),
    ],
)
@pytest.mark.parametrize("op", ["not_equals", "is_null", "is_not_null"])
def test_typed_flags_keep_shared_negative_null_and_presence_contract(kind, value, op):
    if kind in {"array", "map", "json"} and op == "not_equals":
        op = "not_contains"
    filters = [_date(), _leaf(kind=kind, value=value, op=op)]
    rows, having, expected_params = graph._user_membership_having(
        filters, project_id=PROJECT
    )
    plan = UserGraphMembershipPlan.from_compiled(rows, having)
    sql, params, _ = _read(filters)
    assert plan.require_match == (
        (True, False) if op.startswith("not_") else (op != "is_null",)
    )
    for index, (predicate, required) in enumerate(
        zip(plan.predicates, plan.require_match, strict=True)
    ):
        assert f"countIf({predicate}) AS user_member_trace_{index}" in sql
        assert f"user_member_window_{index} {'> 0' if required else '= 0'}" in sql
    assert all(params[key] == value for key, value in expected_params.items())


@pytest.mark.parametrize(
    "rows,having",
    [
        ((), "1 = 1"),
        (("(1) AS user_member_match_0",), "countIf(user_member_match_0) >= 0"),
        (("(1) AS user_member_match_0",), "countIf(user_member_match_0) > 1"),
        (("(1) AS other",), "countIf(user_member_match_0) > 0"),
        (("(1) AS user_member_match_0",), "countIf(user_member_match_1) > 0"),
        (("(1) AS user_member_match_0",), "countIf(user_member_match_0) > 0 OR 1 = 1"),
        (("(1) AS user_member_match_0",), "countIf(user_member_match_0) > 0 AND 1 = 1"),
    ],
)
def test_compiler_contract_changes_fail_closed(rows, having):
    with pytest.raises(ValueError, match="unsupported fused user membership contract"):
        UserGraphMembershipPlan.from_compiled(rows, having)


@pytest.mark.parametrize(
    "start,domain", [(END - timedelta(days=6), True), (None, False)]
)
def test_fusion_rejects_partition_or_missing_curated_domain(start, domain):
    with pytest.raises(ValueError, match="requires full snapshot and user domain"):
        _build([_date(), _leaf()], fused=True, start=start, domain=domain)


@pytest.mark.parametrize(
    "extra",
    [
        _leaf("total_cost", 1, kind="number", source="SYSTEM_METRIC"),
        _leaf("eval_score", 80, kind="number", source="SYSTEM_METRIC"),
        _leaf("quality", 1, kind="number", source="EVAL_METRIC"),
        _leaf("review", "good", source="ANNOTATION"),
        _leaf("user_id", "u", source="TRACE_END_USER"),
        _leaf(source="NORMAL"),
        _leaf(source=""),
    ],
)
def test_non_raw_membership_stays_with_full_selector(monkeypatch, extra):
    calls = []

    def full_selector(**kwargs):
        calls.append(kwargs)
        return "SELECT end_user_id FROM existing_full_selector", {}, False

    monkeypatch.setattr(graph, "_user_id_membership_sql", full_selector)
    sql, _, _ = _read([_date(), _leaf(), extra])
    assert "existing_full_selector" in sql and "user_member_window_" not in sql
    assert len(calls) == 1 and calls[0]["reuse_outer_snapshot"] is True


def test_date_only_path_is_not_fused():
    sql, _, _ = _read([_date()])
    assert "user_member_window_" not in sql
    assert graph._active_user_dimension_membership_sql() in sql


@pytest.mark.parametrize(
    "key",
    [
        "user_id",
        "session_id",
        "total_cost",
        "num_sessions",
        "eval_score",
        "has_eval",
        "has_annotation",
        "start_time",
    ],
)
def test_explicit_raw_native_name_collisions_use_fused_flags(key):
    sql, params, _ = _read([_date(), _leaf(key)])
    assert "AS user_window_rows" in sql
    assert "user_span_metrics AS" not in sql
    assert key in params.values() or f"'{key}'" in sql


def test_camel_case_explicit_source_freezes_default_window(monkeypatch):
    start = END - timedelta(days=7)
    monkeypatch.setattr(graph, "_snapshot_window", lambda _filters: (start, END, False))
    sql, params, _ = _read(
        [
            {
                "columnId": "company_id",
                "filterConfig": {
                    "colType": "SPAN_ATTRIBUTE",
                    "filterType": "text",
                    "filterOp": "equals",
                    "filterValue": "10000001",
                },
            }
        ]
    )
    assert "AS user_window_rows" in sql
    assert params["start_date"] == params["snapshot_start_date"] == start
    assert params["end_date"] == params["snapshot_end_date"] == END
    assert "10000001" in params.values()


@pytest.mark.parametrize("stage", ["compile", "execute"])
def test_failure_never_retries_as_unfiltered_or_publishes_empty_success(
    monkeypatch, stage
):
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(stage)
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(
        graph,
        "_user_membership_having"
        if stage == "compile"
        else "_execute_direct_exact_graph_query",
        fail,
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _read([_date(), _leaf()])
    assert calls == [stage]


def test_fusion_preserves_latest_population_and_span_seeded_remap_verbatim():
    filters = [_date(), _leaf()]
    fused, _ = _build(filters, fused=True)
    previous, _ = _build(filters, fused=False)
    for name in ("latest_spans", "candidate_end_user_ids", "eu_survivor_map"):
        assert _cte(fused, name)[0] == _cte(previous, name)[0]
    latest = " ".join(_cte(fused, "latest_spans")[0].split())
    replay, live = latest.split(") AS snapshot_spans", 1)
    assert "FROM spans FINAL" in replay
    physical = replay.split("FROM spans FINAL PREWHERE", 1)[1].split(
        ") AS physical", 1
    )[0]
    assert "is_deleted" not in physical and "end_user_id" not in physical
    assert (
        "user_snapshot_start_us" not in replay and "user_snapshot_end_us" not in replay
    )
    assert "toStartOfHour(start_time) >= %(user_snapshot_scan_start)s" in physical
    assert "toStartOfHour(start_time) < %(user_snapshot_scan_end)s" in physical
    assert (
        "ARRAY JOIN [tuple(physical.start_time, physical.is_deleted, physical.end_user_id, physical.trace_session_id)] AS latest_membership"
        in replay
    )
    assert "snapshot_spans.is_deleted = 0" in live
    assert "user_snapshot_start_us" in live and "user_snapshot_end_us" in live
    for expression in (
        "min(rs.start_time) AS min_start",
        "avg(rs.latency_ms) AS span_avg_latency",
        "avg(span_avg_latency) AS user_avg_latency",
        "avg(user_avg_latency) AS avg_latency",
        "sum(rs.cost) AS span_total_cost",
        "sum(span_total_cost) AS user_total_cost",
        "avg(user_total_cost) AS avg_cost",
        "sum(user_total_cost) AS total_cost_sum",
        "max(if(rs.status = 'ERROR', 1, 0)) AS span_has_error",
        "max(span_has_error) AS user_has_error",
        "countIf(user_has_error = 1)",
        "count() AS user_traces",
        "avg(user_traces) AS avg_traces_per_user",
    ):
        assert expression in fused and expression in previous


class _CountIf:
    def __init__(self):
        self.count = 0

    def step(self, flag):
        self.count += bool(flag)

    def finalize(self):
        return self.count


class _UniqExact:
    def __init__(self):
        self.values = set()

    def step(self, value):
        self.values.add(value)

    def finalize(self):
        return len(self.values)


def _sqlite_metric_sql(sql):
    # Replace only CH-only syntax/functions; execute both real generated
    # reductions against the same pre-replayed latest_spans/remap fixtures.
    tail = sql[_cte(sql, "eu_survivor_map")[1] :].split("SETTINGS", 1)[0]
    return (
        tail.replace(" FINAL", "")
        .replace("%(project_id)s", ":project_id")
        # SQLite gives input column names precedence over SELECT aliases.
        .replace("GROUP BY end_user_id, trace_id", "GROUP BY 1, 2")
    )


@pytest.mark.parametrize("mode", ["positive", "negative", "null"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("mapped_nil", [False, True])
def test_generated_sql_fused_window_matches_declared_users_and_nested_metrics(
    monkeypatch, mode, reverse, mapped_nil
):
    filters = [
        _date(),
        *(
            [_leaf("a"), _leaf("b")]
            if mode == "positive"
            else [_leaf(op="not_equals" if mode == "negative" else "is_null")]
        ),
    ]
    compiled, having, _ = graph._user_membership_having(filters, project_id=PROJECT)
    flags = tuple(f"rs.flag_{i} AS user_member_match_{i}" for i in range(len(compiled)))
    monkeypatch.setattr(
        graph, "_user_membership_having", lambda *_args, **_kwargs: (flags, having, {})
    )
    fused, _ = _build(filters, fused=True)
    previous, _ = _build(filters, fused=False)
    selected_a = (1, 0) if mode != "null" else (0, 0)
    selected_b = (0, 1) if mode == "positive" else (0, 0)
    selected_both = (1, 1) if mode == "positive" else selected_a

    def row(user, trace, day, latency, cost, status, flags):
        return (
            user,
            trace,
            f"2026-09-0{day} 12:00:00",
            latency,
            cost,
            cost,
            cost,
            cost,
            status,
            *flags,
        )

    data = [
        row("old-b", "t1", 1, 0, 1, "OK", selected_a),
        # Same user/trace across days: all of t1 belongs to its first bucket.
        row("new", "t1", 2, 20, 3, "OK", (0, 0)),
        row("new", "t2", 1, 100, 5, "ERROR", (0, 0)),
        # Independent second witness in another trace AND another bucket.
        row("old-b", "t3", 2, 50, 7, "FAILED", selected_b),
        row("unmapped", "t1", 1, 200, 11, "OK", selected_both),
        # An entirely missing typed domain satisfies null, never a negative.
        row("absent", "absent-trace", 1, 300, 13, "OK", (0, 0)),
        row("reject", "r1", 1, 999, 999, "OK", (1, 0)),
        row(
            "reject", "r2", 2, 999, 999, "OK", (0, 1) if mode == "negative" else (0, 0)
        ),
    ]
    for user in ("missing", "deleted", "empty", "foreign", None, NIL):
        data.append(row(user, "invalid", 1, 30, 2, "ERROR", selected_both))
    if reverse:
        data.reverse()

    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.create_function("if", 3, lambda condition, yes, no: yes if condition else no)
        db.create_function("toUUID", 1, lambda value: value)
        db.create_function("isNotNull", 1, lambda value: value is not None)
        db.create_function("notEmpty", 1, lambda value: bool(value))
        db.create_function("toStartOfDay", 1, lambda value: value[:10])
        db.create_function("greatest", 2, max)
        db.create_aggregate("countIf", 1, _CountIf)
        db.create_aggregate("uniqExact", 1, _UniqExact)
        db.executescript("""
            CREATE TABLE latest_spans(end_user_id TEXT, trace_id TEXT, start_time TEXT,
                latency_ms REAL, cost REAL, total_tokens REAL, prompt_tokens REAL,
                completion_tokens REAL, status TEXT, flag_0 INTEGER, flag_1 INTEGER);
            CREATE TABLE eu_survivor_map(any_id TEXT, survivor_id TEXT);
            CREATE TABLE end_users(end_user_id TEXT, project_id TEXT, user_id TEXT, is_deleted INTEGER);
            CREATE TABLE fixture_selected_users(end_user_id TEXT);
        """)
        db.executemany("INSERT INTO latest_spans VALUES (?,?,?,?,?,?,?,?,?,?,?)", data)
        expected_users = ["old-a", "unmapped", *(["absent"] if mode == "null" else [])]
        db.executemany(
            "INSERT INTO fixture_selected_users VALUES (?)",
            [(user,) for user in expected_users],
        )
        mappings = [(alias, "old-a") for alias in ("old-a", "old-b", "new")]
        if mapped_nil:
            mappings.append((NIL, "old-a"))
        db.executemany("INSERT INTO eu_survivor_map VALUES (?,?)", mappings)
        db.executemany(
            "INSERT INTO end_users VALUES (?,?,?,?)",
            [
                ("old-a", PROJECT, "deleted-survivor", 1),
                ("old-b", PROJECT, "live-alias", 0),
                ("unmapped", PROJECT, "live", 0),
                ("absent", PROJECT, "live", 0),
                ("reject", PROJECT, "live", 0),
                ("deleted", PROJECT, "deleted", 1),
                ("empty", PROJECT, "", 0),
                ("foreign", "other-project", "live", 0),
                (NIL, PROJECT, "nil", 0),
            ],
        )
        actual = [
            dict(row)
            for row in db.execute(_sqlite_metric_sql(fused), {"project_id": PROJECT})
        ]
        expected = [
            dict(row)
            for row in db.execute(_sqlite_metric_sql(previous), {"project_id": PROJECT})
        ]
    assert len(actual) == len(expected) == 2
    for left, right in zip(actual, expected, strict=True):
        assert left.keys() == right.keys()
        assert left["time_bucket"] == right["time_bucket"]
        for key in left.keys() - {"time_bucket"}:
            assert left[key] == pytest.approx(right[key])
    if not mapped_nil:
        assert actual[0]["avg_latency"] == (185 if mode == "null" else 127.5)
        assert actual[0]["avg_cost"] == (11 if mode == "null" else 10)
        assert (
            actual[0]["total_cost_sum"]
            == actual[0]["total_tokens"]
            == (33 if mode == "null" else 20)
        )
        assert actual[0]["avg_traces_per_user"] == pytest.approx(
            4 / 3 if mode == "null" else 1.5
        )
        assert actual[0]["error_rate"] == pytest.approx(
            100 / 3 if mode == "null" else 50
        )
        assert (
            actual[0]["traffic_count"]
            == actual[0]["active_users"]
            == (3 if mode == "null" else 2)
        )
        assert actual[1]["avg_latency"] == 50
        assert actual[1]["avg_cost"] == 7
        assert actual[1]["error_rate"] == 0  # FAILED is not ERROR in this graph.
