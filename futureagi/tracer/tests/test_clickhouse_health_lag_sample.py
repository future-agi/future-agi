"""A large but current CDC mirror must not look unhealthy because it is large.

Health statements are capped at about one second. ``_peerdb_synced_at`` is in
no sorting or partition key of the CDC mirrors, so a whole-table
``max(_peerdb_synced_at)`` reads every row. On mirrors of a few hundred million
rows that scan outgrew the cap and the endpoint reported ``unhealthy`` with lag
-1 while CDC was current. The lag probe now reads only the newest parts that fit
a fixed row budget, plus a bounded prefix of the newest part when it alone is
larger than the budget. It selects parts by partition and block range, so a
merge that commits between the part inventory and the sample still selects the
merged part.

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
configs require a grant for (``select_from_system_db_requires_grant``). A
readonly=1 profile also accepts no per-query limit, so for a role without that
grant the probe bounds itself: it reads a mirror in full only when
``system.tables`` shows it within the budget, and reports a larger mirror's lag
as unknown (degraded) instead of scanning it.
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
# A part that fits the budget beside a planned prefix, but not beside the whole
# granule (8192 rows) ClickHouse reads for that prefix.
LARGE_FITTING_ROWS = 15_000
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


def _insert_part(admin, table, rows, *, months_back=0, synced_age_seconds=0):
    """One INSERT ... SELECT: one new part in one monthly partition."""
    created = (
        "toDateTime64(toStartOfMonth(now64(3)) "
        f"- toIntervalMonth({int(months_back)}) + toIntervalDay(3), 3)"
        if months_back
        else "now64(3)"
    )
    admin.execute(
        f"INSERT INTO {table} (id, created_at, updated_at, _peerdb_synced_at, "
        "_peerdb_is_deleted, _peerdb_version) "
        f"SELECT generateUUIDv4(number), {created}, {created}, "
        f"toDateTime64(now64(6) - toIntervalSecond({int(synced_age_seconds)}), 6), "
        f"0, toInt64(number) + 1 FROM numbers({int(rows)})"
    )


def _active_parts(admin, table="tracer_trace"):
    return admin.execute(
        "SELECT name, partition_id, rows FROM system.parts "
        "WHERE database = currentDatabase() AND table = %(table)s AND active "
        "ORDER BY name",
        {"table": table},
    )


def _newest_sync_age(admin, table="tracer_trace"):
    return admin.execute(
        f"SELECT dateDiff('second', max(_peerdb_synced_at), now64(6)) FROM {table}"
    )[0][0]


# profile: (user suffix, server-enforced readonly=1, system.parts access).
# Without readonly=1 the client sends readonly=2 and its per-query limits.
PROFILES = {
    "readonly2": ("rw", False, "select"),
    "server_readonly1": ("ro", True, "select"),
    "no_parts_grant": ("np", True, None),
    "no_parts_grant_readonly2": ("np2", False, None),
    # SHOW COLUMNS lists the system.parts columns but grants no SELECT.
    "parts_columns_only": ("pc", True, "show_columns"),
}
NO_PART_METADATA = sorted(
    profile for profile, (_, _, parts) in PROFILES.items() if parts != "select"
)


def _probe_users(stack, admin, database):
    """Probing users per profile; the server refuses their whole-table scans."""
    users = {}
    for profile, (suffix, server_readonly, parts) in PROFILES.items():
        user = users[profile] = f"{database}_{suffix}"
        readonly = "readonly = 1, " if server_readonly else ""
        stack.callback(admin.execute, f"DROP USER IF EXISTS {user}")
        admin.execute(
            f"CREATE USER {user} IDENTIFIED WITH no_password "
            f"SETTINGS {readonly}max_rows_to_read = {SERVER_ROW_LIMIT}"
        )
        admin.execute(f"GRANT SELECT ON {database}.* TO {user}")
        if parts == "select":
            admin.execute(f"GRANT SELECT({PART_COLUMNS}) ON system.parts TO {user}")
        elif parts == "show_columns":
            admin.execute(f"GRANT SHOW COLUMNS ON system.parts TO {user}")
    return users


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
        yield admin, database, _probe_users(stack, admin, database)


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
@pytest.mark.parametrize("profile", NO_PART_METADATA)
def test_role_without_part_metadata_never_scans_a_large_mirror(
    configure, large_mirror, statements, small_budget, profile
):
    """No readable part metadata: bounded reads and unknown lag, not unhealthy.

    The server refuses these users' whole-table scans, as a statement cap does
    on a mirror large enough. The probe used to send that scan for every mirror
    in one statement, so the refusal made all of them unavailable and the
    endpoint unhealthy. Now the large mirror's lag is unknown (degraded), the
    small mirrors keep their exact lag, and no statement is refused.
    """
    admin, database, users = large_mirror
    (total,) = admin.execute("SELECT count() FROM tracer_trace")[0]
    if total <= SERVER_ROW_LIMIT:
        pytest.fail(f"precondition: {total} rows fit the server's row limit")
    _sync_now(admin)
    checker = configure(
        _probe_client(
            admin, database, users[profile], server_readonly=PROFILES[profile][1]
        )
    )
    with capture_logs() as events:
        health = checker.get_health_status()
        result = health_tests.response()
    detail = {"health": health, "statements": statements}

    assert health.clickhouse_connected is True, detail
    assert health.status == "degraded", detail
    assert health.details["checks"] == {
        "legacy.tracer_trace": "unsampled",
        "legacy.trace_session": "ready",
        "legacy.tracer_eval_logger": "ready",
    }, detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    assert all(
        0 <= health.cdc_lag[table] < 60
        for table in ("trace_session", "tracer_eval_logger")
    ), detail
    assert not _trace_lag_reads(statements), detail
    assert all(
        entry["rows"] <= small_budget for entry in statements if entry["error"] is None
    ), detail
    refused = [entry for entry in statements if entry["error"] is not None]
    if profile == "parts_columns_only":
        # Only the part inventory is denied (once per check), never a data read.
        assert refused, detail
        assert all(
            entry["error"] == 497 and "FROM system.parts" in entry["sql"]
            for entry in refused
        ), detail
    else:
        assert not refused, detail
        assert not [s for s in statements if "FROM system.parts" in s["sql"]]
    assert not [e for e in events if e["event"] == "CDC lag check failed"], events
    assert result.status_code == 200
    assert set(result.data) == {"status", "clickhouse_connected", "cdc_lag", "routing"}
    assert result.data["status"] == "degraded", result.data
    assert result.data["cdc_lag"]["tracer_trace"] == -1, result.data


@pytest.mark.integration
def test_role_without_part_metadata_still_reports_a_missing_mirror(
    configure, statements
):
    """Unknown lag degrades; a mirror that does not exist is still unhealthy."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_missing_") as database,
        _ch_test_native_client(database=database) as admin,
        ExitStack() as stack,
    ):
        for table in ("tracer_trace", "tracer_eval_logger"):
            admin.execute(_production_ddl(table))
        _insert_part(admin, "tracer_trace", 10)
        _insert_part(admin, "tracer_eval_logger", 10)
        user = f"{database}_np"
        stack.callback(admin.execute, f"DROP USER IF EXISTS {user}")
        admin.execute(
            f"CREATE USER {user} IDENTIFIED WITH no_password SETTINGS readonly = 1"
        )
        admin.execute(f"GRANT SELECT ON {database}.* TO {user}")
        health = configure(
            _probe_client(admin, database, user, server_readonly=True)
        ).get_health_status()
    detail = {"health": health, "statements": statements}
    assert health.status == "unhealthy", detail
    assert health.details["checks"] == {
        "legacy.tracer_trace": "ready",
        "legacy.trace_session": "missing_required_table",
        "legacy.tracer_eval_logger": "ready",
    }, detail
    assert health.cdc_lag["trace_session"] == -1, detail
    assert 0 <= health.cdc_lag["tracer_trace"] < 60, detail
    assert not [entry for entry in statements if entry["error"] is not None], detail


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
    reads = _trace_lag_reads(statements)
    assert reads and all("_partition_id" in s["sql"] for s in reads), statements


