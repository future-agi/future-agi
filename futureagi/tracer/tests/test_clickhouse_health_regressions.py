"""Health regressions for retained readers, verified sidecars and stalled queries."""

from contextlib import contextmanager
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
@pytest.mark.parametrize("fails", [False, True])
def test_bounded_reads_also_clamp_and_restore_native_ping_timeout(fails):
    from tracer.services.clickhouse.client import ClickHouseClient

    socket = Mock()
    socket.gettimeout.return_value = 300
    connection = SimpleNamespace(
        connected=True,
        connect_timeout=10,
        send_receive_timeout=300,
        sync_request_timeout=5,
        socket=socket,
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
                ClickHouseClient._execute_native_read_with_remaining_timeout(
                    driver,
                    "SELECT 1",
                    {},
                    query_settings=None,
                    started_at=monotonic(),
                    timeout_ms=1000,
                )
        else:
            assert ClickHouseClient._execute_native_read_with_remaining_timeout(
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
        assert checker.get_health_status().status == "healthy"

    original_get = client._get_client
    patched = set()
    delayed = False
    needle = {"ping": "SELECT 1", "metadata": "system.columns", "lag": "max(_peerdb"}[
        stage
    ]

    def get_client():
        driver = original_get()
        if id(driver) not in patched:
            patched.add(id(driver))
            execute = driver.execute

            def execute_with_delay(sql, *args, **kwargs):
                nonlocal delayed
                if needle in sql and not delayed:
                    delayed = True
                    execute("SELECT sleep(3)", settings=kwargs.get("settings"))
                return execute(sql, *args, **kwargs)

            monkeypatch.setattr(driver, "execute", execute_with_delay)
        return driver

    monkeypatch.setattr(client, "_get_client", get_client)
    started = monotonic()
    with application_read_context():
        result = health_tests.response()
    elapsed = monotonic() - started
    assert delayed, "The intended real query was not exercised"
    assert result.status_code == 200
    assert result.data["status"] == "unhealthy", result.data
    assert elapsed < 2.5, f"{stage} health query took {elapsed:.3f}s"
    assert health_tests.response().data["status"] == "healthy"
    driver = client._get_client()
    try:
        assert driver.connection.socket.gettimeout() == 300
        assert driver.connection.send_receive_timeout == 300
        assert driver.connection.sync_request_timeout == 5
    finally:
        client._return_client(driver)
