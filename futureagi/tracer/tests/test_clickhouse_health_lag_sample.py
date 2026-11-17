"""Cloud-only synthetic coverage and physical-read-bound regressions.

Production CDC DDL is retained except for replication. Exact lag requires full
immutable part coverage and a stable post-read inventory. Oversized/truncated
inventories are healthy but explicitly unverified; missing or denied metadata
is actionable and degrades health regardless of table size. No physical bound
relies on SQL LIMIT, part age, prefix granularity, or per-query settings in a
readonly=1 profile.
ClickHouse progress counters are the runtime oracle, including concurrent growth
at the production ten-million-row budget and sparse lightweight-delete masks.
"""

import re
import time
from contextlib import ExitStack

import pytest
from structlog.testing import capture_logs

from tracer.tests import test_clickhouse_consistency as health_tests

configure = health_tests.configure
real_clients = health_tests.real_clients

CDC_TABLES = ("tracer_trace", "trace_session", "tracer_eval_logger")
_DDL = {
    "tracer_trace": "CDC_TRACE",
    "trace_session": "CDC_TRACE_SESSION",
    "tracer_eval_logger": "CDC_EVAL_LOGGER",
}
LARGE_ROWS = 2_000_000
# Far below the table and every bulk part, far above a fresh CDC batch.
SAMPLE_BUDGET = 20_000
# A smaller stale part beside an oversized current part: its maximum alone
# cannot establish either exact lag or staleness.
LARGE_FITTING_ROWS = 15_000
# Server-side scan limit for the probing users: room for the metadata reads and
# the sample, an order of magnitude short of the whole table.
SERVER_ROW_LIMIT = 200_000
PART_COLUMNS = "database, table, name, rows, active"
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
            finally:
                progress = getattr(
                    getattr(driver, "last_query", None), "progress", None
                )
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


def _assert_large_unverified_and_healthy(checker, recorded, events):
    health = checker.get_health_status()
    detail = {"health": health, "statements": recorded}
    assert health.clickhouse_connected is True, detail
    assert health.details["checks"] == {
        "legacy.tracer_trace": "unverified",
        "legacy.trace_session": "ready",
        "legacy.tracer_eval_logger": "ready",
    }, detail
    assert health.status == "healthy", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    assert health.cdc_lag_verified == {
        "tracer_trace": False,
        "trace_session": True,
        "tracer_eval_logger": True,
    }, detail
    assert not _trace_lag_reads(recorded), detail
    assert not [e for e in events if e["event"] == "ClickHouse read query failed"]
    result = health_tests.response()
    assert result.status_code == 200
    assert result.data["status"] == "healthy", result.data
    assert result.data["cdc_lag"]["tracer_trace"] == -1, result.data
    assert result.data["cdc_lag_verified"]["tracer_trace"] is False, result.data


@pytest.mark.integration
def test_lag_read_does_not_grow_with_table_size(
    configure, large_mirror, statements, small_budget
):
    """The oversized mirror is skipped, never scanned just to sample a timestamp."""
    admin, database, _ = large_mirror
    (total,) = admin.execute("SELECT count() FROM tracer_trace")[0]
    if total < 50 * small_budget:
        pytest.fail(f"precondition: {total} rows is not far above the budget")
    _sync_now(admin)
    checker = configure(_probe_client(admin, database))
    with capture_logs() as events:
        _assert_large_unverified_and_healthy(checker, statements, events)


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
        _assert_large_unverified_and_healthy(checker, statements, events)


