"""Real-ClickHouse proof that Project user graphs replay corrections/tombstones."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.agent_graph import (
    AgentGraphQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
    UserTimeSeriesQueryBuilderV2,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(
    os.environ.get("CH25_NATIVE_PORT") or os.environ.get("CH25_TCP_PORT") or "19000"
)
CH_USER = os.environ.get("CH25_USER") or os.environ.get("CH_USERNAME") or "default"
CH_PASSWORD = os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or ""


def _ch_client(*, database: str) -> Client:
    return Client(
        host=CH_HOST,
        port=CH_NATIVE_PORT,
        user=CH_USER,
        password=CH_PASSWORD,
        database=database,
        connect_timeout=3,
    )


@pytest.fixture(scope="module")
def ch_database():
    """Create one unique test-owned database and remove it after the module."""

    database = f"test_user_graph_{uuid.uuid4().hex}"
    _require_safe_ch25_test_target(host=CH_HOST, database=database)
    admin = _ch_client(database="default")
    created = False
    try:
        try:
            admin.execute("SELECT 1")
        except Exception as exc:
            pytest.skip(
                f"CH25 is not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})"
            )
        admin.execute(f"CREATE DATABASE {database}")
        created = True
        yield database
    finally:
        try:
            if created:
                # The exact target is an unguessable database created above;
                # no shared schema object is touched during cleanup.
                admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")
        finally:
            admin.disconnect()


@pytest.fixture(scope="module")
def ch_client(ch_database):
    client = _ch_client(database=ch_database)
    try:
        try:
            client.execute("SELECT 1")
        except Exception as exc:
            pytest.skip(
                f"CH25 is not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})"
            )
        yield client
    finally:
        client.disconnect()


@pytest.fixture()
def user_graph_tables(ch_client):
    suffix = uuid.uuid4().hex[:8]
    spans = f"_test_user_graph_spans_{suffix}"
    end_users = f"_test_user_graph_end_users_{suffix}"
    end_user_remap = f"_test_user_graph_eu_remap_{suffix}"
    trace_session_remap = f"_test_user_graph_ts_remap_{suffix}"

    ch_client.execute(
        f"""
        CREATE TABLE {spans} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            parent_span_id String,
            end_user_id Nullable(UUID),
            trace_session_id Nullable(UUID),
            latency_ms Int32,
            total_tokens Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            cost Float64,
            status String,
            created_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id, _version)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {end_users} (
            project_id UUID,
            end_user_id UUID,
            organization_id UUID,
            version UInt64,
            is_deleted UInt8
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (project_id, end_user_id)
        """
    )
    for table in (end_user_remap, trace_session_remap):
        ch_client.execute(
            f"""
            CREATE TABLE {table} (
                old_id UUID,
                new_id UUID,
                version UInt64
            ) ENGINE = ReplacingMergeTree(version)
            ORDER BY old_id
            """
        )
    try:
        yield spans, end_users, end_user_remap, trace_session_remap
    finally:
        for table in (spans, end_users, end_user_remap, trace_session_remap):
            ch_client.execute(f"DROP TABLE {table}")


def _execute(ch_client, query, params):
    rows, columns = ch_client.execute(
        query,
        params,
        with_column_types=True,
        settings={
            "max_threads": 1,
            "optimize_move_to_prewhere_if_final": 0,
            "use_skip_indexes_if_final": 0,
        },
    )
    names = [name for name, _type in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


def test_user_rollup_cursor_keeps_string_tie_order_across_pages(ch_client):
    """ORDER BY and keyset continuation use the same public String domain."""

    table = f"_test_user_rollup_cursor_{uuid.uuid4().hex[:8]}"
    curated_table = "end_users"
    organization_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    # Keep every row in the same non-zero DateTime64(6) bucket.  A Python
    # datetime bound through clickhouse-driver used to lose ``.052877`` and
    # skip the unconsumed suffix of this tie on the next keyset page.
    first_seen = datetime(2026, 8, 1, 10, 0, 0, 52_877, tzinfo=UTC)
    end_user_ids = [str(uuid.UUID(int=index)) for index in range(1, 42)]
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            end_user_id UUID,
            hour_first_seen DateTime('UTC'),
            first_seen AggregateFunction(min, DateTime64(6, 'UTC')),
            last_seen AggregateFunction(max, Nullable(DateTime64(6, 'UTC')))
        ) ENGINE = AggregatingMergeTree
        ORDER BY (project_id, end_user_id, hour_first_seen)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {curated_table} (
            project_id UUID,
            end_user_id UUID,
            organization_id UUID,
            user_id String,
            version UInt64,
            is_deleted UInt8
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (organization_id, project_id, end_user_id)
        """
    )
    try:
        ch_client.execute(
            f"""
            INSERT INTO {table}
            SELECT
                toUUID(%(project_id)s),
                arrayJoin(%(end_user_ids)s) AS end_user_id,
                toStartOfHour(toDateTime(%(first_seen)s)),
                minState(toDateTime64(%(first_seen)s, 6)),
                maxState(toNullable(toDateTime64(%(first_seen)s, 6)))
            GROUP BY end_user_id
            """,
            {
                "project_id": project_id,
                "end_user_ids": end_user_ids,
                "first_seen": first_seen,
            },
        )

        actual: list[str] = []
        before_first_seen = None
        before_end_user_id = None
        while True:
            builder = UserListQueryBuilder(
                organization_id=organization_id,
                project_ids=[project_id],
            )
            query, params = builder.build_dimension_candidate_query(
                limit=7,
                before_first_seen=before_first_seen,
                before_end_user_id=before_end_user_id,
                window_start=first_seen - timedelta(days=1),
                window_end=first_seen + timedelta(days=1),
            )
            query = query.replace(
                "FROM span_user_rollup AS rollup", f"FROM {table} AS rollup"
            )
            page = _execute(ch_client, query, params)
            if not page:
                break
            actual.extend(str(row["end_user_id"]) for row in page)
            before_first_seen = page[-1]["first_seen"]
            before_end_user_id = str(page[-1]["end_user_id"])

        assert actual == sorted(end_user_ids, reverse=True)
        assert len(actual) == len(set(actual)) == len(end_user_ids)
    finally:
        ch_client.execute(f"DROP TABLE {table}")
        ch_client.execute(f"DROP TABLE {curated_table}")


