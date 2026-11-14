"""Health regressions for retained readers, verified sidecars and stalled queries."""

import queue
import socket
import threading
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from tracer.services.clickhouse.application_read_policy import application_read_context
from tracer.tests import test_clickhouse_consistency as health_tests

configure = health_tests.configure
real_clients = health_tests.real_clients


@pytest.mark.unit
@pytest.mark.parametrize("native_eval", [False, True])
def test_retirement_still_reports_required_legacy_outage(
    configure, settings, native_eval
):
    legacy = health_tests.fake_client({})
    eval_table = "tracer_eval_logger_v2" if native_eval else "tracer_eval_logger"
    settings.CH25_EVAL_LOGGER_TABLE = eval_table
    native = health_tests.fake_client(
        {
            **health_tests.NATIVE_COLUMNS,
            eval_table: (
                health_tests.NATIVE_EVAL_COLUMNS
                if native_eval
                else health_tests.CDC_COLUMNS
            ),
        },
        sample=(0, None),
    )
    checker = configure(legacy, native, retired=True)
    assert checker.get_health_status().status == "healthy"

    legacy.ping.return_value = False
    read = legacy.execute_read.side_effect
    legacy.execute_read.side_effect = ConnectionError("synthetic legacy outage")
    health = checker.get_health_status()
    assert health.status == "unhealthy"
    assert health.clickhouse_connected is False
    assert health_tests.response().data["status"] == "unhealthy"

    legacy.ping.return_value = True
    legacy.execute_read.side_effect = read
    assert checker.get_health_status().status == "healthy"
    # Connectivity is still required, but retired trace metadata/data is not.
    assert all(
        call.args[0] == "SELECT 1" for call in legacy.execute_read.call_args_list
    )


@pytest.mark.unit
@pytest.mark.parametrize("topology", ["hostname", "credentials"])
def test_real_clients_preserve_each_verified_connection(monkeypatch, topology):
    import conftest

    @contextmanager
    def owned_database(prefix):
        yield prefix + "synthetic"

    drivers = []

    @contextmanager
    def native_client(*, database):
        # Different per-driver ports catch accidentally copying the first
        # driver's identity to both wrappers, too. No sockets are opened here.
        connection = SimpleNamespace(
            host="test-sidecar" if topology == "hostname" else "localhost",
            port=19000 + len(drivers),
            database=database,
            user="health_test" if topology == "credentials" else "default",
            password="synthetic-password" if topology == "credentials" else "",
        )
        driver = SimpleNamespace(connection=connection)
        drivers.append(driver)
        yield driver

    monkeypatch.setattr(conftest, "_ch_test_owned_database", owned_database)
    monkeypatch.setattr(conftest, "_ch_test_native_client", native_client)
    fixture = health_tests.real_clients.__wrapped__(None)
    try:
        legacy, native, clients = next(fixture)
        for driver, client in zip((legacy, native), clients, strict=True):
            for field in ("host", "port", "user", "password", "database"):
                assert getattr(client, field) == getattr(driver.connection, field), (
                    field
                )
    finally:
        fixture.close()


@pytest.mark.unit
def test_health_budget_is_shared_and_does_not_leak_application_policy(
    configure, monkeypatch
):
    from tracer.services.clickhouse import consistency
    from tracer.services.clickhouse.application_read_policy import is_application_read

    clock = [0.0]
    monkeypatch.setattr(consistency, "monotonic", lambda: clock[0], raising=False)
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    inventory["system.parts"] = PART_COLUMNS
    client = health_tests.fake_client(inventory)
    read = client.execute_read.side_effect
    timeouts = []

    def slow_read(sql, params=None, *, timeout_ms):
        assert not is_application_read()
        timeouts.append(timeout_ms)
        # Simulate the shared deadline being almost consumed by earlier work.
        clock[0] += 4.5 if len(timeouts) == 1 else 0.6
        return read(sql, params)

    client.execute_read.side_effect = slow_read
    checker = configure(client)
    with application_read_context():
        health = checker.get_health_status()
        assert is_application_read()
    assert not is_application_read()
    # Schema and part metadata answered; the lag read found the shared budget
    # spent. A server that answers but runs out of time is slow, not down.
    assert health.clickhouse_connected is True
    assert health.status == "degraded"
    assert set(health.details["checks"].values()) == {"timeout"}
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    cap = consistency.ConsistencyChecker.QUERY_TIMEOUT_MS
    assert cap < consistency.ConsistencyChecker.CHECK_TIMEOUT_SECONDS * 1000
    assert timeouts == [cap, 500]
    client.execute.assert_not_called()