@pytest.mark.integration
@pytest.mark.parametrize("profile", NO_PART_METADATA)
def test_role_without_part_metadata_never_scans_a_large_mirror(
    configure, large_mirror, statements, small_budget, profile
):
    """No readable part metadata: bounded reads and unknown lag, not unhealthy.

    The server refuses these users' whole-table scans, as a statement cap does
    on a mirror large enough. The probe used to send that scan for every mirror
    in one statement, so the refusal made all of them unavailable and the
    endpoint unhealthy. Now every mirror has unknown lag and an actionable
    metadata failure (degraded), and no lag data statement is attempted.
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
        "legacy.trace_session": "unsampled",
        "legacy.tracer_eval_logger": "unsampled",
    }, detail
    assert health.cdc_lag == dict.fromkeys(CDC_TABLES, -1), detail
    assert health.cdc_lag_verified == dict.fromkeys(CDC_TABLES, False), detail
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
    assert set(result.data) == {
        "status",
        "clickhouse_connected",
        "cdc_lag",
        "cdc_lag_verified",
        "routing",
    }
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
        "legacy.tracer_trace": "unsampled",
        "legacy.trace_session": "missing_required_table",
        "legacy.tracer_eval_logger": "unsampled",
    }, detail
    assert health.cdc_lag["trace_session"] == -1, detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    assert health.cdc_lag_verified["tracer_trace"] is False, detail
    assert not [entry for entry in statements if entry["error"] is not None], detail


@pytest.mark.integration
@pytest.mark.parametrize(
    "profile", ["no_parts_grant", "parts_columns_only", "server_readonly1"]
)
@pytest.mark.parametrize("synced_age_seconds", [0, 600], ids=["fresh", "stale"])
def test_small_mirror_metadata_grants_control_lag_verification(
    configure, statements, profile, synced_age_seconds
):
    """Even a 100-row stale mirror needs readable parts before any lag scan."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with ExitStack() as stack:
        database = stack.enter_context(_ch_test_owned_database("test_health_small_"))
        admin = stack.enter_context(_ch_test_native_client(database=database))
        _create_mirrors(admin)
        for table in CDC_TABLES:
            _insert_part(admin, table, 100, synced_age_seconds=synced_age_seconds)
            assert sum(part[2] for part in _active_parts(admin, table)) == 100
        users = _probe_users(stack, admin, database)
        client = _probe_client(admin, database, users[profile], server_readonly=True)
        stack.callback(client.close)
        health = configure(client).get_health_status()

    detail = {"health": health, "statements": statements}
    reads = [entry for entry in statements if "max(_peerdb_synced_at)" in entry["sql"]]
    readable = profile == "server_readonly1"
    assert health.clickhouse_connected is True, detail
    assert health.status == (
        "healthy" if readable and synced_age_seconds == 0 else "degraded"
    ), detail
    assert health.cdc_lag_verified == dict.fromkeys(CDC_TABLES, readable), detail
    if readable:
        state = "lagging" if synced_age_seconds else "ready"
        assert set(health.details["checks"].values()) == {state}, detail
        if synced_age_seconds:
            assert all(lag >= 590 for lag in health.cdc_lag.values()), detail
        else:
            assert all(0 <= lag < 60 for lag in health.cdc_lag.values()), detail
        assert len(reads) == 1 and reads[0]["error"] is None, detail
    else:
        assert set(health.details["checks"].values()) == {"unsampled"}, detail
        assert health.cdc_lag == dict.fromkeys(CDC_TABLES, -1), detail
        assert not reads, detail


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
        _settle(admin)
        client = _probe_client(admin, database)
        try:
            health = configure(client).get_health_status()
        finally:
            client.close()
    assert health.status == "degraded", (health, statements)
    assert set(health.details["checks"].values()) == {"lagging"}
    assert all(lag >= 590 for lag in health.cdc_lag.values()), health.cdc_lag
    reads = _trace_lag_reads(statements)
    assert reads and all(
        "_part IN" in s["sql"] and "LIMIT" not in s["sql"] for s in reads
    ), statements
    assert health.cdc_lag_verified == dict.fromkeys(CDC_TABLES, True)


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

    Reading only the small stale parts would falsely report lag. Reading a
    fresh prefix would not establish the exact maximum. Skip the data read and
    explicitly report unverified without degrading health.
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
        _assert_large_unverified_and_healthy(checker, statements, events)


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
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    assert health.cdc_lag_verified["tracer_trace"] is False, detail


# Sort-key order puts every row of the first project before the second's.
EARLY_PROJECT = "00000000-0000-0000-0000-000000000001"
LATE_PROJECT = "ffffffff-ffff-ffff-ffff-fffffffffff1"


