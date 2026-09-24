"""Actual Users graph SQL against independently materialized full FINAL winners."""

# ruff: noqa: F811
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    engine,  # noqa: F401 - pytest fixture
    time_filter,
)

UA, UB, UZ, NEW = (f"{n:08d}-3333-3333-3333-333333333333" for n in (3, 4, 5, 9))
LO, HI = START + timedelta(minutes=15), START + timedelta(minutes=30)
FILTERS = [time_filter(LO, HI)] + [
    {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": value,
        },
    }
    for key, value in (("a", 1), ("b", 2))
]


def populate(run, insert, minute):
    run("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    run(
        "SET max_threads=1, max_memory_usage=536870912, max_execution_time=10, "
        "use_skip_indexes_if_final=0, enable_optimize_predicate_expression=1, "
        "enable_optimize_predicate_expression_to_final_subquery=1"
    )
    run("""CREATE TABLE end_users (project_id UUID, end_user_id UUID, user_id String,
        user_id_type String, user_id_hash String, first_seen DateTime64(6,'UTC'),
        version UInt64, is_deleted UInt8) ENGINE=ReplacingMergeTree(version)
        ORDER BY (project_id,end_user_id)""")
    for table in ("end_user_id_remap", "trace_session_id_remap"):
        run(
            f"CREATE TABLE {table} (old_id UUID,new_id UUID,version UInt64) "
            "ENGINE=ReplacingMergeTree(version) ORDER BY old_id"
        )
    for uid in (UA, UB, UZ):
        run(
            "INSERT INTO end_users VALUES (%(p)s,%(u)s,%(u)s,'id','hash',%(t)s,1,0)",
            {"p": PROJECT, "u": uid, "t": START},
        )
    run(
        "INSERT INTO end_user_id_remap VALUES (%(a)s,%(n)s,1),(%(b)s,%(n)s,1)",
        {"a": UA, "b": UB, "n": NEW},
    )

    def add(trace, sid, cost, *, user=UB, attrs=None, **kwargs):
        base = {
            "trace_id": trace,
            "id": sid,
            "end_user_id": user,
            "attrs_number": attrs or {},
            "cost": cost,
            "total_tokens": 2 * cost,
            "prompt_tokens": cost,
            "completion_tokens": cost,
            "latency_ms": 8,
            "status": "OK",
        }
        insert(**{**base, **kwargs})
        return base

    add("trace-a", "a", 2, user=UA, attrs={"a": 1})
    add("trace-b", "b", 3, attrs={"b": 2}, parent_span_id="parent")
    add("trace-a", "c", 5, parent_span_id="parent")
    add(
        "trace-a",
        "c",
        1,
        service_name="service-b",
        trace_session_id="00000000-0000-0000-0000-000000000000",
    )
    add("trace-a", "c", 1, observation_type="llm")
    for trace, sid, cost, correction in (
        ("trace-clear", "clear", 32, {"attrs_number": {}}),
        ("trace-a", "nullable", 7, {"latency_ms": None, "attrs_number": {}}),
        ("trace-deleted", "deleted", 128, {"is_deleted": 1}),
        ("trace-null-user", "null-user", 256, {"end_user_id": None}),
        (
            "trace-reassigned",
            "reassigned",
            512,
            {"end_user_id": UZ, "attrs_number": {"a": 1}},
        ),
        (
            "trace-moving",
            "moving",
            64,
            {
                "start_time": START + timedelta(minutes=minute),
                "latency_ms": 64,
                "attrs_number": {},
            },
        ),
    ):
        old = add(trace, sid, cost, attrs={"a": 1, "b": 2})
        insert(**{**old, **correction, "_version": 2})  # Separate physical part.
    old = add("trace-z-clear", "z-clear", 1024, user=UZ, attrs={"b": 2})
    insert(**{**old, "attrs_number": {}, "_version": 2})
    add("trace-a", "a", 4096, user=UA, project_id=OTHER_PROJECT, _version=99)


