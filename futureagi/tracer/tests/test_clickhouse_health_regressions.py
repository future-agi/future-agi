"""Health regressions for retained readers, verified sidecars and stalled queries."""

import queue
import socket
import threading
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
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
            "system.parts": health_tests.PART_COLUMNS,
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


PART_COLUMNS = health_tests.PART_COLUMNS
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


@pytest.mark.unit
@pytest.mark.parametrize("rows", [0, 1, 9_999_999, 10_000_000, 10_000_001])
def test_lag_plan_reads_complete_parts_or_nothing(configure, rows):
    from tracer.services.clickhouse import consistency

    checker = configure(health_tests.fake_client({}))
    parts = [("202611_1_2_1", rows)] if rows else []
    plan = checker._plan(parts)
    if rows > checker.LAG_SAMPLE_MAX_ROWS:
        assert plan is None
    else:
        assert plan == consistency._SampleRead(tuple(parts))
        assert plan.rows == rows
    # No prefix of an oversized part, even beside a small current part.
    assert checker._plan([("202611_1_1_0", 1), ("202611_2_9_2", 12_000_000)]) is None


@pytest.mark.unit
def test_lag_selects_immutable_names_and_rechecks_complete_inventory(configure):
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[
            ("tracer_trace", "202611_1_1_0", 100),
            ("tracer_trace", "202611_2_3_1", 200),
            ("trace_session", "all_1_1_0_7", 3),
        ],
    )
    health = configure(client).get_health_status()
    assert health.status == "healthy"
    assert health.cdc_lag_verified == {
        "tracer_trace": True,
        "trace_session": True,
        "tracer_eval_logger": False,
    }
    ((sql, params),) = _lag_statements(client)
    assert params["parts0"] == ("202611_1_1_0", "202611_2_3_1")
    assert params["parts1"] == ("all_1_1_0_7",)
    assert "FROM tracer_eval_logger WHERE 0" in sql
    assert "_part IN" in sql
    assert "LIMIT" not in sql and "_part_offset" not in sql
    assert "splitByChar" not in sql
    statements = [call.args[0] for call in client.execute_read.call_args_list]
    assert len(statements) == 4
    assert "FROM system.parts" in statements[1]
    assert "FROM system.parts" in statements[3]


@pytest.mark.unit
@pytest.mark.parametrize(
    "visible",
    [pytest.param(set(), id="hidden"), pytest.param({"name", "rows"}, id="partial")]
    + [
        pytest.param(PART_COLUMNS - {column}, id=f"missing-{column}")
        for column in sorted(PART_COLUMNS)
    ],
)
def test_lag_needs_every_part_column_visible_without_unsafe_small_scan(
    configure, visible
):
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    if visible:
        inventory["system.parts"] = visible
    client = health_tests.fake_client(inventory)
    health = configure(client).get_health_status()
    assert health.status == "degraded"
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert health.cdc_lag_verified == dict.fromkeys(health_tests.CDC_TABLES, False)
    assert set(health.details["checks"].values()) == {"unsampled"}
    assert not _lag_statements(client)
    assert not any(
        "FROM system.parts" in c.args[0] for c in client.execute_read.call_args_list
    )