def _insert_project_rows(admin, rows, *, project, synced_age_seconds=0):
    """One INSERT into tracer_trace for one project: one new part."""
    admin.execute(
        "INSERT INTO tracer_trace (id, project_id, created_at, updated_at, "
        "_peerdb_synced_at, _peerdb_is_deleted, _peerdb_version) "
        "SELECT generateUUIDv4(number), toUUID(%(project)s), now64(3), now64(3), "
        "toDateTime64(now64(6) - toIntervalSecond(%(age)s), 6), "
        "0, toInt64(number) + 1 FROM numbers(%(rows)s)",
        {"project": project, "age": synced_age_seconds, "rows": rows},
    )


def _merge_partition(admin, partition):
    admin.execute("SYSTEM START MERGES tracer_trace")
    try:
        admin.execute(
            "OPTIMIZE TABLE tracer_trace PARTITION ID %(partition)s FINAL",
            {"partition": partition},
        )
    finally:
        admin.execute("SYSTEM STOP MERGES tracer_trace")


def _probe_health(configure, admin, database):
    client = _probe_client(admin, database)
    try:
        return configure(client).get_health_status()
    finally:
        client.close()


@pytest.mark.integration
def test_current_mirror_after_a_large_merge_has_unknown_lag(
    configure, statements, small_budget
):
    """A merge rewrites old rows into a part newer than any sync it holds.

    The fresh batch sorts after the old rows, so the bounded prefix of the
    merged part would see only old syncs. Without complete coverage the check
    can prove neither freshness nor lag. Return unverified without degradation.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_merge_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        _insert_project_rows(
            admin, 3 * small_budget, project=EARLY_PROJECT, synced_age_seconds=600
        )
        _insert_project_rows(admin, 10, project=LATE_PROJECT)
        admin.execute("OPTIMIZE TABLE tracer_trace FINAL")
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        parts = _active_parts(admin)
        assert len(parts) == 1, parts
        assert int(parts[0][0].split("_")[3]) > 0, parts  # a merged part
        assert parts[0][2] > small_budget, parts
        assert _newest_sync_age(admin) < 60
        health = _probe_health(configure, admin, database)
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    reads = _trace_lag_reads(statements)
    assert not reads, detail


@pytest.mark.integration
def test_stale_oversized_insert_has_unknown_lag(configure, statements, small_budget):
    """One inserted batch may contain mixed historical timestamps; its prefix
    cannot stand for every row, even when this fixture happens to be stale."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_stale_insert_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        _insert_project_rows(
            admin, 3 * small_budget, project=EARLY_PROJECT, synced_age_seconds=600
        )
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        parts = _active_parts(admin)
        assert len(parts) == 1, parts
        _partition, low, high, *_level = parts[0][0].split("_")
        assert low == high, parts  # one insert: one block number, whatever its level
        health = _probe_health(configure, admin, database)
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail


@pytest.mark.integration
@pytest.mark.parametrize("fresh_first", [False, True])
def test_historical_repair_merge_uses_source_timestamps(
    configure, statements, small_budget, fresh_first
):
    """Recent physical inserts/merges cannot verify an incomplete maximum.

    Snapshot/reconcile preserve old PG change times rather than the wall time
    of this INSERT. Its recent inactive source must not make stale data healthy.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_repair_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        admin.execute("SYSTEM STOP MERGES tracer_trace")
        _insert_project_rows(
            admin, 3 * small_budget, project=LATE_PROJECT, synced_age_seconds=600
        )
        _insert_project_rows(
            admin,
            10,
            project=EARLY_PROJECT,
            synced_age_seconds=0 if fresh_first else 600,
        )
        partition = _active_parts(admin)[0][1]
        _merge_partition(admin, partition)
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        parts = _active_parts(admin)
        assert len(parts) == 1 and parts[0][2] > small_budget, parts
        # Real part metadata is fresh in both cases, while source syncs differ.
        youngest = admin.execute(
            "SELECT min(dateDiff('second', modification_time, now())) "
            "FROM system.parts WHERE database = currentDatabase() "
            "AND table = 'tracer_trace' AND NOT active"
        )[0][0]
        assert youngest < 60
        source_age = _newest_sync_age(admin)
        if fresh_first:
            assert source_age < 60
        else:
            assert source_age >= 590
        health = _probe_health(configure, admin, database)
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    assert health.cdc_lag_verified["tracer_trace"] is False, detail
    assert not _trace_lag_reads(statements), detail
    assert not any("NOT active" in entry["sql"] for entry in statements), detail


@pytest.mark.integration
def test_newer_stale_partition_does_not_report_false_lag(
    configure, statements, small_budget
):
    """A small repair in an old month is newer than a large current month's merge.

    The newest physical part has only stale syncs; the unselected larger part
    has a fresh sync at its tail. The partial sample cannot establish lag.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_partitions_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        admin.execute("SYSTEM STOP MERGES tracer_trace")
        _insert_project_rows(
            admin, 3 * small_budget, project=EARLY_PROJECT, synced_age_seconds=600
        )
        _insert_project_rows(admin, 10, project=LATE_PROJECT)
        fresh_partition = _active_parts(admin)[0][1]
        _merge_partition(admin, fresh_partition)
        time.sleep(1.1)  # system.parts modification_time has one-second resolution.
        for _ in range(2):
            _insert_part(
                admin, "tracer_trace", 50, months_back=1, synced_age_seconds=600
            )
        old_partition = next(
            partition
            for _, partition, _ in _active_parts(admin)
            if partition != fresh_partition
        )
        _merge_partition(admin, old_partition)
        for table in ("trace_session", "tracer_eval_logger"):
            _insert_part(admin, table, 1)
        inventory = admin.execute(
            "SELECT partition_id, rows, modification_time FROM system.parts "
            "WHERE database = currentDatabase() AND table = 'tracer_trace' "
            "AND active ORDER BY modification_time DESC"
        )
        assert len(inventory) == 2, inventory
        assert inventory[0][0] == old_partition and inventory[0][1] == 100, inventory
        assert inventory[0][2] > inventory[1][2], inventory
        assert inventory[1][1] > small_budget, inventory
        assert _newest_sync_age(admin) < 60
        health = _probe_health(configure, admin, database)
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    reads = _trace_lag_reads(statements)
    assert not reads, detail


@pytest.mark.integration
def test_native_eval_repair_merge_does_not_invent_a_fresh_sync(
    configure, real_clients, statements, small_budget
):
    """The default outbox native eval mirror retains historical sync timestamps."""
    _, native, clients = real_clients
    health_tests.create_native(native)
    native.execute(_production_ddl("tracer_eval_logger"))
    native.execute("SYSTEM STOP MERGES tracer_eval_logger")
    for rows, trace in ((3 * small_budget, EARLY_PROJECT), (10, LATE_PROJECT)):
        native.execute(
            "INSERT INTO tracer_eval_logger "
            "(id, trace_id, created_at, updated_at, _peerdb_synced_at, _peerdb_version) "
            "SELECT generateUUIDv4(number), toUUID(%(trace)s), now64(6), now64(6), "
            "now64(6) - toIntervalSecond(%(age)s), toInt64(number) + 1 "
            "FROM numbers(%(rows)s)",
            {"trace": trace, "age": 600, "rows": rows},
        )
    native.execute("SYSTEM START MERGES tracer_eval_logger")
    native.execute("OPTIMIZE TABLE tracer_eval_logger FINAL")
    parts = _active_parts(native, "tracer_eval_logger")
    assert len(parts) == 1 and parts[0][2] > small_budget, parts
    assert _newest_sync_age(native, "tracer_eval_logger") >= 590
    health = configure(*clients, retired=True).get_health_status()
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["native.tracer_eval_logger"] == "unverified", detail
    assert health.cdc_lag == {"tracer_eval_logger": -1}, detail
    reads = [entry for entry in statements if "max(_peerdb_synced_at)" in entry["sql"]]
    assert not reads, detail


