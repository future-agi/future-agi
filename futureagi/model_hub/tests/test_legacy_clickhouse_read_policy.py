"""Read-policy contract for the legacy model-hub ClickHouse client."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from clickhouse_driver import errors

from tfc.utils import clickhouse as clickhouse_module
from tfc.utils.clickhouse import ClickHouseClientSingleton


class _RecordingDriver:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None, **kwargs):
        self.calls.append((query, params, kwargs))
        if query.startswith("SELECT count() FROM"):
            return [(23,)]
        return [("row",)]

    def disconnect(self):
        return None


class _ConcurrentReadTracker:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.condition = threading.Condition()
        self.release = threading.Event()
        self.timeouts = []

    def execute(self, timeout):
        with self.condition:
            self.timeouts.append(timeout)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.condition.notify_all()
        try:
            assert self.release.wait(timeout=1)
            return [("row",)]
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def wait_for_active(self, count):
        with self.condition:
            return self.condition.wait_for(lambda: self.active >= count, timeout=1)


class _BlockingDriver:
    def __init__(self, tracker):
        self.tracker = tracker

    def execute(self, _query, _params=None, *, settings):
        return self.tracker.execute(settings["max_execution_time"])

    def disconnect(self):
        return None


def _client(driver):
    client = ClickHouseClientSingleton.__new__(ClickHouseClientSingleton)
    client._client = driver
    return client


def test_execute_read_forces_shared_limits_and_removes_row_read_limit():
    driver = _RecordingDriver()
    client = _client(driver)

    assert client.execute_read(
        "SELECT 1",
        timeout_ms=120_000,
        settings={
            "max_rows_to_read": 10,
            "max_memory_usage": 2 * 1024 * 1024 * 1024,
            "max_bytes_to_read": 512 * 1024 * 1024,
            "max_threads": 99,
        },
    ) == [("row",)]

    query_settings = driver.calls[0][2]["settings"]
    assert query_settings["readonly"] == 2
    assert 0 < query_settings["max_execution_time"] <= 9.5
    assert query_settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert query_settings["max_bytes_to_read"] == 512 * 1024 * 1024
    assert query_settings["max_threads"] == 4
    assert query_settings["max_result_rows"] == 100_000
    assert query_settings["max_result_bytes"] == 64 * 1024 * 1024
    assert "max_rows_to_read" not in query_settings


def test_execute_read_rejects_mutations_before_transport():
    driver = _RecordingDriver()
    client = _client(driver)

    with pytest.raises(RuntimeError, match="Only read statements"):
        client.execute_read("ALTER TABLE events DELETE WHERE 1")

    assert driver.calls == []


def test_plain_execute_preserves_mutation_transport_without_readonly_settings():
    driver = _RecordingDriver()
    client = _client(driver)

    client.execute("INSERT INTO events VALUES", [(1,)])

    assert driver.calls == [("INSERT INTO events VALUES", [(1,)], {})]


def test_plain_execute_cannot_bypass_read_policy():
    driver = _RecordingDriver()
    client = _client(driver)

    client.execute(
        "SELECT 1",
        settings={
            "max_rows_to_read": 1,
            "max_memory_usage": 2 * 1024 * 1024 * 1024,
        },
    )

    query_settings = driver.calls[0][2]["settings"]
    assert 0 < query_settings["max_execution_time"] <= 9.5
    assert query_settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert query_settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in query_settings


@pytest.mark.parametrize(
    "query",
    (
        "SELECT 1; DROP TABLE events",
        "WITH doomed AS (SELECT 1) DELETE FROM events WHERE 1",
        "EXPLAIN INSERT INTO events VALUES (1)",
        "not_a_clickhouse_statement",
    ),
)
def test_plain_execute_rejects_invalid_read_prefixed_or_unknown_sql(query):
    driver = _RecordingDriver()
    client = _client(driver)

    with pytest.raises(RuntimeError, match="Only read statements"):
        client.execute(query)

    assert driver.calls == []


def test_execute_paginated_guards_count_and_page_results_separately():
    driver = _RecordingDriver()
    client = _client(driver)

    rows, total_pages = client.execute_paginated(
        "SELECT id FROM events",
        page=2,
        page_size=10,
    )

    assert rows == [("row",)]
    assert total_pages == 3
    assert driver.calls[0][2]["settings"]["max_result_rows"] == 1
    assert driver.calls[1][2]["settings"]["max_result_rows"] == 10
    assert all("max_rows_to_read" not in call[2]["settings"] for call in driver.calls)


def test_execute_read_preserves_tighter_deadline_and_does_not_retry_network_error():
    class NetworkFailingDriver(_RecordingDriver):
        def execute(self, query, params=None, **kwargs):
            self.calls.append((query, params, kwargs))
            raise errors.NetworkError("network")

    driver = NetworkFailingDriver()
    client = _client(driver)

    with pytest.raises(errors.NetworkError):
        client.execute_read(
            "SELECT 1",
            timeout_ms=9_500,
            settings={"max_execution_time": 0.25},
        )

    assert len(driver.calls) == 1
    assert driver.calls[0][2]["settings"]["max_execution_time"] <= 0.25


def test_connect_time_is_subtracted_from_legacy_read_deadline():
    class Socket:
        timeout = 300.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

    class Connection:
        connected = False
        connect_timeout = 10.0
        send_receive_timeout = 300.0
        socket = Socket()

        def connect(self):
            time.sleep(0.04)
            self.socket.settimeout(self.send_receive_timeout)
            self.connected = True

    class Driver:
        connection = Connection()

        def __init__(self):
            self.execution_timeout = None

        def execute(self, _query, _params=None, *, settings):
            self.execution_timeout = settings["max_execution_time"]
            return [(1,)]

    driver = Driver()

    assert _client(driver).execute_read("SELECT 1", timeout_ms=200) == [(1,)]
    assert 0 < driver.execution_timeout < 0.19
    assert driver.connection.connect_timeout == 10.0
    assert driver.connection.send_receive_timeout == 300.0
    assert driver.connection.socket.timeout == 300.0


def test_reads_on_same_native_client_are_serialized_across_wrappers(monkeypatch):
    monkeypatch.setattr(
        clickhouse_module,
        "_APPLICATION_READ_ADMISSION",
        threading.BoundedSemaphore(4),
    )
    tracker = _ConcurrentReadTracker()
    driver = _BlockingDriver(tracker)
    clients = [_client(driver), _client(driver)]
    start = threading.Barrier(3)

    def execute(client):
        start.wait()
        return client.execute_read("SELECT 1", timeout_ms=1_000)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute, client) for client in clients]
        start.wait()
        try:
            assert tracker.wait_for_active(1)
            time.sleep(0.05)
            assert tracker.max_active == 1
        finally:
            tracker.release.set()
        assert [future.result() for future in futures] == [[("row",)], [("row",)]]
    assert min(tracker.timeouts) < 0.98


def test_different_legacy_clients_share_one_process_admission_gate(monkeypatch):
    monkeypatch.setattr(
        clickhouse_module,
        "_APPLICATION_READ_ADMISSION",
        threading.BoundedSemaphore(2),
    )
    tracker = _ConcurrentReadTracker()
    clients = [_client(_BlockingDriver(tracker)) for _ in range(3)]
    start = threading.Barrier(4)

    def execute(client):
        start.wait()
        return client.execute_read("SELECT 1", timeout_ms=1_000)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(execute, client) for client in clients]
        start.wait()
        try:
            assert tracker.wait_for_active(2)
            time.sleep(0.05)
            assert tracker.max_active == 2
        finally:
            tracker.release.set()
        assert [future.result() for future in futures] == [
            [("row",)],
            [("row",)],
            [("row",)],
        ]
    assert min(tracker.timeouts) < 0.98