@pytest.mark.unit
@pytest.mark.parametrize("count", [0, 50, 100, 101])
def test_masked_or_partial_read_is_unverified_and_never_retried(configure, count):
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[("tracer_trace", "202611_1_1_0", 100)],
        sample=(count, datetime.now(UTC)),
    )
    health = configure(client).get_health_status()
    assert health.status == "healthy"
    assert health.cdc_lag_verified["tracer_trace"] is (count == 100)
    assert (health.cdc_lag["tracer_trace"] >= 0) is (count == 100)
    assert len(_lag_statements(client)) == 1
    assert all(
        "FROM system.tables" not in c.args[0]
        for c in client.execute_read.call_args_list
    )


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
def test_small_stale_mirror_without_part_metadata_degrades_without_data_scan(
    configure, parts_access
):
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    if parts_access == "read_denied":
        inventory["system.parts"] = PART_COLUMNS
    client = health_tests.fake_client(
        inventory,
        sample=(100, datetime.now(UTC) - timedelta(minutes=10)),
        table_rows=dict.fromkeys(health_tests.CDC_TABLES, 100),
    )
    if parts_access == "read_denied":
        _deny(client, "FROM system.parts")
    health = configure(client).get_health_status()
    assert health.clickhouse_connected is True
    assert health.status == "degraded"
    assert set(health.details["checks"].values()) == {"unsampled"}
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert health.cdc_lag_verified == dict.fromkeys(health_tests.CDC_TABLES, False)
    assert not _lag_statements(client)


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
    assert set(result.data) == {
        "status",
        "clickhouse_connected",
        "cdc_lag",
        "cdc_lag_verified",
        "routing",
    }
    assert result.data["status"] == "degraded", result.data
    assert result.data["clickhouse_connected"] is True
    assert result.data["cdc_lag"]["tracer_trace"] == -1
    assert result.data["cdc_lag"] == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert result.data["cdc_lag_verified"] == dict.fromkeys(
        health_tests.CDC_TABLES, False
    )
    assert health.details["checks"] == {
        "legacy.tracer_trace": "unsampled",
        "legacy.trace_session": "unsampled",
        "legacy.tracer_eval_logger": "unsampled",
    }
    # Size metadata cannot bound a later scan, even for the small mirrors.
    assert not _lag_statements(client)
    unknown = [e for e in events if e["event"] == "CDC lag unknown"]
    assert unknown and unknown[0]["tables"] == list(health_tests.CDC_TABLES), events
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
@pytest.mark.parametrize("size", [None, 0, 100, 10_000_000, 10_000_001])
@pytest.mark.parametrize("denied", [False, True])
def test_missing_part_metadata_degrades_regardless_of_table_size(
    configure, size, denied
):
    client = health_tests.fake_client(
        dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS),
        table_rows=dict.fromkeys(health_tests.CDC_TABLES, size),
    )
    if denied:
        _deny(client, "FROM system.tables")
    health = configure(client).get_health_status()
    assert health.status == "degraded"
    assert set(health.details["checks"].values()) == {"unsampled"}
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert health.cdc_lag_verified == dict.fromkeys(health_tests.CDC_TABLES, False)
    assert not _lag_statements(client)


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
def test_one_connection_and_four_statements_per_destination(
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
        assert len(sql) == 4, sql
        assert "system.columns" in sql[0] and "FROM system.parts" in sql[1]
        assert "max(_peerdb_synced_at)" in sql[2]
        assert "FROM system.parts" in sql[3]


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
    assert result.data["cdc_lag_verified"] == dict.fromkeys(
        health_tests.CDC_TABLES, False
    )


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
    read as disconnected. Now one connection carries one handshake and four
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
        # One handshake and four statements, including the coverage recheck.
        assert elapsed < 7 * delay, detail
    else:
        assert set(health.details["checks"].values()) == {"timeout"}, detail
        assert elapsed < ConsistencyChecker.QUERY_TIMEOUT_MS / 1000 + 0.5, detail


@pytest.mark.unit
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("fresh", [False, True])
def test_repair_and_fresh_samples_never_verify_oversized_coverage(
    configure, native, fresh
):
    """Part age and even a fresh prefix cannot prove the global maximum."""
    inventory = (
        {
            **health_tests.NATIVE_COLUMNS,
            "tracer_eval_logger": health_tests.CDC_COLUMNS,
            "system.parts": PART_COLUMNS,
        }
        if native
        else SAMPLED_INVENTORY
    )
    tables = ("tracer_eval_logger",) if native else health_tests.CDC_TABLES
    client = health_tests.fake_client(
        inventory,
        sample=(100, datetime.now(UTC) - timedelta(seconds=5 if fresh else 600)),
        parts=[(table, "202611_1_9_2", 12_000_000) for table in tables],
    )
    checker = (
        configure(health_tests.fake_client({}), client, retired=True)
        if native
        else configure(client)
    )
    health = checker.get_health_status()
    assert health.status == "healthy"
    assert health.cdc_lag == dict.fromkeys(tables, -1)
    assert health.cdc_lag_verified == dict.fromkeys(tables, False)
    assert set(health.details["checks"].values()) <= {"ready", "unverified"}
    assert not _lag_statements(client)
    assert not any(
        "modification_time" in c.args[0] for c in client.execute_read.call_args_list
    )
    assert checker.get_cdc_lag() == dict.fromkeys(tables, -1)


@pytest.mark.unit
def test_small_stale_partition_before_oversized_fresh_partition_is_unverified(
    configure,
):
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[
            ("tracer_trace", "202610_1_2_1", 100),
            ("tracer_trace", "202611_3_4_1", 12_000_000),
        ],
        sample=(100, datetime.now(UTC) - timedelta(minutes=10)),
    )
    health = configure(client).get_health_status()
    assert health.status == "healthy"
    assert health.details["checks"]["legacy.tracer_trace"] == "unverified"
    assert health.cdc_lag["tracer_trace"] == -1
    assert health.cdc_lag_verified["tracer_trace"] is False
    assert all("FROM tracer_trace" not in sql for sql, _ in _lag_statements(client))


