"""Application opt-in must not unbound the legacy client's maintenance lane."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from clickhouse_driver import errors

from tfc.utils import clickhouse as clickhouse_module
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    application_read_context,
    is_application_read,
)


class _Socket:
    def __init__(self, timeout):
        self.timeout = timeout
        self.changes = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value
        self.changes.append(value)


class _Connection:
    def __init__(self, *, connected=True, timeout=300.0):
        self.connected = connected
        self.connect_timeout = 10.0
        self.send_receive_timeout = timeout
        self.socket = _Socket(timeout) if connected else None

    def connect(self):
        self.connected = True
        self.socket = _Socket(self.send_receive_timeout)


class _Driver:
    def __init__(self, **connection_options):
        self.connection = _Connection(**connection_options)
        self.calls = []
        self.failure = None

    def execute(self, query, params=None, **kwargs):
        if not self.connection.connected:
            self.connection.connect()
        self.calls.append(
            (
                query,
                params,
                kwargs,
                self.connection.connect_timeout,
                self.connection.send_receive_timeout,
                self.connection.socket.gettimeout(),
            )
        )
        if self.failure:
            raise self.failure
        return [(23,)] if query.startswith("SELECT count() FROM") else [(1,)]

    def disconnect(self):
        pass


def _client(driver):
    client = ClickHouseClientSingleton.__new__(ClickHouseClientSingleton)
    client._client = driver
    return client


def _assert_application_settings(settings):
    assert all(settings[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
    assert settings["max_memory_usage"] > 0
    assert settings["max_threads"] > 0
    assert settings["readonly"] == 2
    assert settings["read_overflow_mode"] == "throw"
    assert settings["result_overflow_mode"] == "throw"
    assert settings["timeout_overflow_mode"] == "throw"


@pytest.mark.parametrize("entrypoint", ["execute", "execute_read"])
@pytest.mark.parametrize("explicit", [False, True])
def test_application_marker_drops_abort_caps_without_mutating_inputs(
    entrypoint, explicit, settings
):
    settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES = 96 * 1024**3
    settings.CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS = 6
    settings.CLICKHOUSE_APPLICATION_READ_MAX_THREADS = 12
    supplied = dict.fromkeys(UNLIMITED_STATEMENT_SETTINGS, 1)
    supplied.update(max_memory_usage=72 * 1024**3, max_threads=99)
    original = supplied.copy()
    driver = _Driver()
    client = _client(driver)
    kwargs = {"settings": supplied}
    if entrypoint == "execute_read":
        kwargs.update(timeout_ms=1, max_result_rows=1, max_result_bytes=1)

    def execute():
        return getattr(client, entrypoint)("SELECT 1 LIMIT 25", {"id": 7}, **kwargs)

    if explicit:
        kwargs["application_read_policy"] = True
        assert execute() == [(1,)]
    else:
        with application_read_context():
            assert execute() == [(1,)]

    assert not is_application_read()
    assert supplied == original
    sql, params, options, connect, receive, socket = driver.calls[0]
    assert sql == "SELECT 1 LIMIT 25"
    assert params == {"id": 7}
    _assert_application_settings(options["settings"])
    assert options["settings"]["max_memory_usage"] == 72 * 1024**3
    assert options["settings"]["max_threads"] == 12
    assert (connect, receive, socket) == (10.0, 300.0, 300.0)
    assert driver.connection.socket.changes == []


@pytest.mark.parametrize("requested, expected", [(0, 64), (-1, 64), (12, 12), (99, 64)])
def test_application_memory_ceiling_and_default_concurrency(requested, expected, settings):
    settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES = 64 * 1024**3
    settings.CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS = 6
    result = clickhouse_module._normalized_read_settings(
        {"max_memory_usage": requested * 1024**3}, application_read_policy=True
    )
    assert result["max_memory_usage"] == expected * 1024**3
    assert result["max_threads"] == 6


@pytest.mark.parametrize("entrypoint", ["execute", "execute_read", "execute_paginated"])
def test_explicit_false_keeps_maintenance_bounded_inside_application_context(entrypoint):
    driver = _Driver()
    client = _client(driver)
    with application_read_context():
        getattr(client, entrypoint)("SELECT 1", application_read_policy=False)
        assert is_application_read()
    for call in driver.calls:
        settings = call[2]["settings"]
        assert 0 < settings["max_execution_time"] <= 9.5
        assert settings["max_bytes_to_read"] > 0
        assert settings["max_result_rows"] > 0
        assert settings["max_result_bytes"] > 0
        assert 0 < call[5] <= 9.5
    assert driver.connection.socket.gettimeout() == 300.0


def test_health_check_stays_bounded_and_next_application_read_is_unlimited():
    driver = _Driver()
    client = _client(driver)
    with application_read_context():
        assert client.is_connection_alive()
        client.execute_read("SELECT 2")
    health = driver.calls[0][2]["settings"]
    assert 0 < health["max_execution_time"] <= 1
    assert health["max_result_rows"] == 1
    assert 0 < driver.calls[0][5] <= 1
    _assert_application_settings(driver.calls[1][2]["settings"])
    assert driver.calls[1][5] == 300.0


@pytest.mark.parametrize("explicit", [False, True])
def test_paginated_application_reads_keep_sql_count_and_page_limits(explicit):
    driver = _Driver()
    client = _client(driver)
    if explicit:
        result = client.execute_paginated(
            "SELECT value FROM events", {"id": 7}, 2, 10, application_read_policy=True
        )
    else:
        with application_read_context():
            result = client.execute_paginated("SELECT value FROM events", {"id": 7}, 2, 10)
    assert result == ([(1,)], 3)
    assert [call[0] for call in driver.calls] == [
        "SELECT count() FROM (SELECT value FROM events)",
        "SELECT value FROM events LIMIT 10 OFFSET 10",
    ]
    for call in driver.calls:
        _assert_application_settings(call[2]["settings"])
        assert call[1] == {"id": 7}


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("timeout", [None, 300.0])
def test_lazy_and_reused_transports_are_not_given_a_statement_wall(connected, timeout):
    driver = _Driver(connected=connected, timeout=timeout)
    assert _client(driver).execute_read("SELECT 1", application_read_policy=True) == [(1,)]
    assert driver.calls[0][3:] == (10.0, timeout, timeout)
    assert driver.connection.socket.changes == []
    assert driver.connection.send_receive_timeout == timeout


@pytest.mark.parametrize("failure", [errors.NetworkError("network"), RuntimeError("bug")])
def test_application_error_propagates_without_retry_or_policy_leak(failure, monkeypatch):
    driver = _Driver()
    driver.failure = failure
    client = _client(driver)
    admission = threading.BoundedSemaphore(1)
    monkeypatch.setattr(clickhouse_module, "_APPLICATION_READ_ADMISSION", admission)
    with pytest.raises(type(failure)) as caught, application_read_context():
        client.execute_read("SELECT 1")
    assert caught.value is failure
    assert len(driver.calls) == 1
    assert not is_application_read()
    assert admission.acquire(blocking=False)
    admission.release()
    lock = clickhouse_module._native_client_read_lock(driver)
    assert lock.acquire(blocking=False)
    lock.release()
    driver.failure = None
    client.execute_read("SELECT 1", timeout_ms=123, max_result_rows=2)
    assert 0 < driver.calls[1][2]["settings"]["max_execution_time"] <= 0.123
    assert driver.calls[1][2]["settings"]["max_result_rows"] == 2
    assert driver.connection.socket.gettimeout() == 300.0


@pytest.mark.parametrize("resource", ["admission", "client_lock"])
def test_application_queue_admission_is_separate_and_still_bounded(resource, monkeypatch):
    driver = _Driver()
    gate = Mock()
    gate.acquire.return_value = resource != "admission"
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr(clickhouse_module, "_APPLICATION_READ_ADMISSION", gate)
    monkeypatch.setattr(clickhouse_module, "_native_client_read_lock", lambda _: lock)
    with pytest.raises(TimeoutError):
        _client(driver).execute_read("SELECT 1", application_read_policy=True)
    assert 0 < gate.acquire.call_args.kwargs["timeout"] <= 9.5
    assert not driver.calls
    if resource == "client_lock":
        assert 0 < lock.acquire.call_args.kwargs["timeout"] <= 9.5
        gate.release.assert_called_once_with()
    else:
        lock.acquire.assert_not_called()
        gate.release.assert_not_called()
    lock.release.assert_not_called()


def test_admission_time_does_not_become_an_application_execution_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(clickhouse_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def admit(**kwargs):
        clock.now += 4
        return True

    gate = Mock()
    gate.acquire.side_effect = admit
    monkeypatch.setattr(clickhouse_module, "_APPLICATION_READ_ADMISSION", gate)
    driver = _Driver()
    execute = driver.execute

    def long_query(*args, **kwargs):
        clock.now += 60  # Virtual time only: no sleep or database connection.
        return execute(*args, **kwargs)

    driver.execute = long_query
    assert _client(driver).execute_read("SELECT 1", application_read_policy=True) == [(1,)]
    _assert_application_settings(driver.calls[0][2]["settings"])
    assert driver.calls[0][5] == 300.0
    gate.release.assert_called_once_with()


def test_worker_requires_its_own_marker_and_does_not_inherit_request_context():
    driver = _Driver()
    client = _client(driver)
    with ThreadPoolExecutor(max_workers=1) as executor, application_read_context():
        executor.submit(client.execute_read, "SELECT 1").result()
        executor.submit(client.execute_read, "SELECT 2", application_read_policy=True).result()
        executor.submit(client.execute_read, "SELECT 3").result()
    assert driver.calls[0][2]["settings"]["max_execution_time"] > 0
    _assert_application_settings(driver.calls[1][2]["settings"])
    assert driver.calls[2][2]["settings"]["max_execution_time"] > 0


@pytest.mark.parametrize("entrypoint", ["execute", "execute_read", "execute_paginated"])
def test_invalid_policy_marker_is_not_an_implicit_opt_in(entrypoint):
    driver = _Driver()
    with pytest.raises(TypeError, match="application_read_policy"):
        getattr(_client(driver), entrypoint)("SELECT 1", application_read_policy="false")
    assert not driver.calls


def test_zero_setting_without_marker_does_not_unbound_scripts():
    driver = _Driver()
    _client(driver).execute_read("SELECT 1", settings={"max_execution_time": 0})
    assert 0 < driver.calls[0][2]["settings"]["max_execution_time"] <= 9.5


def test_application_context_does_not_change_mutation_transport_or_read_validation():
    driver = _Driver()
    client = _client(driver)
    with application_read_context():
        client.execute("INSERT INTO events VALUES", [(1,)])
        with pytest.raises(RuntimeError, match="Only read statements"):
            client.execute_read("ALTER TABLE events DELETE WHERE 1")
    assert len(driver.calls) == 1
    assert driver.calls[0][2] == {}