@pytest.mark.unit
def test_destination_the_budget_never_reached_is_slow_not_down(
    configure, monkeypatch, settings
):
    """Mixed routing: the legacy destination answers, then spends the budget."""
    from tracer.services.clickhouse import consistency

    clock = [0.0]
    monkeypatch.setattr(consistency, "monotonic", lambda: clock[0], raising=False)
    settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
    legacy = health_tests.fake_client(SAMPLED_INVENTORY)
    read = legacy.execute_read.side_effect

    def slow_read(sql, params=None, **kwargs):
        clock[0] += consistency.ConsistencyChecker.CHECK_TIMEOUT_SECONDS + 0.1
        return read(sql, params, **kwargs)

    legacy.execute_read.side_effect = slow_read
    native = health_tests.fake_client(
        {
            **health_tests.NATIVE_COLUMNS,
            "tracer_eval_logger": health_tests.CDC_COLUMNS,
        }
    )
    health = configure(legacy, native).get_health_status()
    # The native destination was never contacted: unknown, so not disconnected.
    native.execute_read.assert_not_called()
    assert health.clickhouse_connected is True, health
    assert health.status == "degraded", health
    assert set(health.details["checks"].values()) == {"timeout"}, health


@pytest.mark.unit
def test_ordinary_bounded_read_keeps_ping_and_packet_behavior():
    from tracer.services.clickhouse.client import ClickHouseClient

    socket = Mock()
    socket.gettimeout.return_value = 300
    receive_packet = Mock()
    connection = SimpleNamespace(
        connected=True,
        connect_timeout=10,
        send_receive_timeout=300,
        sync_request_timeout=5,
        receive_packet=receive_packet,
        socket=socket,
    )

    def execute(*args, **kwargs):
        assert connection.sync_request_timeout == 5
        assert connection.receive_packet is receive_packet
        return ([(1,)], [])

    driver = Mock(connection=connection, execute=Mock(side_effect=execute))
    ClickHouseClient._execute_native_read_with_remaining_timeout(
        driver,
        "SELECT 1",
        {},
        query_settings=None,
        started_at=monotonic(),
        timeout_ms=1000,
    )
    socket.settimeout.assert_called_with(300)


@pytest.mark.integration
@pytest.mark.parametrize("saturated", [False, True])
def test_health_does_not_borrow_application_pool(
    configure, real_clients, monkeypatch, saturated
):
    legacy, _, clients = real_clients
    for table in health_tests.CDC_TABLES:
        health_tests.create_cdc(legacy, table)
    source = clients[0]
    source.execute("SELECT 1")
    get_client = Mock(wraps=source._get_client)
    monkeypatch.setattr(source, "_get_client", get_client)
    slots = source._pool_size if saturated else 0
    for _ in range(slots):
        assert source._read_admission.acquire(blocking=False)
    try:
        checker = configure(*clients)
        with application_read_context():
            assert checker.get_health_status().status == "healthy"
        get_client.assert_not_called()
    finally:
        for _ in range(slots):
            source._read_admission.release()


@pytest.mark.unit
@pytest.mark.parametrize("fails", [False, True])
def test_health_reads_clamp_and_restore_native_ping_timeout(fails):
    from tracer.services.clickhouse.health_client import HealthCheckClient

    socket = Mock()
    socket.gettimeout.return_value = 300
    connection = SimpleNamespace(
        connected=True,
        connect_timeout=10,
        send_receive_timeout=300,
        sync_request_timeout=5,
        socket=socket,
        receive_packet=Mock(),
    )
    driver = Mock(connection=connection)

    def execute(*args, **kwargs):
        # clickhouse-driver pings reused connections before executing SQL. That
        # ping temporarily overrides the socket timeout with this separate knob.
        assert 0 < connection.sync_request_timeout <= 1
        if fails:
            raise TimeoutError("synthetic stalled native ping")
        return ([(1,)], [])

    driver.execute.side_effect = execute
    try:
        if fails:
            with pytest.raises(TimeoutError):
                HealthCheckClient._execute_native_read_with_remaining_timeout(
                    driver,
                    "SELECT 1",
                    {},
                    query_settings=None,
                    started_at=monotonic(),
                    timeout_ms=1000,
                )
        else:
            assert HealthCheckClient._execute_native_read_with_remaining_timeout(
                driver,
                "SELECT 1",
                {},
                query_settings=None,
                started_at=monotonic(),
                timeout_ms=1000,
            ) == ([(1,)], [])
    finally:
        assert connection.sync_request_timeout == 5
        assert connection.send_receive_timeout == 300
        assert connection.connect_timeout == 10
        socket.settimeout.assert_called_with(300)


@pytest.mark.unit
def test_health_progress_packets_share_one_deadline(monkeypatch):
    from tracer.services.clickhouse import health_client

    clock = [monotonic()]
    monkeypatch.setattr(health_client, "monotonic", lambda: clock[0])
    socket = Mock()
    socket.gettimeout.return_value = 300

    def progress_packet():
        clock[0] += 0.4
        return object()

    connection = SimpleNamespace(
        connected=True,
        connect_timeout=10,
        send_receive_timeout=300,
        sync_request_timeout=5,
        socket=socket,
        receive_packet=progress_packet,
    )

    def execute(*args, **kwargs):
        for _ in range(10):
            connection.receive_packet()
        pytest.fail("Progress packets extended the health deadline")

    driver = Mock(connection=connection, execute=Mock(side_effect=execute))
    # The base helper has its own clock; only the health packet guard's clock
    # advances here, so the test specifically exercises packet-level bounding.
    with pytest.raises(TimeoutError, match="health check deadline"):
        health_client.HealthCheckClient._execute_native_read_with_remaining_timeout(
            driver,
            "SELECT 1",
            {},
            query_settings=None,
            started_at=monotonic(),
            timeout_ms=1000,
        )
    assert connection.receive_packet is progress_packet
    assert connection.sync_request_timeout == 5