# One CDC batch three times the sample budget, and one merged month of history
# far above it (both well under the probing users' scan limit on their own).
BULK_BATCH_ROWS = 3 * SAMPLE_BUDGET
HISTORY_PART_ROWS = 150_000


@pytest.fixture(
    params=[
        "every_part_oversized",
        "next_to_stale_small_parts",
        "next_to_large_fitting_parts",
    ]
)
def oversized_batch(request):
    """The newest part is one CDC batch larger than the whole sample budget.

    ``every_part_oversized``: history is one large part per month, as after
    ``OPTIMIZE ... FINAL``, so no part fits the budget and the table is larger
    than the probing users may scan. ``next_to_stale_small_parts``: the only
    parts that fit are small ones synced ten minutes ago.
    ``next_to_large_fitting_parts``: a stale part fits beside the prefix's
    planned rows but not beside the whole granule ClickHouse reads for it.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with ExitStack() as stack:
        database = stack.enter_context(_ch_test_owned_database("test_health_bulk_"))
        admin = stack.enter_context(_ch_test_native_client(database=database))
        _create_mirrors(admin)
        for table in CDC_TABLES:
            admin.execute(f"SYSTEM STOP MERGES {table}")
        history = {
            "every_part_oversized": HISTORY_PART_ROWS,
            "next_to_stale_small_parts": 100,
            "next_to_large_fitting_parts": LARGE_FITTING_ROWS,
        }[request.param]
        for months_back in (2, 1):
            _insert_part(
                admin,
                "tracer_trace",
                history,
                months_back=months_back,
                synced_age_seconds=600,
            )
        if request.param == "next_to_large_fitting_parts":
            # system.parts has one-second modification times: write the batch
            # in a later second than the history, so it alone is newest.
            time.sleep(1.1)
        _insert_part(admin, "tracer_trace", BULK_BATCH_ROWS)
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        yield request.param, admin, database, _probe_users(stack, admin, database)


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["readonly2", "server_readonly1"])
def test_current_mirror_whose_newest_part_exceeds_the_budget_is_healthy(
    configure, oversized_batch, statements, small_budget, profile
):
    """The newest part alone is larger than the budget; CDC is current.

    Skipping every part over the budget left either no sample (and a
    whole-table scan the server refuses: unhealthy) or only the stale small
    parts (ten minutes of false lag: degraded). The newest part now contributes
    a bounded prefix of its rows.
    """
    layout, admin, database, users = oversized_batch
    parts = _active_parts(admin)
    fitting = [rows for *_, rows in parts if rows <= small_budget]
    assert len(parts) == 3, parts
    if layout == "every_part_oversized":
        assert not fitting, parts
        (total,) = admin.execute("SELECT count() FROM tracer_trace")[0]
        assert total > SERVER_ROW_LIMIT, total
    elif layout == "next_to_large_fitting_parts":
        assert fitting == [LARGE_FITTING_ROWS] * 2, parts
    else:
        assert fitting == [100, 100], parts
    assert _newest_sync_age(admin) < 60

    checker = configure(
        _probe_client(
            admin,
            database,
            users[profile],
            server_readonly=profile == "server_readonly1",
        )
    )
    with capture_logs() as events:
        _assert_current_and_healthy(checker, statements, small_budget, events)


@pytest.fixture
def race_layout():
    """tracer_trace: one small part synced ten minutes ago in each of two earlier
    months, and the latest CDC batch, two small parts synced now, in this month.
    Merges are stopped before any insert, so the layout is deterministic."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_race_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        for table in CDC_TABLES:
            admin.execute(f"SYSTEM STOP MERGES {table}")
        for months_back in (2, 1):
            _insert_part(
                admin,
                "tracer_trace",
                100,
                months_back=months_back,
                synced_age_seconds=600,
            )
        stale = {name for name, *_ in _active_parts(admin)}
        _insert_part(admin, "tracer_trace", 5)
        _insert_part(admin, "tracer_trace", 5)
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        parts = _active_parts(admin)
        fresh = [name for name, *_ in parts if name not in stale]
        partitions = {partition for name, partition, _ in parts if name in fresh}
        assert len(parts) == 4 and len(fresh) == 2 and len(partitions) == 1, parts
        assert _newest_sync_age(admin) < 60
        yield admin, database, fresh, partitions.pop()


