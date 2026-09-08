"""Read-policy contracts for the legacy/native ClickHouse vector store."""

from __future__ import annotations

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB


def _client(rows=None):
    client = Mock()
    # Test doubles do not need clickhouse-driver socket manipulation.
    client.connection = None
    client.execute.return_value = [] if rows is None else rows
    return client


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


class _BlockingClient:
    connection = None

    def __init__(self, tracker):
        self.tracker = tracker

    def execute(self, _query, _params, *, settings):
        assert settings["max_execution_time"] <= 1
        return self.tracker.execute(settings["max_execution_time"])


def test_native_vector_read_has_finite_policy_and_no_row_scan_cap():
    client = _client([("ok",)])

    rows = ClickHouseVectorDB._execute_read_on_client(
        client,
        "SELECT id FROM embeddings",
        settings={
            "max_rows_to_read": 7,
            "max_threads": 99,
            "max_memory_usage": 2 * 1024 * 1024 * 1024,
        },
    )

    assert rows == [("ok",)]
    assert client.execute.call_args.args == (
        "SELECT id FROM embeddings",
        {},
    )
    settings = client.execute.call_args.kwargs["settings"]
    assert settings["readonly"] == 2
    assert 0 < settings["max_execution_time"] <= 9.5
    assert settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert settings["max_threads"] == 4
    assert settings["max_result_rows"] == 1_000_000
    assert settings["max_result_bytes"] == 512 * 1024 * 1024
    assert settings["result_overflow_mode"] == "throw"
    assert settings["timeout_overflow_mode"] == "throw"
    assert "max_rows_to_read" not in settings


def test_native_vector_read_preserves_every_lower_cap():
    client = _client()

    ClickHouseVectorDB._execute_read_on_client(
        client,
        "SELECT id FROM embeddings",
        settings={
            "max_execution_time": 2,
            "max_memory_usage": 128,
            "max_bytes_to_read": 256,
            "max_threads": 1,
            "max_result_rows": 7,
            "max_result_bytes": 1_024,
        },
    )

    settings = client.execute.call_args.kwargs["settings"]
    assert 0 < settings["max_execution_time"] <= 2
    assert settings["max_memory_usage"] == 128
    assert settings["max_bytes_to_read"] == 256
    assert settings["max_threads"] == 1
    assert settings["max_result_rows"] == 7
    assert settings["max_result_bytes"] == 1_024


def test_public_execute_read_preserves_lower_timeout_and_result_override():
    db = object.__new__(ClickHouseVectorDB)
    db.client = _client()

    db.execute_read(
        "SELECT id FROM embeddings",
        settings={"max_execution_time": 2, "max_result_rows": 3},
        max_result_rows=7,
    )

    settings = db.client.execute.call_args.kwargs["settings"]
    assert 0 < settings["max_execution_time"] <= 2
    assert settings["max_result_rows"] == 3


def test_public_execute_read_uses_same_env_configured_native_client(
    monkeypatch,
    settings,
):
    """A divergent Django analytics database cannot receive vector reads."""

    monkeypatch.setitem(
        settings.CLICKHOUSE,
        "CH_DATABASE",
        "different-analytics-read-database",
    )
    monkeypatch.setenv("CH_HOST", "vector-host")
    monkeypatch.setenv("CH_PORT", "9440")
    monkeypatch.setenv("CH_USERNAME", "vector-user")
    monkeypatch.setenv("CH_PASSWORD", "vector-password")
    monkeypatch.setenv("CH_DATABASE", "vector-write-database")
    native_client = _client([("same-database",)])

    with patch(
        "agentic_eval.core.database.ch_vector.clickhouse_driver.Client",
        return_value=native_client,
    ) as client_factory:
        db = ClickHouseVectorDB()
        rows = db.execute_read("SELECT id FROM embeddings")

    assert rows == [("same-database",)]
    client_factory.assert_called_once_with(
        host="vector-host",
        port=9440,
        user="vector-user",
        password="vector-password",
        database="vector-write-database",
    )
    native_client.execute.assert_called_once()


def test_native_vector_read_rejects_mutation_before_transport():
    client = _client()

    with pytest.raises(RuntimeError, match="Only read statements"):
        ClickHouseVectorDB._execute_read_on_client(
            client,
            "ALTER TABLE embeddings DELETE WHERE 1",
        )

    client.execute.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "WITH 1 AS value INSERT INTO embeddings SELECT value",
        "SELECT 1; DROP TABLE embeddings",
        "EXPLAIN ALTER TABLE embeddings DELETE WHERE 1",
    ],
)
def test_native_vector_read_rejects_disguised_mutation_before_transport(query):
    client = _client()

    with pytest.raises(RuntimeError, match="Only read statements"):
        ClickHouseVectorDB._execute_read_on_client(client, query)

    client.execute.assert_not_called()


