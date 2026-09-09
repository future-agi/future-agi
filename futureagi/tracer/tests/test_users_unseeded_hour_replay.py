"""Unseeded Users replay on constant fixtures; no table creation or writes."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests.test_users_attribute_physical_replay import (
    CONTEXT,
    values_where,
    without_query_settings,
)

P = "11111111-1111-4111-8111-111111111111"
U = "22222222-2222-4222-8222-222222222222"
A = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
START = datetime(2026, 9, 1, 12, 15, tzinfo=UTC)


def query(days, change, *, window_start=START, window_end=None):
    builder = UserListQueryBuilderV2(organization_id=P, project_ids=[P], filters=[])
    sql, params = builder.build_dimension_candidate_query(
        limit=26,
        window_start=window_start,
        window_end=window_end or START + timedelta(days=days),
    )
    base = {
        "project_id": P,
        "observation_type": "SPAN",
        "service_name": "service",
        "trace_id": "trace",
        "id": "span",
        "start_time": START + timedelta(minutes=15),
        "end_time": START + timedelta(minutes=16),
        "end_user_id": U,
        "_version": 1,
        "cost": 1.25,
        "total_tokens": 8,
        "prompt_tokens": 3,
        "completion_tokens": 5,
        "is_deleted": 0,
        "trace_session_id": None,
    }
    later = {**base, "_version": 2, **change}
    types = "project_id UUID, observation_type String, service_name String, trace_id String, id String, start_time DateTime64(6, 'UTC'), end_time Nullable(DateTime64(6, 'UTC')), end_user_id Nullable(UUID), _version UInt64, cost Float64, total_tokens Int32, prompt_tokens Int32, completion_tokens Int32, is_deleted UInt8, trace_session_id Nullable(UUID)"

    def encode(row):
        return tuple(
            v.replace(tzinfo=None).isoformat(sep=" ") if isinstance(v, datetime) else v
            for v in row.values()
        )

    literals = escape_params(
        {
            "schema": types,
            "rows": (encode(base), encode(later)),
            "p": P,
            "u": U,
            "a": A,
            "r": R,
            "time": START.replace(tzinfo=None).isoformat(sep=" "),
        },
        CONTEXT,
    )
    prefix = f"""WITH spans AS (SELECT * FROM values({literals["schema"]},{literals["rows"][1:-1]})),
end_users AS (SELECT toUUID({literals["p"]}) AS project_id,toUUID({literals["p"]}) AS organization_id,toUUID({literals["u"]}) AS end_user_id,'fixture-user' AS user_id,'custom' AS user_id_type,toUInt64(1) AS user_id_hash,toDateTime64({literals["time"]},6,'UTC') AS first_seen,first_seen AS version,toUInt8(0) AS is_deleted),
end_user_id_remap AS (SELECT * FROM values('old_id UUID, new_id UUID',({literals["u"]},{literals["r"]}),({literals["a"]},{literals["r"]}))),"""

    def render(text):
        text = without_query_settings(text) % escape_params(params, CONTEXT)
        text = text.replace("end_user_id_remap FINAL", "end_user_id_remap").replace(
            "end_users AS eu FINAL", "end_users AS eu"
        )
        return values_where(prefix + text.lstrip()[4:])

    return render(sql)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "change, present",
    [
        ({}, True),
        ({"end_user_id": A}, True),
        ({"is_deleted": 1}, False),
        ({"end_user_id": None}, False),
        ({"end_time": None}, True),
        ({"start_time": START - timedelta(minutes=5)}, False),
        ({"service_name": "other", "is_deleted": 1}, True),
        (
            {"cost": 0, "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
            True,
        ),
    ],
)
def test_unseeded_users_latest_state(days, change, present):
    engine = pytest.importorskip("chdb")
    sql = query(days, change)
    assert "candidate_span_identities" not in sql
    rows = [
        json.loads(line)
        for line in str(engine.query(sql, "JSONEachRow")).splitlines()
        if line
    ]
    assert len(rows) == int(present)
    if present:
        row = rows[0]
        assert row["end_user_id"] == U
        assert row["total_cost"] == change.get("cost", 1.25)
        assert int(row["total_tokens"]) == change.get("total_tokens", 8)
        assert int(row["num_traces"]) == 1
        if "end_time" in change:
            assert row["last_active"] is None


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "boundary",
    ["start_same_hour", "start_previous_hour", "end_same_hour", "end_next_hour"],
)
@pytest.mark.parametrize("deleted", [0, 1])
def test_unseeded_users_window_identity_boundaries(days, boundary, deleted):
    engine = pytest.importorskip("chdb")
    times = {
        "start_same_hour": START - timedelta(minutes=5),
        "start_previous_hour": START - timedelta(hours=1),
        "end_same_hour": START + timedelta(days=days, minutes=5),
        "end_next_hour": START + timedelta(days=days, hours=1),
    }
    sql = query(days, {"start_time": times[boundary], "is_deleted": deleted})
    rows = [
        json.loads(line)
        for line in str(engine.query(sql, "JSONEachRow")).splitlines()
        if line
    ]
    # A different hour is a different physical key; it cannot replace the
    # original live row. Within its hour, a correction outside the window does.
    assert len(rows) == (0 if boundary == "start_same_hour" else 1)
    if rows:
        assert rows[0]["total_cost"] == 1.25
        assert rows[0]["end_user_id"] == U


@pytest.mark.parametrize(
    "lower,upper,correction,present",
    [
        (-1, 0, 0, False),
        (0, 1, 0, True),
        (1, 2, 0, False),
        (1, 2, 1, True),
        (0, 1, 1, False),
        (-1, 0, -1, True),
        (0, 1, -1, False),
    ],
)
def test_unseeded_replay_keeps_microsecond_half_open_window(
    lower, upper, correction, present
):
    engine = pytest.importorskip("chdb")
    physical_start = START + timedelta(minutes=15)
    sql = query(
        7,
        {"start_time": physical_start + timedelta(microseconds=correction)},
        window_start=physical_start + timedelta(microseconds=lower),
        window_end=physical_start + timedelta(microseconds=upper),
    )
    rows = [
        json.loads(line)
        for line in str(engine.query(sql, "JSONEachRow")).splitlines()
        if line
    ]
    assert len(rows) == int(present)
    if present:
        assert rows[0]["total_cost"] == 1.25


@pytest.mark.parametrize("deleted", [0, 1])
def test_unseeded_replay_at_exact_hour_boundary(deleted):
    engine = pytest.importorskip("chdb")
    next_hour = START.replace(hour=13, minute=0)
    sql = query(
        7,
        {"start_time": next_hour, "is_deleted": deleted},
        window_start=next_hour,
        window_end=next_hour + timedelta(microseconds=1),
    )
    rows = [
        json.loads(line)
        for line in str(engine.query(sql, "JSONEachRow")).splitlines()
        if line
    ]
    assert len(rows) == (0 if deleted else 1)
    if rows:
        assert rows[0]["total_cost"] == 1.25