@pytest.mark.integration
def test_single_insert_with_mixed_source_timestamps_has_unknown_lag(
    configure, statements, small_budget
):
    """One historical batch is not homogeneous: its fresh tail is outside the prefix."""
    from conftest import _ch_test_native_client, _ch_test_owned_database

    with (
        _ch_test_owned_database("test_health_mixed_batch_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        admin.execute(
            "INSERT INTO tracer_trace "
            "(id, project_id, created_at, updated_at, _peerdb_synced_at, _peerdb_version) "
            "SELECT generateUUIDv4(number), "
            "toUUID(if(number < %(stale_rows)s, %(early)s, %(late)s)), "
            "now64(3), now64(3), "
            "now64(6) - toIntervalSecond(if(number < %(stale_rows)s, 600, 0)), "
            "toInt64(number) + 1 FROM numbers(%(rows)s)",
            {
                "stale_rows": 3 * small_budget,
                "rows": 3 * small_budget + 10,
                "early": EARLY_PROJECT,
                "late": LATE_PROJECT,
            },
        )
        parts = _active_parts(admin)
        assert len(parts) == 1, parts
        _, low, high, *_ = parts[0][0].split("_")
        assert low == high, parts
        assert _newest_sync_age(admin) < 60
        health = _probe_health(configure, admin, database)
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    reads = _trace_lag_reads(statements)
    assert not reads, detail


@pytest.mark.integration
def test_growth_and_merge_after_inventory_do_not_prove_stale_coverage(
    configure, statements, small_budget, monkeypatch
):
    """A table sized as small grows and merges before the bounded data statement.

    The snapshot must be fully read before stale rows can establish lag; old
    inventory counts cannot establish coverage of the new merged part.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database
    from tracer.services.clickhouse import consistency

    with (
        _ch_test_owned_database("test_health_growth_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        _create_mirrors(admin)
        admin.execute("SYSTEM STOP MERGES tracer_trace")
        _insert_project_rows(admin, 10, project=LATE_PROJECT)
        original_read = consistency.ConsistencyChecker._read
        changed = []

        def grow_after_inventory(self, *args, **kwargs):
            rows = original_read(self, *args, **kwargs)
            if "FROM system.parts" in args[1] and not changed:
                _insert_project_rows(
                    admin,
                    3 * small_budget,
                    project=EARLY_PROJECT,
                    synced_age_seconds=600,
                )
                _merge_partition(admin, _active_parts(admin)[0][1])
                changed.extend(_active_parts(admin))
            return rows

        monkeypatch.setattr(
            consistency.ConsistencyChecker, "_read", grow_after_inventory
        )
        health = _probe_health(configure, admin, database)
        assert len(changed) == 1 and changed[0][2] > small_budget, changed
        assert _newest_sync_age(admin) < 60
    detail = (health, statements)
    assert health.status == "healthy", detail
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified", detail
    assert health.cdc_lag["tracer_trace"] == -1, detail
    reads = _trace_lag_reads(statements)
    assert reads and all(
        "_part IN" in entry["sql"] and "LIMIT" not in entry["sql"] for entry in reads
    ), detail
    assert all(entry["rows"] <= small_budget for entry in reads), detail


def _sparsely_mask_trace(admin):
    """The production ReplacingMergeTree DDL permits lightweight deletes.

    Keep one row per 100,000 versions. A LIMIT on surviving rows would no
    longer bound the physical rows traversed to find them.
    """
    admin.execute("SYSTEM START MERGES tracer_trace")
    try:
        admin.execute(
            "DELETE FROM tracer_trace WHERE _peerdb_version % 100000 != 0",
            settings={"mutations_sync": 2, "allow_experimental_lightweight_delete": 1},
        )
    finally:
        admin.execute("SYSTEM STOP MERGES tracer_trace")


@pytest.mark.integration
@pytest.mark.parametrize(
    "metadata,sparse",
    [
        ("select", False),
        ("select", True),
        ("hidden", False),
        ("denied", True),
        ("sizing_denied", False),
    ],
)
def test_production_budget_growth_readonly1_bounds_all_physical_lag_reads(
    configure, statements, monkeypatch, metadata, sparse, record_property
):
    """Inventory below 10M, then multiple parts above 10M before the data read.

    No per-query row/block/thread setting is available to the probing user.
    The sparse variant exercises real lightweight deletes, not logical CDC
    tombstones. Sizing denial is explicitly injected because system.tables is
    implicitly visible on stock ClickHouse; the other grants are real roles.
    """
    from clickhouse_driver.errors import ServerException

    from conftest import _ch_test_native_client, _ch_test_owned_database
    from tracer.services.clickhouse import consistency

    checker_type = consistency.ConsistencyChecker
    assert checker_type.LAG_SAMPLE_MAX_ROWS == 10_000_000
    monkeypatch.setattr(
        checker_type, "MONITORED_TABLES", [("tracer_trace", "tracer_trace")]
    )
    # Only the synthetic writer's insert/delete time is excluded from the
    # diagnostic clock. Actual diagnostic I/O still shares the unchanged wall.
    paused = [0.0]
    monkeypatch.setattr(consistency, "monotonic", lambda: time.monotonic() - paused[0])
    with ExitStack() as stack:
        database = stack.enter_context(_ch_test_owned_database("test_health_physical_"))
        admin = stack.enter_context(_ch_test_native_client(database=database))
        admin.execute(_production_ddl("tracer_trace"))
        admin.execute("SYSTEM STOP MERGES tracer_trace")
        initial_rows = 100_000
        _insert_part(admin, "tracer_trace", initial_rows, synced_age_seconds=600)
        before = _active_parts(admin)
        assert sum(row[2] for row in before) == initial_rows
        user = f"{database}_ro"
        stack.callback(admin.execute, f"DROP USER IF EXISTS {user}")
        admin.execute(
            f"CREATE USER {user} IDENTIFIED WITH no_password SETTINGS readonly = 1, "
            "max_block_size = 100000, max_threads = 8, max_rows_to_read = 0"
        )
        admin.execute(f"GRANT SELECT ON {database}.* TO {user}")
        admin.execute(f"GRANT SELECT(name, value) ON system.settings TO {user}")
        if metadata == "select":
            admin.execute(f"GRANT SELECT({PART_COLUMNS}) ON system.parts TO {user}")
        elif metadata == "denied":
            admin.execute(f"GRANT SHOW COLUMNS ON system.parts TO {user}")
        client = _probe_client(admin, database, user, server_readonly=True)
        stack.callback(client.close)
        # Verify the actual server profile, rather than assuming client flags
        # reproduce readonly=1 or the default 100k-row block behavior.
        profile = dict(
            client.execute(
                "SELECT name, value FROM system.settings "
                "WHERE name IN ('readonly', 'max_block_size', 'max_threads')"
            )
        )
        assert profile == {
            "readonly": "1",
            "max_block_size": "100000",
            "max_threads": "8",
        }
        read = checker_type._read
        grown = []
        sizing_denied = []

        def grow_after_sizing(self, probe, sql, params=None, **kwargs):
            trigger = (
                "FROM system.parts" if metadata == "select" else "FROM system.tables"
            )
            rows = read(self, probe, sql, params, **kwargs)
            if trigger in sql and not grown:
                started = time.monotonic()
                try:
                    # Separate monthly partitions cannot merge together. Each
                    # new part is below the budget; their total exceeds it.
                    for months_back in (1, 2, 3):
                        _insert_part(
                            admin, "tracer_trace", 4_000_000, months_back=months_back
                        )
                    if sparse:
                        _sparsely_mask_trace(admin)
                    grown.extend(_active_parts(admin))
                finally:
                    paused[0] += time.monotonic() - started
            if metadata == "sizing_denied" and "FROM system.tables" in sql:
                sizing_denied.append(True)
                raise ServerException("synthetic size metadata denial", code=497)
            return rows

        monkeypatch.setattr(checker_type, "_read", grow_after_sizing)
        health = configure(client).get_health_status()
        assert len(grown) >= 4, grown
        physical_rows = sum(row[2] for row in grown)
        assert physical_rows > checker_type.LAG_SAMPLE_MAX_ROWS, grown
        if sparse:
            visible = admin.execute("SELECT count() FROM tracer_trace")[0][0]
            assert 0 < visible < physical_rows // 1000, (visible, grown)
        assert bool(sizing_denied) is (metadata == "sizing_denied")

    reads = _trace_lag_reads(statements)
    detail = {
        "health": health,
        "statements": statements,
        "before": before,
        "grown": grown,
    }
    assert all(entry["rows"] is not None for entry in reads), detail
    # Aggregate *all* attempts, not just the maximum returned-row LIMIT. With
    # only one monitored table, native progress belongs entirely to that table.
    total_read = sum(entry["rows"] for entry in reads)
    record_property("lag_physical_rows", total_read)
    record_property("lag_row_budget", checker_type.LAG_SAMPLE_MAX_ROWS)
    record_property("physical_table_rows_after_growth", physical_rows)
    assert total_read <= checker_type.LAG_SAMPLE_MAX_ROWS, detail
    assert total_read <= initial_rows, detail
    assert all(entry["error"] is None for entry in reads), detail
    assert health.cdc_lag == {"tracer_trace": -1}, detail
    assert health.cdc_lag_verified == {"tracer_trace": False}, detail
    assert health.status == ("healthy" if metadata == "select" else "degraded"), detail
    assert health.details["checks"]["legacy.tracer_trace"] == (
        "unverified" if metadata == "select" else "unsampled"
    ), detail
    if metadata == "select":
        assert len(reads) == 1, detail
        assert "_part IN" in reads[0]["sql"] and "LIMIT" not in reads[0]["sql"], detail
    else:
        assert not reads, detail


@pytest.mark.integration
@pytest.mark.parametrize("masked", [False, True])
def test_readonly1_complete_parts_bound_physical_rows_with_sparse_deletes(
    configure, statements, monkeypatch, masked, record_property
):
    """Within-budget immutable parts: masks do not trigger an extra attempt."""
    from conftest import _ch_test_native_client, _ch_test_owned_database
    from tracer.services.clickhouse import consistency

    checker_type = consistency.ConsistencyChecker
    assert checker_type.LAG_SAMPLE_MAX_ROWS == 10_000_000
    monkeypatch.setattr(
        checker_type, "MONITORED_TABLES", [("tracer_trace", "tracer_trace")]
    )
    with ExitStack() as stack:
        database = stack.enter_context(_ch_test_owned_database("test_health_masked_"))
        admin = stack.enter_context(_ch_test_native_client(database=database))
        admin.execute(_production_ddl("tracer_trace"))
        admin.execute("SYSTEM STOP MERGES tracer_trace")
        for month in (1, 2, 3):
            _insert_part(admin, "tracer_trace", 200_000, months_back=month)
        if masked:
            _sparsely_mask_trace(admin)
        physical_rows = sum(row[2] for row in _active_parts(admin))
        assert physical_rows == 600_000
        visible = admin.execute("SELECT count() FROM tracer_trace")[0][0]
        assert visible == (6 if masked else physical_rows)
        user = f"{database}_ro"
        stack.callback(admin.execute, f"DROP USER IF EXISTS {user}")
        admin.execute(
            f"CREATE USER {user} IDENTIFIED WITH no_password SETTINGS readonly = 1, "
            "max_block_size = 100000, max_threads = 8, max_rows_to_read = 0"
        )
        admin.execute(f"GRANT SELECT ON {database}.* TO {user}")
        admin.execute(f"GRANT SELECT({PART_COLUMNS}) ON system.parts TO {user}")
        client = _probe_client(admin, database, user, server_readonly=True)
        stack.callback(client.close)
        health = configure(client).get_health_status()
    reads = _trace_lag_reads(statements)
    detail = {"health": health, "statements": statements}
    assert len(reads) == 1 and reads[0]["error"] is None, detail
    assert reads[0]["rows"] is not None, detail
    total_read = sum(entry["rows"] for entry in reads)
    record_property("lag_physical_rows", total_read)
    record_property("physical_part_rows", physical_rows)
    assert 0 < total_read <= physical_rows <= checker_type.LAG_SAMPLE_MAX_ROWS, detail
    assert health.status == "healthy", detail
    assert health.cdc_lag_verified["tracer_trace"] is (not masked), detail
    if masked:
        assert health.cdc_lag["tracer_trace"] == -1, detail
    else:
        assert 0 <= health.cdc_lag["tracer_trace"] < 60, detail