def test_native_vector_read_admission_is_bounded(monkeypatch):
    admission = Mock()
    admission.acquire.return_value = False
    monkeypatch.setattr(ClickHouseVectorDB, "_read_admission", admission)
    client = _client()

    with pytest.raises(TimeoutError, match="admission deadline"):
        ClickHouseVectorDB._execute_read_on_client(
            client,
            "SELECT id FROM embeddings",
            settings={"max_execution_time": 0.01},
        )

    client.execute.assert_not_called()
    admission.release.assert_not_called()


def test_vector_reads_on_same_native_client_are_serialized(monkeypatch):
    monkeypatch.setattr(
        ClickHouseVectorDB,
        "_read_admission",
        threading.BoundedSemaphore(4),
    )
    tracker = _ConcurrentReadTracker()
    client = _BlockingClient(tracker)
    start = threading.Barrier(3)

    def execute():
        start.wait()
        return ClickHouseVectorDB._execute_read_on_client(
            client,
            "SELECT 1",
            settings={"max_execution_time": 1},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute) for _ in range(2)]
        start.wait()
        try:
            assert tracker.wait_for_active(1)
            time.sleep(0.05)
            assert tracker.max_active == 1
        finally:
            tracker.release.set()
        assert [future.result() for future in futures] == [[("row",)], [("row",)]]
    assert min(tracker.timeouts) < 0.98


def test_different_vector_clients_respect_process_admission_cap(monkeypatch):
    monkeypatch.setattr(
        ClickHouseVectorDB,
        "_read_admission",
        threading.BoundedSemaphore(2),
    )
    tracker = _ConcurrentReadTracker()
    clients = [_BlockingClient(tracker) for _ in range(3)]
    start = threading.Barrier(4)

    def execute(client):
        start.wait()
        return ClickHouseVectorDB._execute_read_on_client(
            client,
            "SELECT 1",
            settings={"max_execution_time": 1},
        )

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


def test_native_vector_read_temporarily_narrows_transport_timeout():
    class Socket:
        def __init__(self):
            self.timeout = 300.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

    class Connection:
        connected = True
        connect_timeout = 10.0
        send_receive_timeout = 300.0
        socket = Socket()

    class Client:
        connection = Connection()

        def __init__(self):
            self.during_execute = None

        def execute(self, _query, _params, *, settings):
            self.during_execute = (
                self.connection.send_receive_timeout,
                self.connection.socket.timeout,
                settings["max_execution_time"],
            )
            return [(1,)]

    client = Client()
    ClickHouseVectorDB._execute_read_on_client(client, "SELECT 1")

    assert client.during_execute is not None
    assert all(0 < timeout <= 9.5 for timeout in client.during_execute)
    assert client.connection.connect_timeout == 10.0
    assert client.connection.send_receive_timeout == 300.0
    assert client.connection.socket.timeout == 300.0


def test_vector_connect_time_is_subtracted_from_execution_deadline():
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

    class Client:
        connection = Connection()

        def __init__(self):
            self.execution_timeout = None

        def execute(self, _query, _params, *, settings):
            self.execution_timeout = settings["max_execution_time"]
            return [(1,)]

    client = Client()

    assert ClickHouseVectorDB._execute_read_on_client(
        client,
        "SELECT 1",
        settings={"max_execution_time": 0.2},
    ) == [(1,)]
    assert 0 < client.execution_timeout < 0.19
    assert client.connection.connect_timeout == 10.0
    assert client.connection.send_receive_timeout == 300.0
    assert client.connection.socket.timeout == 300.0


@pytest.mark.parametrize(
    "method_name",
    [
        "get_or_create_collection",
        "fetch_vector_by_id",
        "fetch_all_vectors",
        "fetch_vectors_by_query",
        "vector_similarity_search_with_threshold",
        "vector_similarity_search",
        "get_num_vectors",
        "get_random_examples",
    ],
)
def test_every_vector_read_helper_uses_guarded_transport(method_name):
    source = inspect.getsource(getattr(ClickHouseVectorDB, method_name))

    assert "self.execute_read(" in source
    assert "self.client.execute(" not in source


def test_cluster_probe_uses_guarded_transport_but_writes_remain_native():
    probe_source = inspect.getsource(ClickHouseVectorDB.is_clustered)
    upsert_source = inspect.getsource(ClickHouseVectorDB.upsert_vector)
    bulk_upsert_source = inspect.getsource(ClickHouseVectorDB.bulk_upsert_vectors)

    assert "_execute_read_on_client(" in probe_source
    assert "client.execute(" not in probe_source
    assert "self.client.execute(" in upsert_source
    assert "self.execute_read(" not in upsert_source
    assert "self.client.execute(" in bulk_upsert_source
    assert "self.execute_read(" not in bulk_upsert_source
