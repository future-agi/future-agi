"""Real-ClickHouse proof that the projected session rollup graph is unchanged.

The statement this PR deletes is carried here verbatim as
``LEGACY_ROLLUP_STATEMENT``, so a real server answers the only question that
matters: does the narrow statement return the same numbers?

This module issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket: see ``_live_native_port``. Nothing here resolves a
default port, because a developer host's well-known ClickHouse ports are held
by port-forwards to shared clusters.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services.clickhouse.query_builders.session_time_series import (
    SessionRollupTimeSeriesQueryBuilder,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_USER = os.environ.get("CH25_USER") or os.environ.get("CH_USERNAME") or "default"
CH_PASSWORD = os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or ""

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"

# Native ports a developer host keeps pointed at shared ClickHouse clusters.
# This suite creates and drops objects, so it refuses them outright rather
# than trusting whoever exported the variable to have meant the test stack.
REFUSED_NATIVE_PORTS = frozenset({19000, 19001, 19002, 19010, *range(18230, 18233)})

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT_ID = "99999999-9999-4999-8999-999999999999"
SESSION_A = "22222222-2222-4222-8222-222222222222"
SESSION_B = "33333333-3333-4333-8333-333333333333"
SESSION_C = "44444444-4444-4444-8444-444444444444"

# The statement this PR deletes, kept as the equality oracle.
LEGACY_ROLLUP_STATEMENT = """
        SELECT
            toStartOfDay(session_start) AS time_bucket,
            avg(session_latency) AS avg_latency,
            sum(session_total_tokens) AS total_tokens,
            avg(session_total_cost) AS avg_cost,
            count() AS traffic_count,
            sum(session_prompt_tokens) AS prompt_tokens,
            sum(session_completion_tokens) AS completion_tokens,
            countIf(session_error_count > 0) * 100.0
                / greatest(count(), 1) AS error_rate,
            count() AS session_count,
            avg(dateDiff('second', session_start,
                coalesce(session_end, session_start))) AS avg_duration,
            CAST(NULL, 'Nullable(Float64)') AS avg_traces_per_session,
            sum(session_total_cost) AS total_cost_sum
        FROM (
            SELECT
                sps.trace_session_id AS session_id,
                minMerge(sps.first_seen) AS session_start,
                maxMerge(sps.last_seen) AS session_end,
                sumMerge(sps.total_tokens_sum) AS session_total_tokens,
                sumMerge(sps.prompt_tokens_sum) AS session_prompt_tokens,
                sumMerge(sps.completion_tokens_sum)
                    AS session_completion_tokens,
                sumMerge(sps.cost_sum) AS session_total_cost,
                (quantilesTDigestMerge(0.5, 0.95, 0.99)(sps.latency_q))[1]
                    AS session_latency,
                countIfMerge(sps.error_count) AS session_error_count
            FROM spans_per_session AS sps
            PREWHERE sps.project_id = toUUID(%(project_id)s)
              AND sps.hour_first_seen >= %(rollup_scan_start)s
              AND sps.hour_first_seen < %(rollup_scan_end)s
            GROUP BY session_id
        ) AS sessions
        WHERE session_start >= %(start_date)s
          AND session_start < %(end_date)s
        GROUP BY time_bucket
        ORDER BY time_bucket