@pytest.mark.unit
@pytest.mark.parametrize("readonly", [False, True])
def test_health_client_preserves_destination_and_profile_without_sharing_pool(readonly):
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.health_client import HealthCheckClient

    source = ClickHouseClient(
        host="synthetic-sidecar",
        port=19000,
        user="health_user",
        password="synthetic-password",
        database="test_health",
        server_enforced_readonly=readonly,
        allow_query_settings_with_server_readonly=readonly,
    )
    probe = HealthCheckClient.from_client(source, timeout_ms=500)
    for field in (
        "host",
        "port",
        "user",
        "password",
        "database",
        "server_enforced_readonly",
        "allow_query_settings_with_server_readonly",
    ):
        assert getattr(probe, field) == getattr(source, field)
    assert probe._pool is not source._pool
    assert probe._read_admission is not source._read_admission
    assert probe.connect_timeout == probe.send_timeout == probe.receive_timeout == 0.5
    assert source.receive_timeout == 300


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["metadata", "parts", "lag"])
@pytest.mark.parametrize("warm_pool", [False, True])
@pytest.mark.parametrize("server_readonly", [False, True])
def test_every_health_query_has_a_short_transport_timeout(
    configure, real_clients, monkeypatch, stage, warm_pool, server_readonly
):
    """A real slow native read must fail promptly, even inside application mode.

    Substitute one three-second statement at the driver boundary, below the
    health checker. This exercises the actual server and socket timeout (not a
    mocked TimeoutError), without disrupting a sidecar used by other tests. The
    server completed the handshake, so it is slow, not down: connected and
    degraded, never disconnected.
    """
    from tracer.services.clickhouse.consistency import ConsistencyChecker

    cap = ConsistencyChecker.QUERY_TIMEOUT_MS / 1000
    assert cap < 3, "the injected statement must outlast one statement's cap"
    legacy, _, clients = real_clients
    for table in health_tests.CDC_TABLES:
        health_tests.create_cdc(legacy, table)
    checker = configure(*clients)
    client = clients[0]
    client.server_enforced_readonly = server_readonly
    if warm_pool:
        with application_read_context():
            client.execute_read("SELECT 1")
        assert checker.get_health_status().status == "healthy"

    from tracer.services.clickhouse.health_client import HealthCheckClient

    original_create = HealthCheckClient._create_client
    drivers = []
    delayed = False
    needle = {
        "metadata": "system.columns",
        "parts": "FROM system.parts",
        "lag": "max(_peerdb",
    }[stage]

    def create_client(self, **kwargs):
        driver = original_create(self, **kwargs)
        drivers.append(driver)
        execute = driver.execute

        def execute_with_delay(sql, *args, **kwargs):
            nonlocal delayed
            if needle in sql and not delayed:
                delayed = True
                execute("SELECT sleep(3)", settings=kwargs.get("settings"))
            return execute(sql, *args, **kwargs)

        monkeypatch.setattr(driver, "execute", execute_with_delay)
        return driver

    monkeypatch.setattr(HealthCheckClient, "_create_client", create_client)
    started = monotonic()
    with application_read_context():
        result = health_tests.response()
    elapsed = monotonic() - started
    assert delayed, "The intended real query was not exercised"
    assert result.status_code == 200
    assert result.data["status"] == "degraded", result.data
    assert result.data["clickhouse_connected"] is True, result.data
    assert elapsed < cap + 0.5, f"{stage} health query took {elapsed:.3f}s"
    assert health_tests.response().data["status"] == "healthy"
    assert all(not driver.connection.connected for driver in drivers)
    driver = client._get_client()
    try:
        assert driver not in drivers
        if warm_pool:
            # The application's transport context restores its pool defaults.
            assert driver.connection.socket.gettimeout() == 300
        assert driver.connection.send_receive_timeout == 300
        assert driver.connection.sync_request_timeout == 5
    finally:
        client._return_client(driver)


PART_COLUMNS = {"database", "table", "name", "rows", "active", "modification_time"}
SAMPLED_INVENTORY = {
    **dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS),
    "system.parts": PART_COLUMNS,
}


def _lag_statements(client):
    return [
        call.args[:2]
        for call in client.execute_read.call_args_list
        if "max(_peerdb_synced_at)" in call.args[0]
    ]


def _sampled_ranges(params):
    """Every ``(partition, low block, high block)`` a lag statement selects."""
    return {
        (value, params[f"low{key[9:]}"], params[f"high{key[9:]}"])
        for key, value in params.items()
        if key.startswith("partition")
    }