def whole_final_gold(run):
    # NO physical/date/project/user/filter predicates in this independent read.
    winners = run("SELECT * FROM spans FINAL")
    remaps = run("SELECT * FROM end_user_id_remap FINAL")
    groups = defaultdict(set)
    for r in remaps:
        groups[r["new_id"]].add(r["old_id"])
    aliases = {alias: min(old) for new, old in groups.items() for alias in old | {new}}
    domain = {
        aliases.get(r["end_user_id"], r["end_user_id"])
        for r in run("SELECT * FROM end_users FINAL")
        if r["project_id"] == PROJECT and not r["is_deleted"] and r["user_id"]
    }
    live = [
        dict(r, end_user_id=aliases.get(r["end_user_id"], r["end_user_id"]))
        for r in winners
        if r["project_id"] == PROJECT
        and not r["is_deleted"]
        and r["end_user_id"] is not None
        and LO <= r["start_time"] < HI
    ]
    selected = {
        u
        for u in domain
        if all(
            any(r["end_user_id"] == u and r["attrs_number"].get(k) == v for r in live)
            for k, v in (("a", 1), ("b", 2))
        )
    }
    assert selected == {UA} and aliases[UB] == UA
    rows = [r for r in live if r["end_user_id"] in selected]
    assert not any(
        r["attrs_number"].get("a") == 1 and r["attrs_number"].get("b") == 2
        for r in rows
    )
    assert len([r for r in rows if r["id"] == "c"]) == 3  # Full six-key collisions.
    assert {r["id"] for r in rows} >= {"a", "b", "c", "clear", "nullable"}
    return rows, tuple(sorted({r["trace_id"] for r in winners}))


def mean(values):
    return float(sum((Fraction(v) for v in values), Fraction()) / len(values))


@pytest.mark.integration
@pytest.mark.parametrize("path", ["full_snapshot", "aggregate", "trace_membership"])
@pytest.mark.parametrize("minute", [10, 40, 21], ids=["before", "after", "inside"])
def test_native_users_actual_paths(engine, path, minute):
    run, insert = engine
    populate(run, insert, minute)
    gold, traces = whole_final_gold(run)
    expected_traces = {r["trace_id"] for r in gold}
    total = sum(r["cost"] for r in gold)
    assert total == (115 if minute == 21 else 51)
    kwargs = {
        "project_id": PROJECT,
        "filters": FILTERS,
        "start_date": LO,
        "end_date": HI,
        "candidate_trace_ids_param": "candidate_traces",
    }
    if path == "full_snapshot":
        captured = []

        def execute(sql, params, **_):
            rows = run(sql, params)
            captured.extend(rows)
            return SimpleNamespace(data=rows, columns=list(rows[0]) if rows else [])

        payload = graphs.read_exact_user_system_graph(
            analytics=SimpleNamespace(execute_ch_query=execute),
            project_id=PROJECT,
            filters=FILTERS,
            interval="day",
            metric_id="active_users",
        )
        by_trace = defaultdict(list)
        for row in gold:
            if row["latency_ms"] is not None:
                by_trace[row["trace_id"]].append(row["latency_ms"])
        expected = {
            "avg_latency": mean([mean(v) for v in by_trace.values()]),
            "total_tokens": 2 * total,
            "avg_cost": total,
            "traffic_count": 1,
            "prompt_tokens": total,
            "completion_tokens": total,
            "error_rate": 0,
            "active_users": 1,
            "total_cost_sum": total,
            "avg_cost_per_user": total,
            "avg_traces_per_user": len(expected_traces),
            "total_tokens_sum": 2 * total,
        }
        assert len(captured) == 1
        assert {k: captured[0][k] for k in expected} == expected
        assert sum(point["value"] for point in payload["data"]) == 1
    else:
        builder = (
            graphs._user_trace_membership_sql
            if path == "trace_membership"
            else graphs._user_aggregate_source_sql
        )
        sql, params, needs_eval = builder(
            **kwargs, **({"include_trace_ids": True} if path == "aggregate" else {})
        )
        assert not needs_eval
        rows = run(sql, {**params, "candidate_traces": traces})
        if path == "trace_membership":
            assert {r["trace_id"] for r in rows} == expected_traces
        else:
            assert len(rows) == 1 and rows[0]["end_user_id"] == UA
            expected = {
                "total_cost": total,
                "total_tokens": 2 * total,
                "input_tokens": total,
                "output_tokens": total,
                "num_traces": len(expected_traces),
                "num_sessions": 0,
                "avg_trace_latency": round(
                    mean(
                        [r["latency_ms"] for r in gold if r["latency_ms"] is not None]
                    ),
                    2,
                ),
                "num_llm_calls": 1,
                "num_active_days": 1,
                "num_traces_with_errors": 0,
            }
            assert {k: rows[0][k] for k in expected} == expected
            assert set(rows[0]["user_trace_ids"]) == expected_traces