@pytest.mark.integration
def test_merge_between_inventory_and_sample_keeps_current_mirror_healthy(
    configure, race_layout, monkeypatch
):
    """A merge commits between the part inventory and the sample read.

    The merged part has a new name. A sample that selected the listed names read
    only the two stale parts, and a current mirror reported ten minutes of lag.
    """
    from tracer.services.clickhouse import consistency

    admin, database, fresh, partition = race_layout
    read = consistency.ConsistencyChecker._read
    merged = {}

    def read_then_merge(self, *args, **kwargs):
        rows = read(self, *args, **kwargs)
        if "FROM system.parts" in args[1] and not merged:
            admin.execute("SYSTEM START MERGES tracer_trace")
            try:
                admin.execute(
                    f"OPTIMIZE TABLE tracer_trace PARTITION ID '{partition}' FINAL",
                    settings={"optimize_throw_if_noop": 1},
                )
            finally:
                admin.execute("SYSTEM STOP MERGES tracer_trace")
            merged["active"] = sorted(name for name, *_ in _active_parts(admin))
        return rows

    monkeypatch.setattr(consistency.ConsistencyChecker, "_read", read_then_merge)
    health = configure(_probe_client(admin, database)).get_health_status()

    assert merged, "the merge did not run between inventory and sample"
    # The listed fresh parts are gone; the data is unchanged and current.
    assert not set(fresh) & set(merged["active"]), (fresh, merged)
    assert _newest_sync_age(admin) < 60
    detail = {"health": health, "fresh": fresh, "merged": merged}
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "ready", detail
    assert 0 <= health.cdc_lag["tracer_trace"] < 60, detail