@pytest.mark.unit
def test_lag_sample_plan_reads_newest_parts_within_the_budget(configure, monkeypatch):
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 1_000)
    checker = configure(health_tests.fake_client({}))
    now = datetime(2026, 1, 1, 12, 0, 30)
    earlier = datetime(2026, 1, 1, 12, 0, 0)
    # Newest first, as the system.parts ORDER BY returns them.
    reads = checker._plan(
        [
            ("202611_9_9_0", 5_000, now),  # newest, over the budget: a prefix
            ("202611_8_8_0", 10, earlier),
            ("202611_7_7_0", 900, earlier),  # overruns what is left: skipped
            ("202610_5_6_1", 200, earlier),
            ("not-a-part-name", 1, earlier),  # unparseable: never guessed at
            ("202609_1_4_2_7", 50, earlier),  # mutated part: same block range
        ]
    )
    assert reads == [
        consistency._SampleRead((("202611", 9, 9),), rows=100, prefix=True),
        consistency._SampleRead(
            (("202611", 8, 8), ("202610", 5, 6), ("202609", 1, 4)), rows=900
        ),
    ]
    # A newest part that fits is read in full, with the whole budget as headroom;
    # an oversized part written in an earlier second is skipped.
    assert checker._plan([("all_3_3_0", 7, now), ("all_1_2_1", 2_000, earlier)]) == [
        consistency._SampleRead((("all", 3, 3),), rows=1_000)
    ]
    # Parts written in the same second are equally new: the batch written
    # alongside small parts still contributes its prefix, and only one does.
    assert checker._plan(
        [
            ("202611_5_5_0", 3, now),
            ("202611_4_4_0", 2_000, now),
            ("202610_2_2_0", 3_000, now),
        ]
    ) == [
        consistency._SampleRead((("202611", 4, 4),), rows=100, prefix=True),
        consistency._SampleRead((("202611", 5, 5),), rows=900),
    ]
    assert checker._plan([]) == []


@pytest.mark.unit
def test_lag_sample_is_one_statement_selecting_parts_by_block_range(
    configure, monkeypatch
):
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 1_000)
    parts = [
        ("tracer_trace", "202611_9_9_0", 5_000),
        ("tracer_trace", "202611_8_8_0", 10),
        ("tracer_trace", "202611_7_7_0", 900),
        ("tracer_trace", "202610_5_6_1", 200),
        ("trace_session", "all_3_3_0", 3),
    ]
    client = health_tests.fake_client(SAMPLED_INVENTORY, parts=parts)
    health = configure(client).get_health_status()

    assert health.status == "healthy"
    assert all(0 <= lag < 60 for lag in health.cdc_lag.values())
    sql = [call.args[0] for call in client.execute_read.call_args_list]
    assert len(sql) == 3, sql
    assert "system.columns" in sql[0] and "FROM system.parts" in sql[1]
    ((statement, params),) = _lag_statements(client)
    assert health_tests.sampled_tables(params) == list(health_tests.CDC_TABLES)
    # Parts are chosen by partition and block range, which a merge keeps, not
    # by name, which a merge replaces.
    assert "_part IN" not in statement
    assert _sampled_ranges(params) == {
        ("202611", 9, 9),
        ("202611", 8, 8),
        ("202610", 5, 6),
        ("all", 3, 3),
    }
    assert "_part_offset < 100" in statement
    # No active part: the whole-table aggregate, free on an empty table.
    assert statement.endswith("FROM tracer_eval_logger")


@pytest.mark.unit
@pytest.mark.parametrize("visible", [set(), {"name", "rows"}])
def test_lag_sample_needs_every_part_column_visible(configure, visible):
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    if visible:
        inventory["system.parts"] = visible
    client = health_tests.fake_client(
        inventory, parts=[("tracer_trace", "all_1_1_0", 1)]
    )
    assert configure(client).get_health_status().status == "healthy"
    assert not any(
        "FROM system.parts" in call.args[0]
        for call in client.execute_read.call_args_list
    )
    ((statement, params),) = _lag_statements(client)
    assert "_part" not in statement
    assert health_tests.sampled_tables(params) == list(health_tests.CDC_TABLES)
    for table in health_tests.CDC_TABLES:
        assert f"FROM {table}" in statement


