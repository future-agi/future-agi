"""All-span graph aggregation against independent whole-fixture latest truth.

This deliberately reads the complete tiny synthetic fixture, not an application
candidate query or production population. Production acquisition evidence lives
in the separately frozen QA artifacts.
"""
# Reusable pytest fixtures are injected by name.
# ruff: noqa: F811
from collections import defaultdict
from datetime import datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.tests.test_span_physical_identity_latest import PROJECT
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def _integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and str(int(value)) == value:
        return int(value)
    raise ValueError("invalid fixture integer")


def _fixture_reference(run, project_id, attribute_key, start, end):
    # Every physical version in the isolated tenant fixture participates.
    # No FINAL, root/child date clipping, population LIMIT or candidate IDs.
    rows = run(
        """
        SELECT toString(project_id) AS project_id, observation_type, service_name,
            toUnixTimestamp64Micro(
                toDateTime64(toStartOfHour(start_time), 6, 'UTC')
            ) AS hour_us, trace_id, id, _version AS version,
            toUnixTimestamp64Micro(start_time) AS start_us,
            parent_span_id, is_deleted, latency_ms,
            mapContains(attrs_number, %(key)s) AS number_present,
            attrs_number[%(key)s] AS number_value
        FROM spans PREWHERE project_id=%(project)s
        """,
        {"project": project_id, "key": attribute_key},
    )
    identity_fields = (
        "project_id", "observation_type", "service_name", "hour_us", "trace_id", "id"
    )
    state_fields = (
        "start_us", "parent_span_id", "is_deleted", "latency_ms",
        "number_present", "number_value",
    )
    winners = {}
    for row in rows:
        assert row["project_id"] == project_id
        for key in ("version", "hour_us", "start_us"):
            row[key] = _integer(row[key])
        if row["latency_ms"] is not None:
            row["latency_ms"] = _integer(row["latency_ms"])
        assert row["start_us"] // 3_600_000_000 * 3_600_000_000 == row["hour_us"]
        identity = tuple(row[key] for key in identity_fields)
        if identity not in winners or row["version"] > winners[identity]["version"]:
            winners[identity] = row
    for row in rows:
        winner = winners[tuple(row[key] for key in identity_fields)]
        if row["version"] == winner["version"]:
            if any(row[key] != winner[key] for key in state_fields):
                raise ValueError("conflicting maximum version")

    epoch = datetime(1970, 1, 1)
    start_us = (start - epoch) // timedelta(microseconds=1)
    end_us = (end - epoch) // timedelta(microseconds=1)
    live = [row for row in winners.values() if not row["is_deleted"]]
    roots = {
        row["trace_id"] for row in live
        if not row["parent_span_id"] and start_us <= row["start_us"] < end_us
    }
    positives = {
        row["trace_id"] for row in live
        if row["number_present"] and row["number_value"] > 1
    }
    states = defaultdict(lambda: [0, 0])
    for row in live:
        if row["trace_id"] not in roots & positives:
            continue
        if not start_us <= row["start_us"] < end_us:
            continue
        stamp = epoch + timedelta(microseconds=row["start_us"])
        bucket = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket -= timedelta(days=bucket.weekday())
        states[bucket][0] += row["latency_ms"] or 0
        states[bucket][1] += 1

    # The year-long test uses the public Monday-week grid, including its
    # leading partial week. Empty buckets remain exact zero-valued points.
    bucket = start - timedelta(days=start.weekday())
    data = []
    while bucket <= end:
        numerator, count = states[bucket]
        data.append({
            "timestamp": bucket.isoformat(),
            "value": round(float(Fraction(numerator, count)), 9) if count else 0,
            "primary_traffic": count,
        })
        bucket += timedelta(days=7)
    return {"metric_name": "latency", "data": data}


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["unchanged", "deleted-contributor", "stale-witness", "tie-conflict"]
)
def test_independent_full_span_graph(trace_engine, change):
    run, insert = trace_engine
    start, end = datetime(2025, 9, 5), datetime(2026, 9, 5)
    key = "agent.duration_s"
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC", "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        },
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE", "filter_type": "number",
                "filter_op": "greater_than", "filter_value": 1,
            },
        },
    ]
    stamp = datetime(2026, 3, 18, 12)
    common = {"trace_id": "one-trace", "start_time": stamp, "attrs_number": {}}
    insert(**common, id="root", latency_ms=51835)
    run(
        """INSERT INTO spans (project_id,observation_type,service_name,start_time,
            trace_id,id,parent_span_id,latency_ms,_version)
        SELECT toUUID(%(project)s),'span','service-a',
            toDateTime64(%(start)s,6,'UTC'),'one-trace',concat('c',toString(number)),
            'root',4000,1 FROM numbers(165)""",
        {"project": PROJECT, "start": stamp},
    )
    # The only positive child is OUTSIDE the output window. Membership still
    # succeeds; all166 in-window spans contribute, not only that child/root.
    witness = {
        **common, "id": "outside-child", "start_time": datetime(2026, 9, 6, 12),
        "parent_span_id": "root", "latency_ms": 99999, "attrs_number": {key: 2},
    }
    insert(**witness)
    if change == "deleted-contributor":
        insert(
            **common, id="c0", parent_span_id="root", latency_ms=4000,
            is_deleted=1, _version=2,
        )
    if change == "stale-witness":
        insert(**{**witness, "attrs_number": {key: 0}, "_version": 2})
    if change == "tie-conflict":
        insert(**common, id="c0", parent_span_id="root", latency_ms=999)
        with pytest.raises(ValueError, match="conflicting maximum version"):
            _fixture_reference(run, PROJECT, key, start, end)
        return

    expected = _fixture_reference(run, PROJECT, key, start, end)
    count = 0 if change == "stale-witness" else 165 if change == "deleted-contributor" else 166
    numerator = 0 if not count else 707835 if count == 165 else 711835
    assert sum(point["primary_traffic"] for point in expected["data"]) == count
    assert len(expected["data"]) == 53
    assert [point for point in expected["data"] if point["primary_traffic"]] == (
        [{
            "timestamp": "2026-03-16T00:00:00",
            "value": round(numerator / count, 9), "primary_traffic": count,
        }] if count else []
    )

    class Reader:
        def execute_ch_query(self, sql, params, **kwargs):
            return SimpleNamespace(
                data=run(sql, params), query_time_ms=0,
                columns=[
                    "time_bucket", "latency_sum", "total_tokens", "cost_sum",
                    "traffic_count", "prompt_tokens", "completion_tokens", "error_count",
                ],
            )

    actual = graphs.read_exact_system_graph(
        analytics=Reader(), project_id=PROJECT, filters=filters,
        interval="day", metric_id="latency", observe_type="trace",
    )
    assert {name: actual[name] for name in ("metric_name", "data")} == expected