@pytest.mark.unit
@pytest.mark.parametrize("part", ["202610_9_9_0", "202610_9_9_0_10", "202610_1_9_2"])
@pytest.mark.parametrize("fresh", [False, True])
def test_complete_coverage_verifies_source_lag_not_part_age(configure, part, fresh):
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[("tracer_trace", part, 100)],
        sample=(100, datetime.now(UTC) - timedelta(seconds=5 if fresh else 600)),
    )
    health = configure(client).get_health_status()
    assert health.status == ("healthy" if fresh else "degraded")
    assert health.cdc_lag_verified["tracer_trace"] is True
    if fresh:
        assert 0 <= health.cdc_lag["tracer_trace"] < 60
    else:
        assert health.cdc_lag["tracer_trace"] >= 590
    ((sql, params),) = _lag_statements(client)
    assert params["parts0"] == (part,)
    assert "LIMIT" not in sql and "WHERE 1" not in sql


@pytest.mark.unit
@pytest.mark.parametrize("fresh", [False, True])
def test_truncated_inventory_cannot_verify_even_fresh_samples(
    configure, monkeypatch, fresh
):
    from tracer.services.clickhouse import consistency

    monkeypatch.setattr(consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_PARTS", 2)
    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[("tracer_trace", "all_1_1_0", 10), ("tracer_trace", "all_2_2_0", 10)],
        sample=(20, datetime.now(UTC) - timedelta(seconds=5 if fresh else 600)),
    )
    health = configure(client).get_health_status()
    assert health.status == "healthy"
    assert health.cdc_lag["tracer_trace"] == -1
    assert health.cdc_lag_verified["tracer_trace"] is False
    assert all("FROM tracer_trace" not in sql for sql, _ in _lag_statements(client))