"""

# metric_id -> the result column both statements must agree on.
METRIC_COLUMNS = {
    "traffic": "traffic_count",
    "session_count": "session_count",
    "cost": "avg_cost",
    "total_cost": "total_cost_sum",
    "tokens": "total_tokens",
    "total_tokens": "total_tokens",
    "prompt_tokens": "prompt_tokens",
    "input_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "output_tokens": "completion_tokens",
    "error_rate": "error_rate",
    "avg_duration": "avg_duration",
    "latency": "avg_latency",
}

WINDOW_START = datetime(2026, 3, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 3, 4, tzinfo=UTC)


def _live_native_port() -> int:
    """Return the opted-into native port, or skip before any socket is opened.

    There is deliberately no default and no fallback chain onto a sibling
    variable. An unpinned run must not resolve to whatever happens to be
    listening on a well-known port: on a developer host those are held by
    port-forwards to shared clusters, and this suite runs ``CREATE``/``INSERT``
    as an admin user. The caller names the disposable stack, or gets a skip.
    """

    if os.environ.get(LIVE_CH_TESTS_ENV_VAR) != "1":
        pytest.skip(f"live ClickHouse tests are opt-in: set {LIVE_CH_TESTS_ENV_VAR}=1")

    raw_port = os.environ.get("CH25_NATIVE_PORT", "").strip()
    if not raw_port:
        pytest.skip(
            "CH25_NATIVE_PORT is not set; this suite will not guess a "
            "ClickHouse port for a test that writes"
        )
    try:
        port = int(raw_port)
    except ValueError:
        pytest.skip(f"CH25_NATIVE_PORT={raw_port!r} is not a port number")

    if port in REFUSED_NATIVE_PORTS:
        pytest.skip(
            f"refusing to write to ClickHouse on port {port}: that port is "
            "reserved for port-forwards to shared clusters on this host"
        )
    return port


@pytest.fixture(scope="module")
def ch_port() -> int:
    return _live_native_port()


def _ch_client(*, port: int, database: str) -> Client:
    return Client(
        host=CH_HOST,
        port=port,
        user=CH_USER,
        password=CH_PASSWORD,
        database=database,
        connect_timeout=3,
    )


def _rollup_table_ddl() -> str:
    """Use the shipped schema statement so the fixture cannot drift from it."""

    schema = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "clickhouse"
        / "v2"
        / "schema"
        / "008_per_session_rollup.sql"
    ).read_text()
    table_sql = schema.split("CREATE MATERIALIZED VIEW", 1)[0]
    start = table_sql.index("CREATE TABLE IF NOT EXISTS spans_per_session")
    statement = table_sql[start : table_sql.index(";", start)]
    # The shipped TTL deletes rows older than 90 days on merge; the fixture
    # seeds a fixed historical window, so the retention clause is dropped
    # rather than the seed being made relative to the clock.
    return re.sub(r"\nTTL [^\n]*\n", "\n", statement).strip()


@pytest.fixture(scope="module")
def ch_database(ch_port: int):
    database = f"test_session_rollup_{uuid.uuid4().hex}"
    _require_safe_ch25_test_target(host=CH_HOST, database=database)
    admin = _ch_client(port=ch_port, database="default")
    created = False
    try:
        try:
            admin.execute("SELECT 1")
        except Exception as exc:
            pytest.skip(f"CH25 is not reachable on {CH_HOST}:{ch_port} ({exc!r})")
        admin.execute(f"CREATE DATABASE {database}")
        created = True
        yield database
    finally:
        try:
            if created:
                # The target is the unguessable database created above; no
                # shared schema object is touched during cleanup.
                admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")
        finally:
            admin.disconnect()


@pytest.fixture(scope="module")
def ch_client(ch_port: int, ch_database: str):
    client = _ch_client(port=ch_port, database=ch_database)
    try:
        client.execute("SELECT 1")
        client.execute(_rollup_table_ddl())
        client.execute("""
            CREATE TABLE seed_spans
            (
                project_id UUID,
                trace_session_id UUID,
                start_time DateTime64(6, 'UTC'),
                end_time Nullable(DateTime64(6, 'UTC')),
                latency_ms Int32,
                total_tokens Int32,
                prompt_tokens Int32,
                completion_tokens Int32,
                cost Float64,
                status String
            ) ENGINE = Memory
            """)
        _seed(client)
        yield client
    finally:
        client.disconnect()


def _seed(client: Client) -> None:
    """Insert one rollup part per row, mirroring the shipped MV's SELECT.

    Session A spans two hours and two parts, so its states really have to
    merge; session C never ended (NULL end_time); the last row belongs to
    another project and must not reach any bucket.
    """

    # project, session, hours after the window start, duration (s, None while
    # in flight), latency ms, total/prompt/completion tokens, cost, status
    rows = [
        (PROJECT_ID, SESSION_A, 10.0, 20, 120, 10, 6, 4, 0.5, "OK"),
        (PROJECT_ID, SESSION_A, 11.5, None, 300, 20, 12, 8, 1.5, "ERROR"),
        (PROJECT_ID, SESSION_B, 33.0, 60, 90, 5, 3, 2, 0.25, "OK"),
        (PROJECT_ID, SESSION_C, 37.0, None, 45, 7, 4, 3, 0.75, "OK"),
        (OTHER_PROJECT_ID, SESSION_B, 33.0, 10, 999, 999, 999, 999, 99.0, "ERROR"),
    ]
    for project_id, session_id, offset_hours, duration_s, *metrics in rows:
        start_time = WINDOW_START + timedelta(hours=offset_hours)
        end_time = (
            None if duration_s is None else start_time + timedelta(seconds=duration_s)
        )
        client.execute(
            "INSERT INTO seed_spans (project_id, trace_session_id, start_time, "
            "end_time, latency_ms, total_tokens, prompt_tokens, "
            "completion_tokens, cost, status) VALUES",
            [(project_id, session_id, start_time, end_time, *metrics)],
        )
        client.execute(
            """
            INSERT INTO spans_per_session
            SELECT
                project_id,
                trace_session_id,
                toStartOfHour(min(start_time)),
                countState(),
                sumState(toInt64(total_tokens)),
                sumState(toInt64(prompt_tokens)),
                sumState(toInt64(completion_tokens)),
                sumState(cost),
                quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms),
                minState(start_time),
                maxState(end_time),
                countIfState(status = 'ERROR')
            FROM seed_spans
            WHERE start_time = %(start_time)s AND project_id = toUUID(%(pid)s)
            GROUP BY project_id, trace_session_id
            """,
            {"start_time": start_time, "pid": project_id},
        )


def _builder(metric_id: str) -> SessionRollupTimeSeriesQueryBuilder:
    return SessionRollupTimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        interval="day",
        metric_id=metric_id,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        WINDOW_START.isoformat(),
                        WINDOW_END.isoformat(),
                    ],
                },
            }
        ],
    )


def _rows(client, query, params, *, settings=None):
    rows, columns = client.execute(
        query,
        params,
        settings=settings,
        with_column_types=True,
    )
    names = [name for name, _type in columns]
    return [dict(zip(names, row, strict=False)) for row in rows]


def _scanned_columns(client, query, params) -> set[str]:
    """The columns the scan itself reads, from the plan's innermost header."""

    explained = client.execute("EXPLAIN header = 1 " + query, params)
    text = "\n".join(row[0] for row in explained)
    header = text.split("ReadFromMergeTree", 1)[1].split("Header:", 1)[1]
    return {match.group(1) for match in re.finditer(r"^\s+(\w+) \S", header, re.M)}