def test_exact_system_graph_statement_reads_current_latest_state(ch_client):
    """One raw source resolves corrections/tombstones through scalar argMax."""

    table = f"_test_exact_graph_snapshot_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            latency_ms Int32,
            total_tokens Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            cost Float64,
            status String,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        """
    )
    try:
        project_id = "00000000-0000-4000-8000-000000000061"
        started_at = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

        def row(
            trace_id: str,
            span_id: str,
            value: int,
            is_deleted: int,
            version: int,
        ):
            return (
                project_id,
                "llm",
                "svc",
                started_at,
                trace_id,
                span_id,
                value,
                value,
                value,
                0,
                float(value),
                "OK",
                is_deleted,
                version,
            )

        ch_client.execute(
            f"INSERT INTO {table} VALUES",
            [
                # Scalar argMax retains only the newest live correction.
                row("trace-a", "span-a", 10, 0, 1),
                row("trace-a", "span-a", 40, 0, 4),
                # A second corrected live row contributes its newest value.
                row("trace-b", "span-b", 20, 0, 1),
                row("trace-b", "span-b", 22, 0, 2),
                # The newest tombstone removes trace-c from current state.
                row("trace-c", "span-c", 30, 0, 1),
                row("trace-c", "span-c", 30, 1, 2),
                # Tombstone ordering is independent of version magnitude.
                row("trace-d", "span-d", 40, 0, 1),
                row("trace-d", "span-d", 40, 1, 4),
            ],
        )

        builder = TimeSeriesQueryBuilder(
            project_id=project_id,
            filters=[],
            interval="hour",
            exact_snapshot=True,
            observe_type="span",
            start_date=started_at - timedelta(hours=1),
            end_date=started_at + timedelta(hours=1),
        )
        builder.RAW_TABLE = table
        query, params = builder.build()
        rows = _execute(ch_client, query, params)

        assert len(rows) == 1
        assert rows[0]["traffic_count"] == 2
        assert rows[0]["total_tokens"] == 62
        assert query.count(f"FROM {table}") == 1
        assert f"FROM {table} FINAL" not in query
        assert "argMax(" in query
        assert "AS graph_physical_versions" in query
        latest_suffix = query.split(") AS graph_physical_versions", 1)[1]
        assert "WHERE tupleElement(graph_latest_row, 8) = 0" in latest_suffix
        assert "AS latest_spans" not in query
        assert "PREWHERE" in query
        assert "snapshot_version_ceiling" not in query
        assert "snapshot_version_ceiling" not in params
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def test_agent_graph_one_statement_replays_corrections_and_tombstones(ch_client):
    """Graph, topology, and path share the same newest-live physical rows."""

    table = f"_test_agent_graph_snapshot_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            trace_id String,
            id String,
            parent_span_id String,
            name String,
            latency_ms Int32,
            total_tokens Int32,
            cost Float64,
            status String,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        """
    )
    try:
        project_id = "00000000-0000-4000-8000-000000000063"
        started_at = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

        def row(
            span_id: str,
            parent_id: str,
            name: str,
            latency: int,
            deleted: int,
            version: int,
            offset: int,
            duration: int = 1,
            observation_type: str | None = None,
        ):
            span_start = started_at + timedelta(seconds=offset)
            return (
                project_id,
                observation_type or ("agent" if not parent_id else "tool"),
                "svc",
                span_start,
                span_start + timedelta(seconds=duration),
                "trace-a",
                span_id,
                parent_id,
                name,
                latency,
                latency,
                float(latency),
                "OK",
                deleted,
                version,
            )

        ch_client.execute(
            f"INSERT INTO {table} VALUES",
            [
                row("root", "", "agent", 100, 0, 1, 0),
                row("root", "", "agent", 10, 0, 2, 0),
                # Two overlapping direct siblings start at the same instant
                # and are both recorded children of root. Agent Graph may not
                # invent a sibling-to-sibling transition; hierarchy alone is
                # insufficient to publish an Agent Path.
                row("lookup", "root", "lookup", 20, 0, 1, 1, 4, "tool"),
                row("search", "root", "search", 25, 0, 1, 1, 2, "retriever"),
                # A later sibling remains a direct child in the hierarchy.
                row("answer", "root", "answer", 30, 0, 1, 6, 1, "llm"),
                # The newest physical version is a tombstone and contributes
                # neither a node nor a transition.
                row("deleted", "root", "deleted", 40, 0, 1, 8, 1, "tool"),
                row("deleted", "root", "deleted", 40, 1, 2, 8, 1, "tool"),
                # Membership witnesses are not constrained by the root's
                # selected window. This child is deliberately beyond the
                # retired +/- one-day witness heuristic.
                row(
                    "remote",
                    "root",
                    "remote-child",
                    50,
                    0,
                    1,
                    3 * 24 * 60 * 60,
                    1,
                    "tool",
                ),
            ],
        )

        builder = AgentGraphQueryBuilderV2(
            project_id=project_id,
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            started_at - timedelta(hours=1),
                            started_at + timedelta(hours=1),
                        ],
                    },
                }
            ],
        )
        builder.TABLE = table
        query, params = builder.build()
        payload = builder.format_result(_execute(ch_client, query, params), [])

        assert query.count(f"FROM {table}") == 1
        assert {node["name"] for node in payload["nodes"]} == {
            "agent",
            "lookup",
            "search",
            "answer",
        }
        root = next(node for node in payload["nodes"] if node["name"] == "agent")
        assert root["avg_latency_ms"] == 10
        assert {(edge["source"], edge["target"]) for edge in payload["edges"]} == {
            ("agent:agent", "tool:lookup"),
            ("agent:agent", "retriever:search"),
            ("agent:agent", "llm:answer"),
        }
        assert payload["path_edges"] == []

        remote_builder = AgentGraphQueryBuilderV2(
            project_id=project_id,
            filters=[
                *builder.filters,
                {
                    "column_id": "span_name",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "remote-child",
                    },
                },
            ],
        )
        remote_builder.TABLE = table
        remote_query, remote_params = remote_builder.build()
        remote_payload = remote_builder.format_result(
            _execute(ch_client, remote_query, remote_params), []
        )

        # The remote child qualifies its in-window root but never contributes
        # a visible node/edge outside the requested graph window.
        assert {node["name"] for node in remote_payload["nodes"]} == {
            "agent",
            "lookup",
            "search",
            "answer",
        }
        assert "remote-child" not in {node["name"] for node in remote_payload["nodes"]}
        assert remote_payload["edges"] == payload["edges"]
        assert "graph_witness_start_date" not in remote_query
        remote_prewhere = remote_query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
        assert "start_time >=" not in remote_prewhere
        assert "start_time <" not in remote_prewhere
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def test_exact_trace_graph_one_scan_matches_independent_sibling_filters(ch_client):
    """All filters use one scalar latest-row stream; all children contribute."""

    table = f"_test_exact_graph_siblings_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            parent_span_id String,
            latency_ms Int32,
            total_tokens Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            cost Float64,
            status String,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        """
    )
    try:
        project_id = "00000000-0000-4000-8000-000000000062"
        started_at = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

        def row(
            trace_id: str,
            span_id: str,
            parent_id: str,
            tokens: int,
            string_attrs: dict[str, str] | None = None,
            number_attrs: dict[str, float] | None = None,
            extra: str = "{}",
            version: int = 1,
            row_start: datetime | None = None,
        ):
            return (
                project_id,
                "llm",
                "svc",
                row_start or started_at,
                trace_id,
                span_id,
                parent_id,
                tokens,
                tokens,
                tokens,
                0,
                float(tokens),
                "OK",
                string_attrs or {},
                number_attrs or {},
                {},
                extra,
                0,
                version,
            )

        ch_client.execute(
            f"INSERT INTO {table} VALUES",
            [
                # Different children satisfy different filters. Every child
                # must still contribute once the trace satisfies every flag.
                row(
                    "trace-match",
                    "root",
                    "",
                    10,
                    {"final_status": "Rechazado"},
                ),
                row(
                    "trace-match",
                    "confidence",
                    "root",
                    20,
                    number_attrs={"confidence": 0.9},
                ),
                row(
                    "trace-match",
                    "structured",
                    "root",
                    30,
                    extra=(
                        '{"tags":["vip",7,true],'
                        '"profile":{"tier":"gold","enabled":true},'
                        '"legacy_payload":{"kind":"customer"}}'
                    ),
                ),
                # This trace lacks the structured predicates and must not leak.
                row(
                    "trace-incomplete",
                    "root-2",
                    "",
                    100,
                    {"final_status": "Rechazado"},
                    {"confidence": 0.95},
                ),
                # Scalar/system witnesses retain the legacy adjacent-day
                # lookup, but only exact-window children contribute.
                row(
                    "trace-boundary-scalar",
                    "inside-root",
                    "",
                    40,
                    number_attrs={"confidence": 0.9},
                    extra=(
                        '{"tags":["vip",7,true],'
                        '"profile":{"tier":"gold","enabled":true},'
                        '"legacy_payload":{"kind":"customer"}}'
                    ),
                ),
                row(
                    "trace-boundary-scalar",
                    "outside-scalar-witness",
                    "inside-root",
                    900,
                    {"final_status": "Rechazado"},
                    row_start=started_at - timedelta(hours=2),
                ),
                # Structured witnesses deliberately stay in the output window;
                # an adjacent JSON match must not admit this trace.
                row(
                    "trace-boundary-structured",
                    "inside-root-2",
                    "",
                    500,
                    {"final_status": "Rechazado"},
                    {"confidence": 0.9},
                ),
                row(
                    "trace-boundary-structured",
                    "outside-structured-witness",
                    "inside-root-2",
                    600,
                    extra=(
                        '{"tags":["vip",7,true],'
                        '"profile":{"tier":"gold","enabled":true},'
                        '"legacy_payload":{"kind":"customer"}}'
                    ),
                    row_start=started_at - timedelta(hours=2),
                ),
                # The version collapse must evaluate the latest attribute. The older
                # matching version cannot survive a newer non-matching value.
                row(
                    "trace-corrected",
                    "root-3",
                    "",
                    1000,
                    {"final_status": "Rechazado"},
                    {"confidence": 0.95},
                    '{"tags":["vip",7,true],"profile":{"tier":"gold",'
                    '"enabled":true},"legacy_payload":{"kind":"customer"}}',
                    1,
                ),
                row(
                    "trace-corrected",
                    "root-3",
                    "",
                    1000,
                    {"final_status": "Aprobado"},
                    {"confidence": 0.95},
                    '{"tags":["vip",7,true],"profile":{"tier":"gold",'
                    '"enabled":true},"legacy_payload":{"kind":"customer"}}',
                    2,
                ),
            ],
        )

        filters = [
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rechazado",
                },
            },
            {
                "column_id": "confidence",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "number",
                    "filter_op": "greater_than_or_equal",
                    "filter_value": 0.8,
                },
            },
            {
                "column_id": "tags",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "array",
                    "filter_op": "contains",
                    "filter_value": ["vip", 7, True],
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
                "column_id": "legacy_payload",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "json",
                    "filter_op": "contains",
                    "filter_value": {"kind": "customer"},
                },
            },
        ]
        builder = TimeSeriesQueryBuilder(
            project_id=project_id,
            filters=filters,
            interval="hour",
            exact_snapshot=True,
            observe_type="trace",
            start_date=started_at - timedelta(hours=1),
            end_date=started_at + timedelta(hours=1),
        )
        builder.RAW_TABLE = table
        query, params = builder.build()
        rows = _execute(ch_client, query, params)

        assert len(rows) == 1
        assert rows[0]["traffic_count"] == 4
        assert rows[0]["total_tokens"] == 100
        assert query.count(f"FROM {table}") == 1
        assert f"FROM {table} FINAL" not in query
        assert "argMax(" in query
        assert query.count("OVER (PARTITION BY trace_id) AS graph_match_") == 0
        assert query.count("AS graph_bucket_match_") == 5
        assert query.count("max(graph_bucket_match_") == 5
        collapse_suffix = query.split(") AS graph_physical_versions", 1)[1]
        assert "attrs_string" not in collapse_suffix
        assert "attrs_number" not in collapse_suffix
        assert "attributes_extra" not in collapse_suffix
        assert " IN (SELECT" not in query
        assert " INNER JOIN " not in query
        assert " LEFT JOIN " not in query
        assert " RIGHT JOIN " not in query
        assert " FULL JOIN " not in query
        assert "PREWHERE" in query
        assert params["graph_witness_start_date"] == (
            started_at - timedelta(hours=1) - timedelta(days=1)
        )
        assert params["graph_witness_end_date"] == (
            started_at + timedelta(hours=1) + timedelta(days=1)
        )
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def test_rmt_merge_proves_version_ceiling_is_not_time_travel(ch_client):
    """A fixed predicate cannot recover an old version after RMT merges it away."""

    table = f"_test_exact_graph_no_time_travel_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id UInt64,
            value UInt64,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        ORDER BY id
        """
    )
    try:
        ch_client.execute(f"SYSTEM STOP MERGES {table}")
        # Separate INSERTs create separate parts so both physical versions are
        # initially available to the pre-FINAL predicate.
        ch_client.execute(f"INSERT INTO {table} VALUES", [(1, 10, 0, 1)])
        ch_client.execute(f"INSERT INTO {table} VALUES", [(1, 40, 0, 4)])
        frozen_query = (
            f"SELECT value, _version FROM {table} FINAL PREWHERE _version < 3"
        )

        assert ch_client.execute(frozen_query) == [(10, 1)]

        ch_client.execute(f"SYSTEM START MERGES {table}")
        ch_client.execute(f"OPTIMIZE TABLE {table} FINAL")
        # The merged part contains only version 4. Neither PREWHERE nor
        # additional_table_filters can resurrect version 1 for a later query.
        assert ch_client.execute(frozen_query) == []
    finally:
        ch_client.execute(f"SYSTEM START MERGES {table}")
        ch_client.execute(f"DROP TABLE {table}")


