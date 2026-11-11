"""A large but current CDC mirror must not look unhealthy because it is large.

Health statements are capped at about one second. ``_peerdb_synced_at`` is in
no sorting or partition key of the CDC mirrors, so a whole-table
``max(_peerdb_synced_at)`` reads every row. On mirrors of a few hundred million
rows that scan outgrew the cap and the endpoint reported ``unhealthy`` with lag
-1 while CDC was current. The lag probe now reads only the newest parts that fit
a fixed row budget.

These tests use the ClickHouse test sidecar, the production CDC DDL (engine made
non-replicated; partition and sorting keys unchanged) and synthetic rows only.
Scan speed varies by machine, so instead of a giant table the size dependence is
pinned two deterministic ways:

* ClickHouse's own progress counters: the lag statement must read no more than
  the sample budget from a table many times larger than it.
* The probing users carry a server-side ``max_rows_to_read`` well below the
  table size. It stands in for the time cap: the server refuses a whole-table
  scan and accepts a size-independent sample, on any hardware.

Both server-enforced readonly=1 and client readonly=2 profiles are covered. The
sample needs ``SELECT`` on six ``system.parts`` columns, which stock ClickHouse
configs require a grant for (``select_from_system_db_requires_grant``); a role
without that grant keeps the whole-table aggregate and issues no denied query.
"""

import re
import time
from contextlib import ExitStack

import pytest
from structlog.testing import capture_logs

from tracer.tests import test_clickhouse_consistency as health_tests

configure = health_tests.configure

CDC_TABLES = ("tracer_trace", "trace_session", "tracer_eval_logger")
_DDL = {
    "tracer_trace": "CDC_TRACE",
    "trace_session": "CDC_TRACE_SESSION",
    "tracer_eval_logger": "CDC_EVAL_LOGGER",
}
LARGE_ROWS = 2_000_000
# Far below the table and every bulk part, far above a fresh CDC batch.
SAMPLE_BUDGET = 20_000
# Server-side scan limit for the probing users: room for the metadata reads and
# the sample, an order of magnitude short of the whole table.
SERVER_ROW_LIMIT = 200_000
PART_COLUMNS = "database, table, name, rows, active, modification_time"
AGGREGATE = "SELECT count(), max(_peerdb_synced_at) FROM tracer_trace"
TOO_MANY_ROWS = 158


def _production_ddl(table):
    from tracer.services.clickhouse import schema

    ddl = re.sub(
        r"ReplicatedReplacingMergeTree\('[^']*',\s*'\{replica\}',\s*_peerdb_version\)",
        "ReplacingMergeTree(_peerdb_version)",
        getattr(schema, _DDL[table]),
    )
    assert "Replicated" not in ddl
    assert "PARTITION BY toYYYYMM(created_at)" in ddl
    return ddl.replace("IF NOT EXISTS ", "").strip().rstrip(";")


def _create_mirrors(admin):
    for table in CDC_TABLES:
        admin.execute(_production_ddl(table))


def _fill(admin, rows, *, synced_age_seconds=0):
    # About 60 days of created_at over 8 projects, so several monthly partitions.
    admin.execute(
        "INSERT INTO tracer_trace (id, project_id, created_at, updated_at, "
        "_peerdb_synced_at, _peerdb_is_deleted, _peerdb_version) "
        "SELECT generateUUIDv4(number), "
        "toUUID(concat('00000000-0000-4000-8000-00000000000', toString(number % 8))), "
        "now64(3) - toIntervalSecond(number % 5184000), "
        "now64(3) - toIntervalSecond(number % 5184000), "
        f"toDateTime64(now64(3) - toIntervalSecond({int(synced_age_seconds)} "
        "+ number % 5184000), 6), 0, number "
        f"FROM numbers_mt({int(rows)})",
        settings={"max_insert_threads": 8, "max_threads": 8},
    )


def _settle(admin, limit_s=180):
    """Let merges finish, then freeze the part layout for a deterministic read."""
    started = time.monotonic()
    while admin.execute(
        "SELECT count() FROM system.merges WHERE database = currentDatabase()"
    )[0][0]:
        if time.monotonic() - started > limit_s:
            pytest.fail(f"precondition: merges did not finish within {limit_s}s")
        time.sleep(1)
    for table in CDC_TABLES:
        admin.execute(f"SYSTEM STOP MERGES {table}")


