from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from tracer.services.clickhouse.exact_graph_reads import (
    _session_aggregate_source_sql,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)


def _local_ch25_client():
    """Return an explicitly local native client or skip the live proof."""

    host = os.environ.get("CH25_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("RMT identity proof is restricted to local ClickHouse")
    try:
        from clickhouse_driver import Client

        client = Client(
            host=host,
            port=int(
                os.environ.get("CH25_NATIVE_PORT")
                or os.environ.get("CH25_TCP_PORT")
                or "19000"
            ),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database="default",
            connect_timeout=int(os.environ.get("CH25_CONNECT_TIMEOUT", "2")),
            send_receive_timeout=int(os.environ.get("CH25_READ_TIMEOUT", "10")),
        )
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse is unavailable for RMT proof: {exc!r}")
    return client


def _time_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _attribute_filter() -> dict:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["Rejected"],
        },
    }


@pytest.mark.integration
def test_ch25_classifier_does_not_revive_pre_correction_live_span() -> None:
    """Collapse versions by the deployed CH25 RMT key, including start hour."""

    admin = _local_ch25_client()
    database = f"test_trace_classifier_rmt_{uuid4().hex}"
    project_id = str(uuid4())
    trace_id = "trace-with-corrected-child-time"
    start = datetime(2026, 8, 8)
    end = start + timedelta(hours=1)

    try:
        admin.execute(f"CREATE DATABASE {database}")
        admin.execute(f"USE {database}")
        admin.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                observation_type LowCardinality(String),
                service_name LowCardinality(String),
                start_time DateTime64(6, 'UTC'),
                trace_id String,
                id String,
                parent_span_id Nullable(String),
                attrs_string Map(String, String),
                latency_ms Int64,
                total_tokens Int64,
                cost Float64,
                prompt_tokens Int64,
                completion_tokens Int64,
                status String,
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version, is_deleted)
            PARTITION BY toDate(start_time)
            ORDER BY
            (
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
            )
            """
        )
        # Keep all physical versions present so the read query itself, rather
        # than a background merge, must implement replacement semantics.
        admin.execute("SYSTEM STOP MERGES spans")
        rows = [
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=1),
                trace_id,
                "root",
                None,
                {},
                1,
                1,
                0.01,
                1,
                0,
                "OK",
                0,
                1,
            ),
            # Both child rows share the deployed replacement identity. The
            # producer-corrected timestamp remains in the same start hour;
            # the newer tombstone must suppress the older matching value.
            (
                project_id,
                "span",
                "voice",
                start + timedelta(minutes=4, seconds=59),
                trace_id,
                "corrected-child",
                "root",
                {"final_status": "Rejected"},
                100,
                10,
                0.1,
                6,
                4,
                "OK",
                0,
                1,
            ),
            (
                project_id,
                "span",
                "voice",
                start + timedelta(minutes=5, seconds=1),
                trace_id,
                "corrected-child",
                "root",
                {"final_status": "Rejected"},
                200,
                20,
                0.2,
                12,
                8,
                "ERROR",
                1,
                2,
            ),
        ]
        for row in rows:
            admin.execute("INSERT INTO spans VALUES", [row])
        assert admin.execute("SELECT count() FROM spans") == [(3,)]
        assert admin.execute(
            """
            SELECT count()
            FROM
            (
                SELECT
                    argMax(is_deleted, _version) AS latest_is_deleted,
                    argMax(attrs_string['final_status'], _version) AS latest_status
                FROM spans
                WHERE id = 'corrected-child'
                GROUP BY project_id, trace_id, id, start_time
            )
            WHERE latest_is_deleted = 0 AND latest_status = 'Rejected'
            """
        ) == [(1,)]

        builder = TraceListQueryBuilderV2(
            project_id=project_id,
            filters=[_time_filter(start, end), _attribute_filter()],
            bounded_internal_scan=True,
            bounded_identity_only=True,
            bounded_bulk_scan=True,
            bounded_include_filter_witnesses=False,
            bounded_global_span_witnesses=True,
        )
        query, params = builder.build_filter_identity_match_query_from_seed_rows(
            [
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "root_span_id": "root",
                    "start_time": start + timedelta(minutes=1),
                }
            ]
        )

        assert (
            "GROUP BY observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id" in query
        )
        assert "argMax(start_time, _version) AS latest_start_time" in query
        assert admin.execute(query, params) == []
    finally:
        admin.execute("USE default")
        admin.execute(f"DROP DATABASE IF EXISTS {database}")


@pytest.mark.integration
def test_ch25_trace_contribution_collapses_complete_boundary_hour() -> None:
    """A corrected version just outside the window must defeat its old value."""

    admin = _local_ch25_client()
    database = f"test_trace_contribution_rmt_{uuid4().hex}"
    project_id = str(uuid4())
    trace_id = "trace-with-boundary-correction"
    start = datetime(2026, 8, 8)
    end = start + timedelta(minutes=5)

    try:
        admin.execute(f"CREATE DATABASE {database}")
        admin.execute(f"USE {database}")
        admin.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                observation_type LowCardinality(String),
                service_name LowCardinality(String),
                start_time DateTime64(6, 'UTC'),
                trace_id String,
                id String,
                latency_ms Int64,
                total_tokens Int64,
                cost Float64,
                prompt_tokens Int64,
                completion_tokens Int64,
                status String,
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version, is_deleted)
            PARTITION BY toDate(start_time)
            ORDER BY
            (
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
            )
            """
        )
        admin.execute("SYSTEM STOP MERGES spans")
        rows = [
            (
                project_id,
                "span",
                "voice",
                end - timedelta(seconds=1),
                trace_id,
                "corrected-span",
                100,
                10,
                0.1,
                6,
                4,
                "OK",
                0,
                1,
            ),
            (
                project_id,
                "span",
                "voice",
                end + timedelta(seconds=1),
                trace_id,
                "corrected-span",
                200,
                20,
                0.2,
                12,
                8,
                "ERROR",
                1,
                2,
            ),
        ]
        for row in rows:
            admin.execute("INSERT INTO spans VALUES", [row])
        assert admin.execute("SELECT count() FROM spans") == [(2,)]

        builder = TimeSeriesQueryBuilder(
            project_id=project_id,
            filters=[_time_filter(start, end)],
            interval="hour",
            exact_snapshot=True,
            observe_type="trace",
            start_date=start,
            end_date=end,
        )
        query, params = builder.build_exact_trace_contribution_batch([trace_id])

        assert params["graph_trace_scan_start"] == start
        assert params["graph_trace_scan_end"] == start + timedelta(hours=1)
        assert admin.execute(query, params) == []
    finally:
        admin.execute("USE default")
        admin.execute(f"DROP DATABASE IF EXISTS {database}")


