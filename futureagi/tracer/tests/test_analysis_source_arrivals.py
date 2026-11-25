"""Arrival provenance: bounded SQL, DTO hydration, and a real RMT proof."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from conftest import _open_ch_test_http_client
from tracer.services.clickhouse.v2.span_reader import (
    _DATA_KEYS,
    CHSpanReader,
    _row_to_chspan,
)


@pytest.mark.unit
def test_arrival_reader_is_bounded_metadata_only_and_empty_reads_nothing():
    calls = []
    reader = object.__new__(CHSpanReader)

    def query(sql, **kwargs):
        calls.append((sql, kwargs))
        return SimpleNamespace(result_rows=[])

    reader._client = SimpleNamespace(query=query)
    assert reader.source_arrivals_by_trace_ids([], project_id="p") == {}
    assert calls == []
    reader.source_arrivals_by_trace_ids(
        [str(i) for i in range(1001)], project_id="p", version_cutoff=7
    )
    assert [len(args["parameters"]["ids"]) for _, args in calls] == [1000, 1]
    for sql, args in calls:
        assert args["parameters"]["pid"] == "p" and args["parameters"]["cutoff"] == 7
        assert "argMax(created_at, _version)" in sql
        assert "(_version <= %(cutoff)s OR is_deleted = 1)" in sql
        assert ") WHERE latest_deleted = 0" in sql
        assert "input" not in sql and "output" not in sql and "SELECT *" not in sql


@pytest.mark.unit
def test_source_arrival_is_hydrated_separately_from_event_time():
    event = datetime(2020, 1, 1, tzinfo=UTC)
    arrival = datetime(2026, 11, 3, tzinfo=UTC)
    values = dict.fromkeys(_DATA_KEYS)
    values.update(id="span", trace_id="trace", start_time=event, created_at=arrival)
    span = _row_to_chspan(tuple(values[key] for key in _DATA_KEYS))
    assert span.start_time == event and span.created_at == arrival


@pytest.mark.unit
def test_activity_projects_and_hydrates_child_arrival():
    at = datetime(2026, 11, 3, tzinfo=UTC)
    values = dict.fromkeys(CHSpanReader._ACTIVITY_KEYS)
    values.update(
        trace_id="trace", id="child", start_time=at, created_at=at - timedelta(days=40)
    )

    def query(sql, **kwargs):
        assert "AS in_trace, created_at FROM" in sql
        assert "start_time, created_at, status" in sql
        return SimpleNamespace(
            result_rows=[tuple(values[key] for key in CHSpanReader._ACTIVITY_KEYS)]
        )

    reader = object.__new__(CHSpanReader)
    reader._client = SimpleNamespace(query=query)
    rows = reader.activity_by_trace_ids(
        ["trace"], project_id="project", media_patterns=("image", "audio")
    )
    assert rows == [values]


@pytest.mark.integration
def test_real_ch_arrival_uses_logical_versions_children_cutoff_and_project_scope():
    database = os.environ.get("CH25_DATABASE") or os.environ.get("CH_DATABASE")
    if not database:
        pytest.skip("no test ClickHouse configured")
    if not database.startswith("test_"):
        pytest.fail("arrival provenance requires a test_* ClickHouse database")
    # The shared guard resolves the port without a default, rejects unsafe
    # targets and proves CI's opted-in sidecar identity before any DDL.
    client = _open_ch_test_http_client(
        database=database, connect_timeout=5, send_receive_timeout=30
    )
    table = "arrival_" + uuid4().hex
    project, other = str(uuid4()), str(uuid4())
    event = datetime(2020, 1, 1, tzinfo=UTC)
    now = datetime(2026, 11, 3, tzinfo=UTC)
    child = now - timedelta(days=40)
    ancient = now - timedelta(days=400)
    try:
        client.command(
            f"CREATE TABLE {table} (project_id UUID, observation_type String, service_name String, start_time DateTime64(6, 'UTC'), trace_id String, id String, created_at DateTime64(6, 'UTC'), _version UInt64, is_deleted UInt8) ENGINE=ReplacingMergeTree(_version, is_deleted) ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)"
        )
        client.command(f"SYSTEM STOP MERGES {table}")
        rows = [
            [project, "agent", "svc", event, "imported", "root", now, 1, 0],
            [project, "agent", "svc", now, "with-child", "root", now, 1, 0],
            [project, "tool", "svc", now, "with-child", "tool", child, 1, 0],
            [project, "tool", "svc", now, "with-child", "duplicate", ancient, 1, 0],
            [project, "tool", "svc", now, "with-child", "duplicate", now, 2, 0],
            [project, "tool", "svc", now, "with-child", "deleted", ancient, 1, 0],
            [project, "tool", "svc", now, "with-child", "deleted", ancient, 3, 1],
            [other, "tool", "svc", now, "with-child", "foreign", ancient, 1, 0],
            [project, "tool", "svc", now, "future", "late", ancient, 4, 0],
        ]
        # Separate parts keep stale physical versions present during the proof.
        for row in rows:
            client.insert(
                table,
                [row],
                column_names=[
                    "project_id",
                    "observation_type",
                    "service_name",
                    "start_time",
                    "trace_id",
                    "id",
                    "created_at",
                    "_version",
                    "is_deleted",
                ],
            )
        assert client.query(f"SELECT count() FROM {table}").result_rows[0][0] == len(
            rows
        )
        reader = object.__new__(CHSpanReader)
        reader._client = SimpleNamespace(
            query=lambda sql, **kw: client.query(
                sql.replace("FROM spans", f"FROM {table}"), **kw
            )
        )
        result = reader.source_arrivals_by_trace_ids(
            ["imported", "with-child", "future"], project_id=project, version_cutoff=2
        )
        assert {key: value.replace(tzinfo=UTC) for key, value in result.items()} == {
            "imported": now,
            "with-child": child,
        }
        latest = reader.source_arrivals_by_trace_ids(
            ["with-child", "future"], project_id=project
        )
        assert {key: value.replace(tzinfo=UTC) for key, value in latest.items()} == {
            "with-child": child,
            "future": ancient,
        }
    finally:
        try:
            client.command(f"DROP TABLE IF EXISTS {table}")
        finally:
            client.close()