@pytest.mark.unit
def test_lag_sample_with_no_visible_row_uses_the_exact_aggregate(
    configure, monkeypatch
):
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 100)
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[
            ("tracer_trace", "202611_2_2_0", 1_000),
            ("trace_session", "202611_4_4_0", 5),
            ("tracer_eval_logger", "202611_6_6_0", 1),
        ],
    )
    read = client.execute_read.side_effect

    def masked_sample(sql, params=None, **kwargs):
        rows, columns, elapsed = read(sql, params, **kwargs)
        if "max(_peerdb_synced_at)" in sql and "_partition_id" in sql:
            # Parts can hold only masked rows, e.g. after a lightweight delete.
            rows = [
                (table, 0, datetime(1970, 1, 1))
                if table == "trace_session"
                else (table, *sample)
                for table, *sample in rows
            ]
        return rows, columns, elapsed

    client.execute_read.side_effect = masked_sample
    health = configure(client).get_health_status()
    assert health.status == "healthy"
    first, second = _lag_statements(client)
    assert health_tests.sampled_tables(first[1]) == list(health_tests.CDC_TABLES)
    # The oversized newest part is a bounded prefix, never the whole table.
    assert "_part_offset < 10" in first[0]
    # Only the masked sample falls back, and only to the exact aggregate.
    assert health_tests.sampled_tables(second[1]) == ["trace_session"]
    assert "_part" not in second[0]


@pytest.mark.unit
def test_failed_part_inventory_is_unavailable_not_healthy(configure):
    client = health_tests.fake_client(SAMPLED_INVENTORY)
    read = client.execute_read.side_effect

    def unavailable(sql, params=None, **kwargs):
        if "FROM system.parts" in sql:
            raise ConnectionError("synthetic metadata outage")
        return read(sql, params, **kwargs)

    client.execute_read.side_effect = unavailable
    health = configure(client).get_health_status()
    assert health.status == "unhealthy"
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert set(health.details["checks"].values()) == {"unavailable"}
    assert not _lag_statements(client)


def _deny(client, needle):
    """Refuse reads containing ``needle`` as ClickHouse does: ACCESS_DENIED."""
    from clickhouse_driver.errors import ServerException

    read = client.execute_read.side_effect

    def execute_read(sql, params=None, **kwargs):
        if needle in sql:
            raise ServerException("synthetic missing grant", code=497)
        return read(sql, params, **kwargs)

    client.execute_read.side_effect = execute_read


@pytest.mark.unit
@pytest.mark.parametrize("parts_access", ["columns_hidden", "read_denied"])
def test_role_without_part_metadata_never_aggregates_a_large_table(
    configure, monkeypatch, parts_access
):
    """A readonly=1 role cannot cap a statement, so the probe must bound itself.

    Without readable ``system.parts`` the probe used to send the exact
    whole-table aggregate for every mirror: a large one outgrew the statement
    cap (or a server row limit) and the whole destination read unhealthy.
    """
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 1_000)
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    if parts_access == "read_denied":
        # SHOW COLUMNS lists the columns; only the SELECT is refused.
        inventory["system.parts"] = PART_COLUMNS
    client = health_tests.fake_client(
        inventory,
        parts=[(table, "all_1_1_0", 1) for table in health_tests.CDC_TABLES],
        table_rows={"tracer_trace": 1_001, "trace_session": 1_000},
    )
    if parts_access == "read_denied":
        _deny(client, "FROM system.parts")
    configure(client)
    with capture_logs() as events:
        result = health_tests.response()
    health = consistency.ConsistencyChecker().get_health_status()

    assert result.status_code == 200
    assert set(result.data) == {"status", "clickhouse_connected", "cdc_lag", "routing"}
    assert result.data["status"] == "degraded", result.data
    assert result.data["clickhouse_connected"] is True
    assert result.data["cdc_lag"]["tracer_trace"] == -1
    assert all(
        0 <= result.data["cdc_lag"][table] < 60
        for table in ("trace_session", "tracer_eval_logger")
    ), result.data
    assert health.details["checks"] == {
        "legacy.tracer_trace": "unsampled",
        "legacy.trace_session": "ready",
        "legacy.tracer_eval_logger": "ready",
    }
    # The large mirror is never read; the small ones keep the exact aggregate.
    for statement, params in _lag_statements(client):
        assert "tracer_trace" not in health_tests.sampled_tables(params), statement
        assert "_part" not in statement
    unknown = [e for e in events if e["event"] == "CDC lag unknown"]
    assert unknown and unknown[0]["tables"] == ["tracer_trace"], events
    assert unknown[0]["cause"] == "no_part_metadata"
    assert "ON system.parts" in unknown[0]["required_grant"]
    assert not [e for e in events if e["event"] == "CDC lag check failed"], events


@pytest.mark.unit
def test_unknown_lag_never_hides_a_missing_table(configure, monkeypatch):
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 1_000)
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    del inventory["trace_session"]
    client = health_tests.fake_client(inventory, table_rows={"tracer_trace": 5_000})
    health = configure(client).get_health_status()
    assert health.status == "unhealthy", health
    assert health.details["checks"]["legacy.trace_session"] == "missing_required_table"
    assert health.details["checks"]["legacy.tracer_trace"] == "unsampled"
    assert health.cdc_lag["trace_session"] == health.cdc_lag["tracer_trace"] == -1