def test_one_statement_pins_parts_across_concurrent_insert_and_merge(
    ch_client, ch_database
):
    """A running statement keeps its parts snapshot while new parts are merged."""

    table = f"_test_exact_graph_statement_snapshot_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id UInt64,
            value UInt64,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        ORDER BY id
        """
    )
    reader = _ch_client(database=ch_database)
    query_id = f"exact_statement_snapshot_{uuid.uuid4().hex}"
    outcome: dict[str, object] = {}

    def read_slow_snapshot():
        try:
            outcome["rows"] = reader.execute(
                f"""
                SELECT sum(value + if(sleepEachRow(0.01) = 0, 0, 0))
                FROM {table} FINAL
                """,
                query_id=query_id,
                settings={"max_threads": 1},
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            outcome["error"] = exc

    try:
        ch_client.execute(
            f"INSERT INTO {table} VALUES",
            [(row_id, 10, 0, 1) for row_id in range(50)],
        )
        thread = threading.Thread(target=read_slow_snapshot, daemon=True)
        thread.start()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if ch_client.execute(
                "SELECT count() FROM system.processes WHERE query_id = %(query_id)s",
                {"query_id": query_id},
            )[0][0]:
                break
            sleep(0.01)
        else:
            pytest.fail("slow snapshot query did not start")

        ch_client.execute(f"INSERT INTO {table} VALUES", [(0, 100, 0, 2)])
        ch_client.execute(f"OPTIMIZE TABLE {table} FINAL")
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert "error" not in outcome
        assert outcome["rows"] == [(500,)]
        assert ch_client.execute(f"SELECT sum(value) FROM {table} FINAL") == [(590,)]
    finally:
        reader.disconnect()
        ch_client.execute(f"DROP TABLE {table}")


def test_user_graphs_count_only_latest_live_span_rows(ch_client, user_graph_tables):
    spans, end_users, end_user_remap, trace_session_remap = user_graph_tables
    project_id = "00000000-0000-4000-8000-000000000071"
    organization_id = "00000000-0000-4000-8000-000000000072"
    end_user_id = "00000000-0000-4000-8000-000000000073"
    trace_session_id = "00000000-0000-4000-8000-000000000074"
    started_at = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)

    # One corrected span plus one tombstoned span. A raw physical-row aggregate
    # would report cost=169 and tokens=169; exact latest-live truth is 10/10.
    rows = [
        (
            project_id,
            "llm",
            "svc",
            started_at,
            "trace-live",
            "span-live",
            "",
            end_user_id,
            trace_session_id,
            900,
            100,
            40,
            60,
            100.0,
            "OK",
            started_at,
            0,
            1,
        ),
        (
            project_id,
            "llm",
            "svc",
            started_at,
            "trace-live",
            "span-live",
            "",
            end_user_id,
            trace_session_id,
            90,
            10,
            4,
            6,
            10.0,
            "OK",
            started_at,
            0,
            2,
        ),
        (
            project_id,
            "llm",
            "svc",
            started_at + timedelta(minutes=1),
            "trace-deleted",
            "span-deleted",
            "",
            end_user_id,
            trace_session_id,
            500,
            50,
            20,
            30,
            50.0,
            "ERROR",
            started_at + timedelta(minutes=1),
            0,
            1,
        ),
        (
            project_id,
            "llm",
            "svc",
            started_at + timedelta(minutes=1),
            "trace-deleted",
            "span-deleted",
            "",
            end_user_id,
            trace_session_id,
            500,
            50,
            20,
            30,
            50.0,
            "ERROR",
            started_at + timedelta(minutes=1),
            1,
            2,
        ),
    ]
    ch_client.execute(f"INSERT INTO {spans} VALUES", rows)
    ch_client.execute(
        f"INSERT INTO {end_users} VALUES",
        [(project_id, end_user_id, organization_id, 1, 0)],
    )

    date_filter = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                started_at - timedelta(hours=1),
                started_at + timedelta(hours=1),
            ],
        },
    }

    aggregate = UserTimeSeriesQueryBuilderV2(
        project_id=project_id,
        filters=[date_filter],
        interval="hour",
    )
    aggregate.TABLE = spans
    aggregate.END_USER_REMAP_TABLE = end_user_remap
    aggregate_query, aggregate_params = aggregate.build()
    aggregate_rows = _execute(ch_client, aggregate_query, aggregate_params)

    assert len(aggregate_rows) == 1
    assert aggregate_rows[0]["active_users"] == 1
    assert aggregate_rows[0]["total_cost_sum"] == 10.0
    assert aggregate_rows[0]["total_tokens"] == 10
    assert aggregate_rows[0]["error_rate"] == 0

    old_value_filter = {
        "column_id": "cost",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 20,
        },
    }
    corrected_filter_builder = UserTimeSeriesQueryBuilderV2(
        project_id=project_id,
        filters=[date_filter, old_value_filter],
        interval="hour",
    )
    corrected_filter_builder.TABLE = spans
    corrected_filter_builder.END_USER_REMAP_TABLE = end_user_remap
    corrected_query, corrected_params = corrected_filter_builder.build()
    # Neither the corrected old cost=100 row nor the tombstoned cost=50 row
    # may satisfy a filter compiled against latest state.
    assert _execute(ch_client, corrected_query, corrected_params) == []

    detail = UserDetailTimeSeriesQueryBuilderV2(
        project_id=project_id,
        organization_id=organization_id,
        end_user_id=end_user_id,
        filters=[date_filter],
        interval="hour",
    )
    detail.TABLE = spans
    detail.END_USERS_TABLE = end_users
    detail.END_USER_REMAP_TABLE = end_user_remap
    detail.TRACE_SESSION_REMAP_TABLE = trace_session_remap
    detail_query, detail_params = detail.build()
    detail_rows = _execute(ch_client, detail_query, detail_params)

    assert len(detail_rows) == 1
    assert detail_rows[0]["session_count"] == 1
    assert detail_rows[0]["trace_count"] == 1
    assert detail_rows[0]["cost"] == 10.0
    assert detail_rows[0]["input_tokens"] == 4
    assert detail_rows[0]["output_tokens"] == 6

    filtered_detail = UserDetailTimeSeriesQueryBuilderV2(
        project_id=project_id,
        organization_id=organization_id,
        end_user_id=end_user_id,
        filters=[date_filter, old_value_filter],
        interval="hour",
    )
    filtered_detail.TABLE = spans
    filtered_detail.END_USERS_TABLE = end_users
    filtered_detail.END_USER_REMAP_TABLE = end_user_remap
    filtered_detail.TRACE_SESSION_REMAP_TABLE = trace_session_remap
    filtered_query, filtered_params = filtered_detail.build()
    assert _execute(ch_client, filtered_query, filtered_params) == []
