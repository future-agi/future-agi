"""Health regressions for retained readers, verified sidecars and stalled queries."""

from contextlib import contextmanager
from datetime import datetime
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
    client = health_tests.fake_client(
        dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    )
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
    assert health.status == "unhealthy"
    assert timeouts == [1000, 500]
    client.execute.assert_not_called()


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
@pytest.mark.parametrize("stage", ["ping", "metadata", "lag"])
@pytest.mark.parametrize("warm_pool", [False, True])
@pytest.mark.parametrize("server_readonly", [False, True])
def test_every_health_query_has_a_short_transport_timeout(
    configure, real_clients, monkeypatch, stage, warm_pool, server_readonly
):
    """A real slow native read must fail promptly, even inside application mode.

    Substitute one three-second statement at the driver boundary, below the
    health checker. This exercises the actual server and socket timeout (not a
    mocked TimeoutError), without disrupting a sidecar used by other tests.
    """
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
    needle = {"ping": "SELECT 1", "metadata": "system.columns", "lag": "max(_peerdb"}[
        stage
    ]

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
    assert result.data["status"] == "unhealthy", result.data
    assert elapsed < 2.5, f"{stage} health query took {elapsed:.3f}s"
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


def _lag_reads(client, table):
    return [
        call.args
        for call in client.execute_read.call_args_list
        if "max(_peerdb_synced_at)" in call.args[0] and f"FROM {table}" in call.args[0]
    ]


@pytest.mark.unit
def test_lag_sample_reads_newest_parts_that_fit_the_budget(configure, monkeypatch):
    from tracer.services.clickhouse import consistency

    # raising=False keeps the regression runnable against the unsampled checker.
    monkeypatch.setattr(
        consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 1_000, raising=False
    )
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    inventory["system.parts"] = PART_COLUMNS
    # Newest first, as the system.parts ORDER BY returns them.
    parts = [
        ("tracer_trace", "just_merged", 5_000),  # oversized: skipped, not a stop
        ("tracer_trace", "fresh", 10),
        ("tracer_trace", "recent", 900),
        ("tracer_trace", "older", 200),  # would overrun the remaining budget
        ("tracer_trace", "oldest", 50),
        ("trace_session", "only", 3),
    ]
    client = health_tests.fake_client(inventory, parts=parts)
    health = configure(client).get_health_status()

    assert health.status == "healthy"
    assert all(0 <= lag < 60 for lag in health.cdc_lag.values())
    sql = [call.args[0] for call in client.execute_read.call_args_list]
    assert sql[0] == "SELECT 1" and "system.columns" in sql[1]
    assert "FROM system.parts" in sql[2] and len(sql) == 6
    trace = _lag_reads(client, "tracer_trace")
    assert [params for _, params in trace] == [{"parts": ("fresh", "recent", "oldest")}]
    assert "WHERE _part IN" in trace[0][0]
    assert [params for _, params in _lag_reads(client, "trace_session")] == [
        {"parts": ("only",)}
    ]
    # No active part: the whole-table aggregate, free on an empty table.
    assert _lag_reads(client, "tracer_eval_logger") == [
        ("SELECT count(), max(_peerdb_synced_at) FROM tracer_eval_logger", None)
    ]


@pytest.mark.unit
@pytest.mark.parametrize("visible", [set(), {"name", "rows"}])
def test_lag_sample_needs_every_part_column_visible(configure, visible):
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    if visible:
        inventory["system.parts"] = visible
    client = health_tests.fake_client(inventory, parts=[("tracer_trace", "fresh", 1)])
    assert configure(client).get_health_status().status == "healthy"
    assert not any(
        "FROM system.parts" in call.args[0]
        for call in client.execute_read.call_args_list
    )
    for table in health_tests.CDC_TABLES:
        assert [sql for sql, _ in _lag_reads(client, table)] == [
            f"SELECT count(), max(_peerdb_synced_at) FROM {table}"
        ]


@pytest.mark.unit
def test_lag_sample_falls_back_when_no_part_fits_or_sample_is_empty(
    configure, monkeypatch
):
    from tracer.services.clickhouse import consistency

    # raising=False keeps the regression runnable against the unsampled checker.
    monkeypatch.setattr(
        consistency.ConsistencyChecker, "LAG_SAMPLE_MAX_ROWS", 100, raising=False
    )
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    inventory["system.parts"] = PART_COLUMNS
    client = health_tests.fake_client(
        inventory,
        parts=[
            ("tracer_trace", "bulk", 1_000),
            ("trace_session", "masked", 5),
            ("tracer_eval_logger", "fresh", 1),
        ],
    )
    read = client.execute_read.side_effect

    def masked_sample(sql, params=None, **kwargs):
        # Parts can hold only masked rows, e.g. after a lightweight delete.
        if "FROM trace_session WHERE _part IN" in sql:
            return [(0, datetime(1970, 1, 1))], [], 0
        return read(sql, params, **kwargs)

    client.execute_read.side_effect = masked_sample
    assert configure(client).get_health_status().status == "healthy"
    assert [sql for sql, _ in _lag_reads(client, "tracer_trace")] == [
        "SELECT count(), max(_peerdb_synced_at) FROM tracer_trace"
    ]
    assert [params for _, params in _lag_reads(client, "trace_session")] == [
        {"parts": ("masked",)},
        None,
    ]
    assert [params for _, params in _lag_reads(client, "tracer_eval_logger")] == [
        {"parts": ("fresh",)}
    ]


@pytest.mark.unit
def test_failed_part_inventory_is_unavailable_not_healthy(configure):
    inventory = dict.fromkeys(health_tests.CDC_TABLES, health_tests.CDC_COLUMNS)
    inventory["system.parts"] = PART_COLUMNS
    client = health_tests.fake_client(inventory)
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
    assert not any(_lag_reads(client, table) for table in health_tests.CDC_TABLES)