@pytest.mark.unit
@pytest.mark.parametrize("sizes", ["readable", "denied"])
def test_masked_sample_of_a_large_table_is_unknown_not_a_full_scan(
    configure, monkeypatch, sizes
):
    """A masked sample of a large table is unknown lag, not a whole-table scan.

    The retry used to aggregate the whole table, and its failure blanked every
    mirror of the destination.
    """
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 100)
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[
            ("tracer_trace", "202611_2_2_0", 1_000),
            ("trace_session", "202611_4_4_0", 5),
            ("tracer_eval_logger", "202611_6_6_0", 1),
        ],
        table_rows={"tracer_trace": 50_000},
    )
    read = client.execute_read.side_effect

    def masked_sample(sql, params=None, **kwargs):
        rows, columns, elapsed = read(sql, params, **kwargs)
        if "max(_peerdb_synced_at)" in sql and "_partition_id" in sql:
            rows = [
                (table, 0, datetime(1970, 1, 1))
                if table == "tracer_trace"
                else (table, *sample)
                for table, *sample in rows
            ]
        return rows, columns, elapsed

    client.execute_read.side_effect = masked_sample
    if sizes == "denied":
        _deny(client, "FROM system.tables")
    with capture_logs() as events:
        health = configure(client).get_health_status()

    assert health.status == "degraded", health
    assert health.details["checks"]["legacy.tracer_trace"] == "unsampled"
    (unknown,) = [e for e in events if e["event"] == "CDC lag unknown"]
    assert unknown["cause"] == "masked_sample" and unknown["required_grant"] is None
    assert health.cdc_lag["tracer_trace"] == -1
    # The other mirrors keep the lag their sample already read.
    assert all(
        0 <= health.cdc_lag[table] < 60
        for table in ("trace_session", "tracer_eval_logger")
    ), health
    ((statement, params),) = _lag_statements(client)
    assert health_tests.sampled_tables(params) == list(health_tests.CDC_TABLES)
    assert "_part_offset < 10" in statement


@pytest.mark.unit
def test_failed_size_read_is_timeout_on_that_destination_only(configure, settings):
    """A slow ``system.tables`` read is slow, not down, and stays local."""
    settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
    legacy = health_tests.fake_client(
        dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    )
    legacy.handshake_completed = True
    read = legacy.execute_read.side_effect

    def slow_sizes(sql, params=None, **kwargs):
        if "FROM system.tables" in sql:
            raise TimeoutError("synthetic socket timeout")
        return read(sql, params, **kwargs)

    legacy.execute_read.side_effect = slow_sizes
    native = health_tests.fake_client(
        {
            **health_tests.NATIVE_COLUMNS,
            "tracer_eval_logger": health_tests.CDC_COLUMNS,
            "system.parts": PART_COLUMNS,
        },
        parts=[("tracer_eval_logger", "all_1_1_0", 1)],
    )
    health = configure(legacy, native).get_health_status()
    assert health.clickhouse_connected is True, health
    assert health.status == "degraded", health
    checks = health.details["checks"]
    assert {checks[f"legacy.{table}"] for table in health_tests.CDC_TABLES} == {
        "timeout"
    }
    assert checks["native.tracer_eval_logger"] == "ready"
    assert not _lag_statements(legacy)


@pytest.mark.unit
@pytest.mark.parametrize("mixed", [False, True])
def test_one_connection_and_three_statements_per_destination(
    configure, monkeypatch, settings, mixed
):
    """Each statement used to open its own connection: ~20 round trips a check."""
    from tracer.services.clickhouse import consistency

    parts = [(table, "all_1_1_0", 1) for table in health_tests.CDC_TABLES]
    legacy = health_tests.fake_client(SAMPLED_INVENTORY, parts=parts)
    native = None
    if mixed:
        settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
        native = health_tests.fake_client(
            {
                **health_tests.NATIVE_COLUMNS,
                "tracer_eval_logger": health_tests.CDC_COLUMNS,
                "system.parts": PART_COLUMNS,
            },
            parts=parts,
        )
    checker = configure(legacy, native)
    factory = consistency.HealthCheckClient.from_client
    opened = []

    def counting_factory(source, **kwargs):
        opened.append(source)
        return factory(source, **kwargs)

    monkeypatch.setattr(consistency.HealthCheckClient, "from_client", counting_factory)
    assert checker.get_health_status().status == "healthy"
    clients = [legacy, native] if mixed else [legacy]
    assert opened == clients
    for client in clients:
        sql = [call.args[0] for call in client.execute_read.call_args_list]
        assert len(sql) == 3, sql
        assert "system.columns" in sql[0] and "FROM system.parts" in sql[1]
        assert "max(_peerdb_synced_at)" in sql[2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "stage", ["system.columns", "FROM system.parts", "max(_peerdb"]
)
@pytest.mark.parametrize("error", ["client", "server"])
def test_reachable_but_slow_is_degraded_not_disconnected(configure, stage, error):
    """A server that completed the handshake and then runs out of time is slow."""
    from clickhouse_driver.errors import ServerException

    failure = (
        TimeoutError("synthetic socket timeout")
        if error == "client"
        else ServerException("synthetic", code=159)  # TIMEOUT_EXCEEDED
    )
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[(table, "all_1_1_0", 1) for table in health_tests.CDC_TABLES],
    )
    client.handshake_completed = True
    read = client.execute_read.side_effect

    def slow(sql, params=None, **kwargs):
        if stage in sql:
            raise failure
        return read(sql, params, **kwargs)

    client.execute_read.side_effect = slow
    configure(client)
    result = health_tests.response()
    assert result.status_code == 200
    assert result.data["clickhouse_connected"] is True, result.data
    assert result.data["status"] == "degraded", result.data
    assert result.data["cdc_lag"] == dict.fromkeys(health_tests.CDC_TABLES, -1)


