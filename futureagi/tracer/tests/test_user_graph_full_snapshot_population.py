"""Offline full-snapshot population/replacement and remap SQL contracts."""

import re
from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 9, 1, 12, 15, 0, 123456)
END = datetime(2026, 9, 1, 14, 30, 0, 654321)


def _build(
    start=START, end=END, *, snapshot_start=START, snapshot_end=END, membership=True
):
    return UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), end.isoformat()],
                },
            }
        ],
        interval="hour",
        exact_snapshot_start=snapshot_start,
        exact_snapshot_end=snapshot_end,
        user_membership_sql="SELECT toUUID(%(selected_user)s)" if membership else None,
        user_membership_params={"selected_user": PROJECT} if membership else None,
    ).build()


def _cte(sql, name):
    start = re.search(r"\b" + re.escape(name) + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


@pytest.mark.parametrize("membership", [True, False])
def test_equal_snapshot_bounds_remove_recursive_trace_population(membership):
    sql, params = _build(membership=membership)
    assert "candidate_trace_ids AS" not in sql
    assert "HAVING min(start_time)" not in sql
    assert sql.count("FROM spans FINAL") == 1
    assert "trace_id IN (SELECT trace_id" not in sql
    assert "GROUP BY end_user_id, trace_id" in sql
    assert "GROUP BY time_bucket, end_user_id" in sql
    assert "sum(rs.cost) AS span_total_cost" in sql
    assert "avg(rs.latency_ms) AS span_avg_latency" in sql
    assert params["snapshot_start_date"] == START
    assert params["snapshot_end_date"] == END
    assert params["project_id"] == PROJECT
    if membership:
        assert "SELECT toUUID(%(selected_user)s)" in sql
        assert params["selected_user"] == PROJECT
    placeholders = set(re.findall(r"%\((\w+)\)s", sql))
    assert placeholders <= params.keys()


@pytest.mark.parametrize(
    ("start", "end", "scan_start", "scan_end"),
    [
        (START, END, datetime(2026, 9, 1, 12), datetime(2026, 9, 1, 15)),
        (
            datetime(2026, 9, 1, 12),
            datetime(2026, 9, 1, 14),
            datetime(2026, 9, 1, 12),
            datetime(2026, 9, 1, 14),
        ),
        (
            START,
            START + timedelta(microseconds=1),
            datetime(2026, 9, 1, 12),
            datetime(2026, 9, 1, 13),
        ),
    ],
)
def test_replacement_sees_full_hours_before_exact_timestamp_and_tombstone_filter(
    start, end, scan_start, scan_end
):
    sql, params = _build(start, end, snapshot_start=start, snapshot_end=end)
    population = _cte(sql, "latest_spans")
    inner, outer = population.split(") AS snapshot_spans", 1)
    assert "FROM spans FINAL PREWHERE project_id" in inner
    physical = inner.split("FROM spans FINAL PREWHERE", 1)[1].split(") AS physical", 1)[
        0
    ]
    assert "is_deleted" not in physical and "end_user_id" not in physical
    assert "toStartOfHour(start_time) >= %(user_snapshot_scan_start)s" in physical
    assert "toStartOfHour(start_time) < %(user_snapshot_scan_end)s" in physical
    assert (
        "ARRAY JOIN [tuple(physical.start_time, physical.is_deleted, physical.end_user_id, physical.trace_session_id)] AS latest_membership"
        in inner
    )
    for index, field in enumerate(
        ("start_time", "is_deleted", "end_user_id", "trace_session_id"), 1
    ):
        assert f"latest_membership.{index} AS {field}" in inner
    assert "user_snapshot_start_us" not in inner
    assert "user_snapshot_end_us" not in inner
    assert "snapshot_spans.is_deleted = 0" in outer
    assert "fromUnixTimestamp64Micro(%(user_snapshot_start_us)s, 'UTC')" in outer
    assert "fromUnixTimestamp64Micro(%(user_snapshot_end_us)s, 'UTC')" in outer
    assert params["user_snapshot_scan_start"] == scan_start
    assert params["user_snapshot_scan_end"] == scan_end
    epoch = datetime(1970, 1, 1)
    assert params["user_snapshot_start_us"] == (start - epoch) // timedelta(
        microseconds=1
    )
    assert params["user_snapshot_end_us"] == (end - epoch) // timedelta(microseconds=1)
    assert "optimize_move_to_prewhere_if_final = 0" in sql
    assert "use_skip_indexes_if_final = 0" in sql


def test_full_snapshot_remap_references_candidate_population_only_once():
    sql, _ = _build()
    remap = _cte(sql, "eu_survivor_map")
    assert remap.count("FROM candidate_end_user_ids") == 1
    assert "ARRAY JOIN [old_id, new_id] AS candidate_alias" in remap
    assert (
        "candidate_alias IN ( SELECT end_user_id FROM candidate_end_user_ids )" in remap
    )
    assert "SELECT DISTINCT new_id" in remap
    assert "argMin(old_id, toString(old_id))" in remap
    assert "arrayDistinct(arrayConcat(groupArray(old_id), [new_id]))" in remap
    assert "GROUP BY new_id" in remap and "GROUP BY any_id" in remap
    assert "LIMIT" not in remap
    candidates = _cte(sql, "candidate_end_user_ids")
    assert "isNotNull(end_user_id)" in candidates
    assert "00000000-0000-0000-0000-000000000000" in candidates
    assert "id_remap.survivor_id IS NULL" in sql