@pytest.fixture(scope="module")
def legacy_rows(ch_client):
    params = _builder("traffic").build()[1]
    rows = _rows(ch_client, LEGACY_ROLLUP_STATEMENT, params)
    assert rows, "the fixture must produce at least one populated bucket"
    return rows


@pytest.mark.parametrize("metric_id", sorted(METRIC_COLUMNS))
def test_projected_statement_returns_todays_numbers(ch_client, legacy_rows, metric_id):
    column = METRIC_COLUMNS[metric_id]
    query, params = _builder(metric_id).build()

    rows = _rows(
        ch_client,
        query,
        params,
        settings={"max_threads": 4, "optimize_aggregation_in_order": 1},
    )

    assert [row["time_bucket"] for row in rows] == [
        row["time_bucket"] for row in legacy_rows
    ]
    assert [row["traffic_count"] for row in rows] == [
        row["traffic_count"] for row in legacy_rows
    ]
    assert [row[column] for row in rows] == [row[column] for row in legacy_rows]


def test_legacy_statement_reads_every_state_column(ch_client):
    params = _builder("traffic").build()[1]

    assert _scanned_columns(ch_client, LEGACY_ROLLUP_STATEMENT, params) == {
        "trace_session_id",
        "first_seen",
        "last_seen",
        "total_tokens_sum",
        "prompt_tokens_sum",
        "completion_tokens_sum",
        "cost_sum",
        "latency_q",
        "error_count",
    }


@pytest.mark.parametrize(
    ("metric_id", "expected"),
    [
        ("traffic", {"trace_session_id", "first_seen"}),
        ("session_count", {"trace_session_id", "first_seen"}),
        ("cost", {"trace_session_id", "first_seen", "cost_sum"}),
        ("tokens", {"trace_session_id", "first_seen", "total_tokens_sum"}),
        ("error_rate", {"trace_session_id", "first_seen", "error_count"}),
        ("avg_duration", {"trace_session_id", "first_seen", "last_seen"}),
        ("latency", {"trace_session_id", "first_seen", "latency_q"}),
    ],
)
def test_projected_statement_reads_only_its_own_columns(ch_client, metric_id, expected):
    query, params = _builder(metric_id).build()

    assert _scanned_columns(ch_client, query, params) == expected


def test_sessions_aggregate_in_sort_key_order(ch_client):
    query, params = _builder("traffic").build()

    pipeline = ch_client.execute(
        "EXPLAIN PIPELINE " + query,
        params,
        settings={"max_threads": 4, "optimize_aggregation_in_order": 1},
    )
    text = "\n".join(row[0] for row in pipeline)

    # Without the setting the same statement builds a hash table over every
    # session in the window instead.
    assert "AggregatingInOrderTransform" in text