@pytest.mark.integration
def test_ch25_exact_session_uses_sibling_membership_and_full_hydration() -> None:
    """Separate filter witnesses select one fully aggregated live session."""

    admin = _local_ch25_client()
    database = f"test_exact_session_membership_{uuid4().hex}"
    project_id = str(uuid4())
    session_id = str(uuid4())
    tombstoned_session_id = str(uuid4())
    stale_value_session_id = str(uuid4())
    start = datetime(2026, 8, 8)
    end = start + timedelta(minutes=5)
    filters = [
        _time_filter(start, end),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
        {
            "column_id": "customer_tier",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gold",
            },
        },
        {
            "column_id": "profile",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "map",
                "filter_op": "contains",
                "filter_value": {"tier": "gold", "enabled": True},
            },
        },
        {
            "column_id": "first_message",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "hello",
            },
        },
    ]

    try:
        admin.execute(f"CREATE DATABASE {database}")
        admin.execute(f"USE {database}")
        admin.execute(
            """
            CREATE TABLE trace_session_id_remap
            (
                old_id UUID,
                new_id UUID,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version)
            ORDER BY old_id
            """
        )
        admin.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                observation_type LowCardinality(String),
                service_name LowCardinality(String),
                start_time DateTime64(6, 'UTC'),
                trace_id String,
                id String,
                parent_span_id Nullable(String),
                trace_session_id UUID,
                end_time DateTime64(6, 'UTC'),
                input String,
                attrs_string Map(String, String),
                attributes_extra String,
                latency_ms Int64,
                total_tokens Int64,
                prompt_tokens Int64,
                completion_tokens Int64,
                cost Float64,
                status String,
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version, is_deleted)
            PARTITION BY toDate(start_time)
            ORDER BY
            (
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
            )
            """
        )
        admin.execute("SYSTEM STOP MERGES spans")
        rows = [
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=1),
                "trace-error",
                "root-error",
                None,
                session_id,
                start + timedelta(minutes=1, seconds=2),
                "hello customer",
                {},
                "{}",
                2,
                10,
                6,
                4,
                1.0,
                "ERROR",
                0,
                1,
            ),
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=2),
                "trace-tier",
                "root-tier",
                None,
                session_id,
                start + timedelta(minutes=2, seconds=3),
                "middle",
                {"customer_tier": "gold"},
                '{"profile":{"tier":"gold","enabled":true}}',
                3,
                90,
                50,
                40,
                9.0,
                "OK",
                0,
                1,
            ),
            # This root matches neither scalar leaf. It must still contribute
            # to every selected-session aggregate and the last message.
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=3),
                "trace-unfiltered",
                "root-unfiltered",
                None,
                session_id,
                start + timedelta(minutes=3, seconds=4),
                "final goodbye",
                {},
                "{}",
                4,
                900,
                500,
                400,
                90.0,
                "OK",
                0,
                1,
            ),
            # The old version is inside the exact request and satisfies both
            # leaves. Its newer same-hour tombstone corrects start_time just
            # outside the request. Exact-window PREWHERE before FINAL would
            # revive this stale session.
            (
                project_id,
                "conversation",
                "voice",
                end - timedelta(seconds=1),
                "trace-boundary-tombstone",
                "root-boundary-tombstone",
                None,
                tombstoned_session_id,
                end,
                "hello stale",
                {"customer_tier": "gold"},
                '{"profile":{"tier":"gold","enabled":true}}',
                777,
                777,
                700,
                77,
                777.0,
                "ERROR",
                0,
                1,
            ),
            (
                project_id,
                "conversation",
                "voice",
                end + timedelta(seconds=1),
                "trace-boundary-tombstone",
                "root-boundary-tombstone",
                None,
                tombstoned_session_id,
                end + timedelta(seconds=2),
                "hello stale",
                {"customer_tier": "gold"},
                '{"profile":{"tier":"gold","enabled":true}}',
                888,
                888,
                800,
                88,
                888.0,
                "ERROR",
                1,
                2,
            ),
            # Both versions remain live, but only the old physical version
            # carries the matching Map value. Raw-row membership would select
            # this session; latest-state membership must use "silver".
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=4),
                "trace-stale-map-value",
                "root-stale-map-value",
                None,
                stale_value_session_id,
                start + timedelta(minutes=4, seconds=1),
                "hello old map",
                {"customer_tier": "gold"},
                '{"profile":{"tier":"gold","enabled":true}}',
                333,
                333,
                300,
                33,
                333.0,
                "ERROR",
                0,
                1,
            ),
            (
                project_id,
                "conversation",
                "voice",
                start + timedelta(minutes=4, seconds=1),
                "trace-stale-map-value",
                "root-stale-map-value",
                None,
                stale_value_session_id,
                start + timedelta(minutes=4, seconds=2),
                "hello new map",
                {"customer_tier": "silver"},
                '{"profile":{"tier":"silver","enabled":true}}',
                444,
                444,
                400,
                44,
                444.0,
                "ERROR",
                0,
                2,
            ),
        ]
        admin.execute("INSERT INTO spans VALUES", rows)

        source, params = _session_aggregate_source_sql(
            project_id=project_id,
            filters=filters,
            start_date=start,
            end_date=end,
            include_trace_ids=False,
            anchor_by_session_start=True,
        )
        params.update({"start_date": start, "end_date": end})
        query = f"""
        SELECT
            toString(session_id),
            session_total_cost,
            session_total_tokens,
            session_prompt_tokens,
            session_completion_tokens,
            session_traces,
            first_message,
            last_message
        FROM ({source})
        """

        assert admin.execute(query, params) == [
            (session_id, 100.0, 1000, 556, 444, 3, "hello customer", "final goodbye")
        ]
    finally:
        admin.execute("USE default")
        admin.execute(f"DROP DATABASE IF EXISTS {database}")