@pytest.mark.unit
@pytest.mark.parametrize(
    "change", ["insert", "merge", "mutation", "truncate", "timeout", "denied"]
)
def test_coverage_recheck_rejects_changed_inventory(configure, change):
    from clickhouse_driver.errors import ServerException

    client = health_tests.fake_client(
        SAMPLED_INVENTORY,
        parts=[("tracer_trace", "all_1_1_0", 100)],
    )
    read = client.execute_read.side_effect
    inventories = 0

    def changing(sql, params=None, **kwargs):
        nonlocal inventories
        rows, columns, elapsed = read(sql, params, **kwargs)
        if "FROM system.parts" in sql:
            inventories += 1
            if inventories == 2:
                if change == "timeout":
                    raise TimeoutError("synthetic recheck timeout")
                if change == "denied":
                    raise ServerException("synthetic revoked metadata grant", code=497)
                rows = {
                    "insert": rows + [("tracer_trace", "all_2_2_0", 20_000_000)],
                    "merge": [("tracer_trace", "all_1_2_1", 20_000_100)],
                    "mutation": [("tracer_trace", "all_1_1_0_2", 100)],
                    "truncate": [],
                }[change]
        return rows, columns, elapsed

    client.execute_read.side_effect = changing
    health = configure(client).get_health_status()
    assert inventories == 2
    assert health.cdc_lag["tracer_trace"] == -1
    assert health.cdc_lag_verified["tracer_trace"] is False
    assert health.status == (
        "degraded" if change in ("timeout", "denied") else "healthy"
    )
    ((sql, params),) = _lag_statements(client)
    assert params["parts0"] == ("all_1_1_0",)
    assert "LIMIT" not in sql


@pytest.mark.unit
@pytest.mark.parametrize("unknown_role", ["legacy", "native"])
@pytest.mark.parametrize("unknown", ["oversized", "empty", "timeout"])
def test_any_unverified_destination_wins_over_verified_lag(
    configure, settings, unknown_role, unknown
):
    settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
    legacy = health_tests.fake_client(SAMPLED_INVENTORY)
    native_columns = {
        **health_tests.NATIVE_COLUMNS,
        "tracer_eval_logger": health_tests.CDC_COLUMNS,
        "system.parts": PART_COLUMNS,
    }
    native = health_tests.fake_client(native_columns)
    columns = SAMPLED_INVENTORY if unknown_role == "legacy" else native_columns
    altered = health_tests.fake_client(
        columns,
        parts=[]
        if unknown == "empty"
        else [("tracer_eval_logger", "all_1_9_2", 12_000_000)],
    )
    if unknown == "timeout":
        altered = health_tests.fake_client(columns)
        original = altered.execute_read.side_effect

        def slow(sql, params=None, **kwargs):
            if "max(_peerdb" in sql:
                raise TimeoutError("synthetic lag timeout")
            return original(sql, params, **kwargs)

        altered.execute_read.side_effect = slow
    if unknown_role == "legacy":
        legacy = altered
    else:
        native = altered
    health = configure(legacy, native).get_health_status()
    assert health.cdc_lag["tracer_eval_logger"] == -1
    assert health.cdc_lag_verified["tracer_eval_logger"] is False
    assert health.status == ("degraded" if unknown == "timeout" else "healthy")


@pytest.mark.unit
def test_verified_mixed_destinations_report_worst_complete_lag(configure, settings):
    settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
    legacy = health_tests.fake_client(SAMPLED_INVENTORY)
    native = health_tests.fake_client(
        {
            **health_tests.NATIVE_COLUMNS,
            "tracer_eval_logger": health_tests.CDC_COLUMNS,
            "system.parts": PART_COLUMNS,
        },
        sample=(1, datetime.now(UTC) - timedelta(minutes=10)),
    )
    health = configure(legacy, native).get_health_status()
    assert health.status == "degraded"
    assert health.cdc_lag["tracer_eval_logger"] >= 590
    assert health.cdc_lag_verified == dict.fromkeys(health_tests.CDC_TABLES, True)


@pytest.mark.unit
def test_data_read_denial_remains_unhealthy_not_metadata_degradation(configure):
    client = health_tests.fake_client(SAMPLED_INVENTORY)
    _deny(client, "max(_peerdb_synced_at)")
    health = configure(client).get_health_status()
    assert health.status == "unhealthy"
    assert set(health.details["checks"].values()) == {"unavailable"}
    assert health.cdc_lag == dict.fromkeys(health_tests.CDC_TABLES, -1)
    assert health.cdc_lag_verified == dict.fromkeys(health_tests.CDC_TABLES, False)