@pytest.mark.unit
def test_timeout_before_any_answer_is_disconnected(configure):
    client = health_tests.fake_client(SAMPLED_INVENTORY)
    client.handshake_completed = False
    client.execute_read.side_effect = TimeoutError("synthetic connect timeout")
    configure(client)
    result = health_tests.response()
    assert result.status_code == 200
    assert result.data["clickhouse_connected"] is False
    assert result.data["status"] == "unhealthy"


@pytest.mark.unit
def test_server_that_never_answers_the_handshake_is_disconnected(configure):
    """TCP accepts, the native hello never comes back: down, within the cap."""
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.consistency import ConsistencyChecker

    listener = socket.create_server(("127.0.0.1", 0))
    held = []
    threading.Thread(
        target=lambda: held.append(listener.accept()[0]), daemon=True
    ).start()
    client = ClickHouseClient(host="127.0.0.1", port=listener.getsockname()[1])
    try:
        started = monotonic()
        health = configure(client).get_health_status()
        elapsed = monotonic() - started
    finally:
        client.close()
        listener.close()
        for sock in held:
            sock.close()
    assert health.clickhouse_connected is False, health
    assert health.status == "unhealthy", health
    assert elapsed < ConsistencyChecker.QUERY_TIMEOUT_MS / 1000 + 0.5, elapsed


class _DelayedProxy:
    """Forward native connections, delaying every server-to-client byte.

    Like ``netem`` on the server's egress: each byte arrives ``delay`` seconds
    late, and bytes sent together arrive together. The loopback TCP handshake
    is not delayed, so each request/response exchange costs one ``delay``.
    """

    def __init__(self, upstream, delay):
        self._upstream = upstream
        self._delay = delay
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self.accepted = 0
        self._sockets = [self._listener]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                downstream, _ = self._listener.accept()
            except OSError:
                return
            self.accepted += 1
            upstream = socket.create_connection(self._upstream)
            for sock in (downstream, upstream):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._sockets.append(sock)
            self._forward(downstream, upstream, 0.0)
            self._forward(upstream, downstream, self._delay)

    @staticmethod
    def _forward(source, target, delay):
        pending = queue.Queue()

        def receive():
            while True:
                try:
                    data = source.recv(65536)
                except OSError:
                    data = b""
                pending.put((monotonic() + delay, data))
                if not data:
                    return

        def send():
            while True:
                due, data = pending.get()
                pause = due - monotonic()
                if pause > 0:
                    sleep(pause)
                try:
                    if not data:
                        target.shutdown(socket.SHUT_WR)
                        return
                    target.sendall(data)
                except OSError:
                    return

        threading.Thread(target=receive, daemon=True).start()
        threading.Thread(target=send, daemon=True).start()

    def close(self):
        for sock in self._sockets:
            with suppress(OSError):
                sock.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "delay,status", [(0.25, "healthy"), (0.5, "healthy"), (1.5, "degraded")]
)
def test_slow_reachable_clickhouse_is_connected_and_costs_few_round_trips(
    configure, delay, status
):
    """Every server byte arrives ``delay`` seconds late; the server itself works.

    Each statement used to open its own connection (handshake, then query)
    under a one-second cap: about twenty round trips a check, and from about
    half a second of latency the first statement timed out, so a working server
    read as disconnected. Now one connection carries one handshake and three
    statements; a server too slow for them is connected and degraded.
    """
    from conftest import _ch_test_native_client, _ch_test_owned_database
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.consistency import ConsistencyChecker

    with (
        _ch_test_owned_database("test_health_latency_") as database,
        _ch_test_native_client(database=database) as admin,
    ):
        for table in health_tests.CDC_TABLES:
            health_tests.create_cdc(admin, table)
            admin.execute(
                f"INSERT INTO {table} (id, project_id, _peerdb_synced_at) VALUES",
                [(uuid4(), uuid4(), datetime.now(UTC))],
            )
        connection = admin.connection
        proxy = _DelayedProxy((connection.host, connection.port), delay)
        client = ClickHouseClient(
            host="127.0.0.1",
            port=proxy.port,
            database=database,
            user=connection.user,
            password=connection.password,
        )
        try:
            checker = configure(client)
            started = monotonic()
            health = checker.get_health_status()
            elapsed = monotonic() - started
        finally:
            client.close()
            proxy.close()
    detail = {"health": health, "elapsed": round(elapsed, 3), "opened": proxy.accepted}
    assert health.clickhouse_connected is True, detail
    assert health.status == status, detail
    assert proxy.accepted == 1, detail
    if status == "healthy":
        assert all(0 <= lag < 60 for lag in health.cdc_lag.values()), detail
        # One handshake and three statements; the old check needed twelve.
        assert elapsed < 6 * delay, detail
    else:
        assert set(health.details["checks"].values()) == {"timeout"}, detail
        assert elapsed < ConsistencyChecker.QUERY_TIMEOUT_MS / 1000 + 0.5, detail