@pytest.mark.parametrize("edge", ["start", "end", "both"])
def test_narrow_output_partition_keeps_entity_safe_trace_ownership(edge):
    start = START + timedelta(minutes=1) if edge in {"start", "both"} else START
    end = END - timedelta(minutes=1) if edge in {"end", "both"} else END
    sql, params = _build(start, end)
    assert "candidate_trace_ids AS" in sql
    candidates = _cte(sql, "candidate_trace_ids")
    assert "FROM snapshot_spans GROUP BY trace_id" in candidates
    assert "HAVING min(start_time) >= fromUnixTimestamp64Micro(%(user_partition_start_us)s, 'UTC')" in candidates
    assert "min(start_time) < fromUnixTimestamp64Micro(%(user_partition_end_us)s, 'UTC')" in candidates
    assert "trace_id IN (SELECT trace_id FROM candidate_trace_ids)" in sql
    assert sql.count("FROM spans FINAL") == 1
    assert "FROM snapshot_spans" in _cte(sql, "latest_spans")
    assert params["user_snapshot_scan_start"] == START.replace(minute=0, second=0, microsecond=0)
    assert params["user_snapshot_scan_end"] == END.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    epoch = datetime(1970, 1, 1)
    assert params["user_partition_start_us"] == (start - epoch) // timedelta(microseconds=1)
    assert params["user_partition_end_us"] == (end - epoch) // timedelta(microseconds=1)
    population = _cte(sql, "snapshot_spans")
    physical, barrier = population.split(") AS physical", 1)
    assert "user_snapshot_start_us" not in physical
    assert "user_snapshot_end_us" not in physical
    assert "ARRAY JOIN [tuple(physical.start_time, physical.is_deleted, physical.end_user_id, physical.trace_session_id)] AS latest_membership" in barrier
    assert "snapshot_spans.is_deleted = 0" in barrier
    assert "optimize_move_to_prewhere_if_final = 0" in sql
    assert "ARRAY JOIN [old_id, new_id]" not in sql


def test_legacy_non_snapshot_path_uses_full_physical_snapshot_replay():
    sql, params = _build(snapshot_start=None, snapshot_end=None)
    full_sql, full_params = _build()
    assert _cte(sql, "latest_spans") == _cte(full_sql, "latest_spans")
    for key in ("user_snapshot_scan_start", "user_snapshot_scan_end", "user_snapshot_start_us", "user_snapshot_end_us"):
        assert params[key] == full_params[key]
    assert "LIMIT 1 BY" not in sql
    assert sql.count("FROM spans FINAL") == 1
    assert "optimize_move_to_prewhere_if_final = 0" in sql
    assert "ARRAY JOIN [old_id, new_id]" not in sql


@pytest.mark.parametrize("candidates", [{"old-b"}, {"z-new"}, {"old-b", "z-new"}])
def test_remap_fixture_expands_complete_touched_group_before_choosing_survivor(
    candidates,
):
    sql, _ = _build()
    remap = _cte(sql, "eu_survivor_map")
    assert "ARRAY JOIN [old_id, new_id] AS candidate_alias" in remap
    assert "argMin(old_id, toString(old_id))" in remap
    rows = [
        ("old-a", "z-new"),
        ("old-b", "z-new"),
        ("z-new", "z-new"),
        ("unrelated", "other"),
    ]
    touched = {new for old, new in rows for alias in (old, new) if alias in candidates}
    mapping = {}
    for new in touched:
        olds = {old for old, group in rows if group == new}
        survivor = min(olds, key=str)
        for alias in olds | {new}:
            mapping[alias] = min(mapping.get(alias, survivor), survivor, key=str)
    # 'z-new' also occurs as an old identity row. The full group chooses its
    # lexical survivor without alias duplication/fan-out or candidate pruning.
    assert mapping == {"old-a": "old-a", "old-b": "old-a", "z-new": "old-a"}
    assert "unrelated" not in mapping


@pytest.mark.parametrize(
    "outcome", ["moves-out", "moves-in", "tombstone", "reassigned"]
)
def test_fixture_full_hour_replacement_does_not_revive_old_timestamp(outcome):
    """Model the SQL's scan->replacement->exact-window order; not live SQL."""
    sql, params = _build()
    assert "FROM spans FINAL" in _cte(sql, "latest_spans")
    old = {
        "project_id": PROJECT,
        "observation_type": "span",
        "service_name": "svc",
        "trace_id": "t",
        "id": "s",
        "start_time": START + timedelta(minutes=1),
        "_version": 1,
        "is_deleted": 0,
        "end_user_id": "old-user",
    }
    new = {**old, "_version": 2}
    if outcome == "moves-out":
        new["start_time"] = START - timedelta(minutes=1)
    elif outcome == "moves-in":
        old["start_time"] = START - timedelta(minutes=1)
    elif outcome == "tombstone":
        new.update(start_time=START - timedelta(minutes=1), is_deleted=1)
    elif outcome == "reassigned":
        new["end_user_id"] = "new-user"
    latest = {}
    for row in [old, new]:
        if (
            not params["user_snapshot_scan_start"]
            <= row["start_time"]
            < params["user_snapshot_scan_end"]
        ):
            continue
        identity = (
            row["project_id"],
            row["observation_type"],
            row["service_name"],
            row["start_time"].replace(minute=0, second=0, microsecond=0),
            row["trace_id"],
            row["id"],
        )
        if identity not in latest or row["_version"] > latest[identity]["_version"]:
            latest[identity] = row
    result = [
        row
        for row in latest.values()
        if not row["is_deleted"] and START <= row["start_time"] < END
    ]
    if outcome in {"moves-out", "tombstone"}:
        assert result == []
    else:
        assert len(result) == 1 and result[0]["_version"] == 2
        assert result[0]["end_user_id"] == (
            "new-user" if outcome == "reassigned" else "old-user"
        )
