"""Direct and partitioned Users graphs versus unfiltered physical FINAL truth."""

# ruff: noqa: F811 - the imported engine fixture is injected by pytest

from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

import pytest

from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    engine,  # noqa: F401 - native fixture, executed only by main
    time_filter,
)

UA, UB, UZ, NEW = (f"{n:08d}-3333-3333-3333-333333333333" for n in (3, 4, 5, 9))
LO = START + timedelta(minutes=15, microseconds=123456)
HI = START + timedelta(minutes=90, microseconds=654321)
PART_LO = START + timedelta(minutes=20, microseconds=123456)
PART_HI = START + timedelta(minutes=80, microseconds=654321)


def populate_legacy(run, insert, correction):
    run("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    run(
        "SET max_threads=1, max_memory_usage=536870912, "
        "use_skip_indexes_if_final=0, enable_optimize_predicate_expression=1, "
        "enable_optimize_predicate_expression_to_final_subquery=1"
    )
    run("""CREATE TABLE end_user_id_remap (old_id UUID,new_id UUID,version UInt64)
        ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
    run(
        "INSERT INTO end_user_id_remap VALUES (%(a)s,%(n)s,1),(%(b)s,%(n)s,1)",
        {"a": UA, "b": UB, "n": NEW},
    )

    def add(trace, sid, cost, **overrides):
        value = {
            "trace_id": trace,
            "id": sid,
            "end_user_id": UB,
            "start_time": START + timedelta(minutes=25),
            "cost": cost,
            "total_tokens": 2 * cost,
            "prompt_tokens": cost,
            "completion_tokens": cost,
            "latency_ms": 8,
            "status": "OK",
        }
        value.update(overrides)
        insert(**value)
        return value

    # Same four legacy keys, distinct service/type; also the next hour's key.
    add("owned", "same", 2, end_user_id=UA)
    add("owned", "same", 3, service_name="service-b")
    add("owned", "same", 5, observation_type="llm")
    add("owned", "same", 7, start_time=START + timedelta(minutes=65))
    # Whole-entity contribution outside the output partition, inside snapshot.
    add("owned", "late-child", 11, start_time=PART_HI + timedelta(minutes=1))
    add("owned", "witness", 64, status="ERROR")
    add("earlier", "root", 13, start_time=PART_LO - timedelta(microseconds=1))
    add("earlier", "child", 17)
    add("at-start", "edge", 19, start_time=PART_LO)
    add("at-end", "edge", 23, start_time=PART_HI)
    for trace, changes in (
        (
            "cleared",
            {"cost": 2, "total_tokens": 4, "prompt_tokens": 2, "completion_tokens": 2},
        ),
        ("deleted", {"is_deleted": 1}),
        ("null-user", {"end_user_id": None}),
        ("reassigned", {"end_user_id": UZ}),
        ("null-latency", {"latency_ms": None}),
    ):
        old = add(trace, trace, 128)
        insert(**{**old, **changes, "_version": 2})
    old_time = START + timedelta(minutes=75 if correction == "after" else 25)
    changed_time = {
        "before": LO - timedelta(microseconds=1),
        "after": HI + timedelta(microseconds=1),
        "inside": old_time + timedelta(microseconds=1),
    }[correction]
    old = add("moving", "moving", 256, start_time=old_time)
    insert(**{**old, "start_time": changed_time, "_version": 2})
    old = add("moving-tombstone", "moving-tombstone", 512, start_time=old_time)
    insert(**{**old, "start_time": changed_time, "is_deleted": 1, "_version": 2})
    # A latest correction can also move into the snapshot from its boundary hour.
    old = add("moves-in", "moves-in", 29, start_time=LO - timedelta(microseconds=1))
    insert(**{**old, "start_time": PART_LO, "_version": 2})
    add("owned", "same", 4096, project_id=OTHER_PROJECT, _version=99)


def legacy_gold(run, path, filter_mode):
    # Materialize every physical winner before applying any predicate in Python.
    winners = run("SELECT * FROM spans FINAL")
    remaps = run("SELECT * FROM end_user_id_remap FINAL")
    groups = defaultdict(set)
    for row in remaps:
        groups[row["new_id"]].add(row["old_id"])
    aliases = {alias: min(old) for new, old in groups.items() for alias in old | {new}}
    assert aliases == {UA: UA, UB: UA, NEW: UA}
    latest = [
        r
        for r in winners
        if r["project_id"] == PROJECT
        and not r["is_deleted"]
        and LO <= r["start_time"] < HI
    ]
    assert (
        len([r for r in latest if r["trace_id"] == "owned" and r["id"] == "same"]) == 4
    )
    if path == "narrow":
        first = {}
        for row in latest:
            first[row["trace_id"]] = min(
                first.get(row["trace_id"], row["start_time"]), row["start_time"]
            )
        owners = {t for t, start in first.items() if PART_LO <= start < PART_HI}
        assert "owned" in owners and "earlier" not in owners
        assert "at-start" in owners and "at-end" not in owners
        latest = [r for r in latest if r["trace_id"] in owners]
        assert any(r["id"] == "late-child" for r in latest)
    matched = {r["trace_id"] for r in latest if r["cost"] > 50}
    rows = [
        dict(r, end_user_id=aliases.get(r["end_user_id"], r["end_user_id"]))
        for r in latest
        if r["end_user_id"] is not None
    ]
    rows = [
        r
        for r in rows
        if (
            r["end_user_id"] == UA
            if filter_mode == "membership"
            else r["trace_id"] in matched
        )
    ]
    assert not any(
        r["trace_id"] in {"deleted", "null-user", "moving-tombstone"} for r in rows
    )
    if filter_mode == "scalar":
        assert not any(r["trace_id"] == "cleared" for r in rows)
        assert any(r["id"] == "late-child" for r in rows)
    return rows


def mean(values):
    return float(sum((Fraction(v) for v in values), Fraction()) / len(values))


def gold_metrics(rows):
    traces, users, buckets = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        traces[row["end_user_id"], row["trace_id"]].append(row)
    for (user, _), spans in traces.items():
        bucket = min(r["start_time"] for r in spans).replace(
            minute=0, second=0, microsecond=0
        )
        users[bucket, user].append(spans)
    for (bucket, _), user_traces in users.items():
        buckets[bucket].append(user_traces)
    expected = []
    for bucket, user_groups in sorted(buckets.items()):
        spans = [r for user in user_groups for trace in user for r in trace]
        user_latencies = []
        for user in user_groups:
            latencies = [
                [r["latency_ms"] for r in trace if r["latency_ms"] is not None]
                for trace in user
            ]
            user_latencies.append(mean([mean(v) for v in latencies if v]))
        total_cost = sum(r["cost"] for r in spans)
        total_tokens = sum(r["total_tokens"] for r in spans)
        expected.append(
            {
                "time_bucket": bucket.strftime("%Y-%m-%d %H:%M:%S"),
                "avg_latency": mean(user_latencies),
                "total_tokens": total_tokens,
                "avg_cost": total_cost / len(user_groups),
                "traffic_count": len(user_groups),
                "prompt_tokens": sum(r["prompt_tokens"] for r in spans),
                "completion_tokens": sum(r["completion_tokens"] for r in spans),
                "error_rate": sum(
                    any(r["status"] == "ERROR" for trace in user for r in trace)
                    for user in user_groups
                )
                * 100.0
                / len(user_groups),
                "active_users": len(user_groups),
                "total_cost_sum": total_cost,
                "avg_cost_per_user": total_cost / len(user_groups),
                "avg_traces_per_user": mean([len(user) for user in user_groups]),
                "total_tokens_sum": total_tokens,
            }
        )
    return expected


@pytest.mark.integration
@pytest.mark.parametrize("path", ["direct", "narrow"])
@pytest.mark.parametrize("correction", ["before", "after", "inside"])
@pytest.mark.parametrize("filter_mode", ["membership", "scalar"])
def test_native_legacy_users_final_population(engine, path, correction, filter_mode):
    run, insert = engine
    populate_legacy(run, insert, correction)
    gold = legacy_gold(run, path, filter_mode)
    start, end = (PART_LO, PART_HI) if path == "narrow" else (LO, HI)
    filters = [time_filter(start, end)]
    kwargs = {}
    if path == "narrow":
        kwargs.update(exact_snapshot_start=LO, exact_snapshot_end=HI)
    if filter_mode == "membership":
        kwargs.update(
            user_membership_sql="SELECT toUUID(%(selected_user)s)",
            user_membership_params={"selected_user": UA},
        )
    else:
        filters.append(
            {
                "column_id": "cost",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 50,
                },
            }
        )
    sql, params = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT, filters=filters, interval="hour", **kwargs
    ).build()
    actual = run(sql, params)
    expected = gold_metrics(gold)
    assert actual == expected