@pytest.mark.unit
def test_lag_sample_plan_charges_a_prefix_its_whole_granules(configure, monkeypatch):
    """A prefix may read up to a granule past its rows, so the parts beside it
    fit what is left."""
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 20_000)
    checker = configure(health_tests.fake_client({}))
    now = datetime(2026, 1, 1, 12, 0, 30)
    earlier = datetime(2026, 1, 1, 12, 0, 0)
    prefix = consistency._SampleRead((("202611", 9, 9),), rows=2_000, prefix=True)
    # An older part fits beside the planned 2,000 rows, not the 10,191 that
    # may be read for them.
    assert checker._plan(
        [
            ("202611_10_10_0", 1, now),
            ("202611_9_9_0", 50_000, now),
            ("202610_1_1_0", 15_000, earlier),
            ("202609_1_1_0", 5_000, earlier),
        ]
    ) == [
        prefix,
        consistency._SampleRead(
            (("202611", 10, 10), ("202609", 1, 1)), rows=20_000 - 10_191
        ),
    ]
    # A part of the same second taken before the prefix is kept: it may hold
    # the latest sync. With no room left for a granule, there is no prefix.
    assert checker._plan(
        [("202611_5_5_0", 15_000, now), ("202611_9_9_0", 50_000, now)]
    ) == [consistency._SampleRead((("202611", 5, 5),), rows=20_000)]


def _planned_cost(checker, reads):
    return sum(
        checker._prefix_cost(read.rows) if read.prefix else read.rows for read in reads
    )


@pytest.mark.unit
def test_lag_sample_plan_never_drops_a_current_part_for_a_prefix(configure):
    """The default budget, a current part and a larger merged part written in
    the same second (ties are listed by rows, smallest first): the current
    part stays in the sample and the prefix gets what it leaves."""
    from tracer.services.clickhouse import consistency

    checker = configure(health_tests.fake_client({}))
    budget = consistency.ConsistencyChecker.LAG_SAMPLE_MAX_ROWS
    granule = consistency.ConsistencyChecker.LAG_SAMPLE_GRANULE_ROWS
    assert (budget, granule) == (10_000_000, 8192)
    now = datetime(2026, 11, 13, 12, 0, 0)

    reads = checker._plan(
        [("202611_1_1_0", 9_000_000, now), ("202610_2_2_1", 12_000_000, now)]
    )

    assert reads == [
        consistency._SampleRead(
            (("202610", 2, 2),), rows=1_000_000 - (granule - 1), prefix=True
        ),
        consistency._SampleRead((("202611", 1, 1),), rows=budget - 1_000_000),
    ]
    assert _planned_cost(checker, reads) == budget


@pytest.mark.unit
@pytest.mark.parametrize(
    "taken, prefix_rows",
    [
        # Less than one granule left: no prefix.
        (10_000_000 - 8_190, None),
        # Exactly one granule left: one row.
        (10_000_000 - 8_192, 1),
        # One granule and a row left: two rows.
        (10_000_000 - 8_193, 2),
        # Plenty left: a tenth of the budget.
        (1_000, 1_000_000),
        # Nothing taken, the oversized part alone: a tenth of the budget.
        (0, 1_000_000),
    ],
    ids=["below-a-granule", "one-granule", "granule-and-a-row", "room", "alone"],
)
def test_lag_sample_prefix_fits_what_the_current_parts_leave(
    configure, taken, prefix_rows
):
    from tracer.services.clickhouse import consistency

    checker = configure(health_tests.fake_client({}))
    budget = consistency.ConsistencyChecker.LAG_SAMPLE_MAX_ROWS
    now = datetime(2026, 11, 13, 12, 0, 0)
    parts = [("202611_1_1_0", taken, now)] if taken else []
    parts += [
        ("202610_2_2_1", 12_000_000, now),
        ("202609_3_3_0", 50_000, datetime(2026, 11, 13, 11, 0, 0)),
    ]

    reads = checker._plan(parts)

    prefixes = [read for read in reads if read.prefix]
    full = [read for read in reads if not read.prefix]
    if prefix_rows is None:
        assert prefixes == []
    else:
        assert prefixes == [
            consistency._SampleRead((("202610", 2, 2),), rows=prefix_rows, prefix=True)
        ]
    if taken:
        # The current part is always read in full.
        assert ("202611", 1, 1) in full[0].ranges
    assert _planned_cost(checker, reads) <= budget