def _sync_now(admin):
    """Emulate a live CDC batch: one row synced now in every mirror."""
    for table in CDC_TABLES:
        admin.execute(
            f"INSERT INTO {table} (id, created_at, updated_at, _peerdb_synced_at, "
            "_peerdb_is_deleted, _peerdb_version) "
            "SELECT generateUUIDv4(), now64(3), now64(3), now64(6), 0, 999999999999"
        )


@pytest.fixture(scope="module")
def large_mirror():
    """A mirror many times the sample budget, plus probing users per profile."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with ExitStack() as stack:
        database = stack.enter_context(_ch_test_owned_database("test_health_lag_"))
        admin = stack.enter_context(_ch_test_native_client(database=database))
        _create_mirrors(admin)
        _fill(admin, LARGE_ROWS)
        _settle(admin)
        users = {
            "readonly2": f"{database}_rw",
            "server_readonly1": f"{database}_ro",
            "no_parts_grant": f"{database}_np",
        }
        for profile, user in users.items():
            settings = {
                "readonly2": f"max_rows_to_read = {SERVER_ROW_LIMIT}",
                "server_readonly1": (
                    f"readonly = 1, max_rows_to_read = {SERVER_ROW_LIMIT}"
                ),
                "no_parts_grant": "readonly = 1",
            }[profile]
            stack.callback(admin.execute, f"DROP USER IF EXISTS {user}")
            admin.execute(
                f"CREATE USER {user} IDENTIFIED WITH no_password SETTINGS {settings}"
            )
            admin.execute(f"GRANT SELECT ON {database}.* TO {user}")
            if profile != "no_parts_grant":
                admin.execute(f"GRANT SELECT({PART_COLUMNS}) ON system.parts TO {user}")
        yield admin, database, users


def _probe_client(admin, database, user=None, *, server_readonly=False):
    from tracer.services.clickhouse.client import ClickHouseClient

    connection = admin.connection
    return ClickHouseClient(
        host=connection.host,
        port=connection.port,
        database=database,
        user=user or connection.user,
        password="" if user else connection.password,
        server_enforced_readonly=server_readonly,
    )


@pytest.fixture
def statements(monkeypatch):
    """Record every health statement and the rows ClickHouse says it read."""
    from tracer.services.clickhouse.health_client import HealthCheckClient

    recorded = []
    original_create = HealthCheckClient._create_client

    def create_client(self, **kwargs):
        driver = original_create(self, **kwargs)
        execute = driver.execute

        def recording_execute(sql, *args, **kwargs):
            entry = {"sql": sql, "rows": None, "error": None}
            recorded.append(entry)
            try:
                result = execute(sql, *args, **kwargs)
            except Exception as exc:
                entry["error"] = getattr(exc, "code", type(exc).__name__)
                raise
            progress = getattr(getattr(driver, "last_query", None), "progress", None)
            entry["rows"] = getattr(progress, "rows", None)
            return result

        monkeypatch.setattr(driver, "execute", recording_execute)
        return driver

    monkeypatch.setattr(HealthCheckClient, "_create_client", create_client)
    return recorded


@pytest.fixture
def small_budget(monkeypatch):
    from tracer.services.clickhouse import consistency

    # raising=False keeps the regression runnable against the unsampled checker.
    monkeypatch.setattr(
        consistency.ConsistencyChecker,
        "LAG_SAMPLE_MAX_ROWS",
        SAMPLE_BUDGET,
        raising=False,
    )
    return SAMPLE_BUDGET


def _trace_lag_reads(recorded):
    return [
        entry
        for entry in recorded
        if "max(_peerdb_synced_at)" in entry["sql"]
        and "FROM tracer_trace" in entry["sql"]
    ]


def _assert_current_and_healthy(checker, recorded, budget, events):
    health = checker.get_health_status()
    detail = {"health": health, "statements": recorded}
    assert health.clickhouse_connected is True, detail
    assert health.details["checks"] == {
        f"legacy.{table}": "ready" for table in CDC_TABLES
    }, detail
    assert health.status == "healthy", detail
    assert set(health.cdc_lag) == set(CDC_TABLES), detail
    assert all(0 <= lag < 60 for lag in health.cdc_lag.values()), detail
    reads = _trace_lag_reads(recorded)
    assert reads and all(entry["error"] is None for entry in reads), detail
    assert all(entry["rows"] <= budget for entry in reads), detail
    assert not [e for e in events if e["event"] == "ClickHouse read query failed"]
    result = health_tests.response()
    assert result.status_code == 200
    assert result.data["status"] == "healthy", result.data
    assert all(lag >= 0 for lag in result.data["cdc_lag"].values()), result.data


@pytest.mark.integration
def test_lag_read_does_not_grow_with_table_size(
    configure, large_mirror, statements, small_budget
):
    """The lag statement reads a bounded sample, not the whole mirror."""
    admin, database, _ = large_mirror
    (total,) = admin.execute("SELECT count() FROM tracer_trace")[0]
    if total < 50 * small_budget:
        pytest.fail(f"precondition: {total} rows is not far above the budget")
    _sync_now(admin)
    checker = configure(_probe_client(admin, database))
    with capture_logs() as events:
        _assert_current_and_healthy(checker, statements, small_budget, events)


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["readonly2", "server_readonly1"])
def test_large_current_mirror_is_healthy_when_whole_scans_are_refused(
    configure, large_mirror, statements, small_budget, profile
):
    """A server that refuses whole-table scans still sees a healthy mirror."""
    from clickhouse_driver import Client
    from clickhouse_driver.errors import ServerException

    admin, database, users = large_mirror
    user = users[profile]
    connection = admin.connection
    direct = Client(
        host=connection.host, port=connection.port, user=user, database=database
    )
    try:
        with pytest.raises(ServerException) as refused:
            direct.execute(AGGREGATE)
        if refused.value.code != TOO_MANY_ROWS:
            pytest.fail(f"precondition: whole scan failed with {refused.value!r}")
    finally:
        direct.disconnect()

    _sync_now(admin)
    checker = configure(
        _probe_client(
            admin, database, user, server_readonly=profile == "server_readonly1"
        )
    )
    with capture_logs() as events:
        _assert_current_and_healthy(checker, statements, small_budget, events)


@pytest.mark.integration
def test_role_without_parts_grant_keeps_the_whole_table_aggregate(
    configure, large_mirror, statements, small_budget
):
    """No system.parts grant: no denied statement, the exact aggregate instead."""
    admin, database, users = large_mirror
    _sync_now(admin)
    checker = configure(
        _probe_client(admin, database, users["no_parts_grant"], server_readonly=True)
    )
    with capture_logs() as events:
        health = checker.get_health_status()
    assert health.status == "healthy", (health, statements)
    assert all(0 <= lag < 60 for lag in health.cdc_lag.values()), health
    assert not [s for s in statements if "FROM system.parts" in s["sql"]]
    assert not [s for s in statements if s["error"] is not None], statements
    assert [s["sql"] for s in _trace_lag_reads(statements)] == [AGGREGATE]
    assert not [e for e in events if e["event"] == "ClickHouse read query failed"]


@pytest.mark.integration
def test_sampled_lag_still_reports_a_stalled_mirror(configure, statements):
    """Sampling must never make old data look current."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_lag_stale_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        # The newest sync anywhere is ten minutes old.
        _fill(admin, 50_000, synced_age_seconds=600)
        for table in ("trace_session", "tracer_eval_logger"):
            admin.execute(
                f"INSERT INTO {table} (id, created_at, updated_at, "
                "_peerdb_synced_at, _peerdb_is_deleted, _peerdb_version) "
                "SELECT generateUUIDv4(), now64(3), now64(3), "
                "now64(6) - toIntervalSecond(600), 0, 1"
            )
        client = _probe_client(admin, database)
        try:
            health = configure(client).get_health_status()
        finally:
            client.close()
    assert health.status == "degraded", (health, statements)
    assert set(health.details["checks"].values()) == {"lagging"}
    assert all(lag >= 590 for lag in health.cdc_lag.values()), health.cdc_lag
    assert all("WHERE _part IN" in s["sql"] for s in _trace_lag_reads(statements))
