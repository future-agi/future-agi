"""Contracts for server-locked read-only ClickHouse connections."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.test import override_settings

from tracer.services.clickhouse import client as client_module
from tracer.services.clickhouse import server_readonly as server_readonly_module
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.server_readonly import (
    ServerEnforcedReadOnlyNativeClient,
    _NativeBlockStream,
    without_query_settings,
)
from tracer.services.clickhouse.v2.span_reader import CHSpanReader


@override_settings(
    CLICKHOUSE={"CH_SERVER_ENFORCED_READONLY": True},
    CLICKHOUSE_V2={"CH25_SERVER_ENFORCED_READONLY": None},
)
def test_v2_config_inherits_legacy_server_locked_profile(monkeypatch):
    from tracer.services.clickhouse.v2 import get_v2_config

    monkeypatch.delenv("CH25_SERVER_ENFORCED_READONLY", raising=False)

    assert get_v2_config()["server_enforced_readonly"] is True


@override_settings(
    CLICKHOUSE={"CH_SERVER_ENFORCED_READONLY": True},
    CLICKHOUSE_V2={"CH25_SERVER_ENFORCED_READONLY": False},
)
def test_v2_config_explicit_false_overrides_legacy_server_locked_profile(
    monkeypatch,
):
    from tracer.services.clickhouse.v2 import get_v2_config

    monkeypatch.delenv("CH25_SERVER_ENFORCED_READONLY", raising=False)

    assert get_v2_config()["server_enforced_readonly"] is False


def _client(*, server_enforced_readonly: bool) -> ClickHouseClient:
    return ClickHouseClient(
        host="clickhouse.invalid",
        port=9000,
        user="readonly",
        password="",
        database="futureagi",
        server_enforced_readonly=server_enforced_readonly,
    )


def test_server_locked_client_sends_no_connection_settings(monkeypatch):
    driver = Mock(return_value=Mock())
    monkeypatch.setattr(client_module, "CHDriver", driver)
    monkeypatch.setattr(client_module, "CLICKHOUSE_AVAILABLE", True)

    _client(server_enforced_readonly=True)._create_client()

    assert driver.call_args.kwargs["settings"] is None


def test_server_locked_read_sends_no_query_setting_overrides(monkeypatch):
    native = Mock()
    native.execute.return_value = ([("ok",)], [("value", "String")])
    client = _client(server_enforced_readonly=True)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    rows, columns, _ = client.execute_read(
        "SELECT 'ok' AS value\nSETTINGS max_threads = 1",
        timeout_ms=250,
        settings={"max_threads": 1, "max_memory_usage": 1024},
    )

    assert rows == [("ok",)]
    assert columns == [("value", "String")]
    assert native.execute.call_args.kwargs["settings"] is None
    assert native.execute.call_args.args[0] == "SELECT 'ok' AS value"


def test_regular_read_keeps_client_side_guardrails(monkeypatch):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    client.execute_read(
        "SELECT 1",
        timeout_ms=250,
        settings={"max_threads": 1},
    )

    assert native.execute.call_args.kwargs["settings"] == {
        "max_threads": 1,
        "readonly": 2,
        "max_execution_time": 0.25,
    }


def test_progress_read_adds_native_rows_and_bytes_without_changing_read_api(
    monkeypatch,
):
    native = Mock()
    native.execute.return_value = ([("ok",)], [("value", "String")])
    native.last_query = SimpleNamespace(
        progress=SimpleNamespace(rows=148_494, bytes=595_674_646)
    )
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    result = client.execute_read_with_progress(
        "SELECT 'ok' AS value",
        timeout_ms=2_500,
        settings={"max_threads": 1},
    )

    assert result[:2] == ([("ok",)], [("value", "String")])
    assert result[3:] == (148_494, 595_674_646)


class _ClickHouseReadError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"ClickHouse error {code}")


def test_regular_read_retries_transient_admission_without_mutating_settings(
    monkeypatch,
):
    native = Mock()
    native.execute.side_effect = [
        _ClickHouseReadError(202),
        _ClickHouseReadError(202),
        ([("ok",)], [("value", "String")]),
    ]
    client = _client(server_enforced_readonly=False)
    return_client = Mock()
    monkeypatch.setattr(client_module, "CHError", _ClickHouseReadError)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", return_client)
    clock = iter([0.0, 0.01, 0.02, 0.04, 0.05, 0.10, 0.11])
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(clock))
    sleep = Mock()
    monkeypatch.setattr(client_module.time, "sleep", sleep)
    requested_settings = {"max_threads": 1}

    rows, columns, _ = client.execute_read(
        "SELECT 1",
        timeout_ms=1_000,
        settings=requested_settings,
    )

    assert rows == [("ok",)]
    assert columns == [("value", "String")]
    assert requested_settings == {"max_threads": 1}
    assert native.execute.call_count == 3
    assert native.execute.call_args_list[0].kwargs["settings"] == {
        "max_threads": 1,
        "readonly": 2,
        "max_execution_time": 1.0,
    }
    assert (
        native.execute.call_args_list[1].kwargs["settings"]["max_execution_time"] < 1.0
    )
    assert (
        native.execute.call_args_list[2].kwargs["settings"]["max_execution_time"]
        < native.execute.call_args_list[1].kwargs["settings"]["max_execution_time"]
    )
    assert sleep.call_args_list == [call(0.025), call(0.075)]
    return_client.assert_called_once_with(native)


def test_regular_read_does_not_retry_admission_past_deadline(monkeypatch):
    native = Mock()
    native.execute.side_effect = _ClickHouseReadError(202)
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client_module, "CHError", _ClickHouseReadError)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())
    clock = iter([0.0, 0.09, 0.10])
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(clock))
    sleep = Mock()
    monkeypatch.setattr(client_module.time, "sleep", sleep)

    with pytest.raises(_ClickHouseReadError):
        client.execute_read("SELECT 1", timeout_ms=100)

    native.execute.assert_called_once()
    sleep.assert_not_called()


def test_regular_read_does_not_retry_non_admission_error(monkeypatch):
    native = Mock()
    native.execute.side_effect = _ClickHouseReadError(159)
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client_module, "CHError", _ClickHouseReadError)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())
    sleep = Mock()
    monkeypatch.setattr(client_module.time, "sleep", sleep)

    with pytest.raises(_ClickHouseReadError):
        client.execute_read("SELECT 1", timeout_ms=1_000)

    native.execute.assert_called_once()
    sleep.assert_not_called()


def test_long_read_uses_matching_disposable_native_transport(monkeypatch):
    native = Mock()
    native.execute.return_value = ([], [])
    driver = Mock(return_value=native)
    monkeypatch.setattr(client_module, "CHDriver", driver)
    monkeypatch.setattr(client_module, "CLICKHOUSE_AVAILABLE", True)
    client = _client(server_enforced_readonly=False)
    get_pooled_client = Mock()
    monkeypatch.setattr(client, "_get_client", get_pooled_client)
    return_pooled_client = Mock()
    monkeypatch.setattr(client, "_return_client", return_pooled_client)

    client.execute_read("SELECT 1", timeout_ms=1_200_000)

    get_pooled_client.assert_not_called()
    return_pooled_client.assert_not_called()
    assert driver.call_args.kwargs["send_receive_timeout"] == 1_205.0
    assert native.execute.call_args.kwargs["settings"]["max_execution_time"] == 1200
    native.disconnect.assert_called_once_with()


def test_query_settings_stripper_preserves_nested_literals_and_format():
    sql = """SELECT 'SETTINGS max_threads = 9' AS value,
       (SELECT settings FROM config WHERE settings = 1) AS nested
SETTINGS max_threads = 1, max_memory_usage = 1024
FORMAT JSON"""

    stripped = without_query_settings(sql)

    assert "'SETTINGS max_threads = 9'" in stripped
    assert "WHERE settings = 1" in stripped
    assert "\nSETTINGS max_threads = 1" not in stripped
    assert stripped.endswith("FORMAT JSON")


def test_server_locked_reader_uses_settings_free_native_transport(monkeypatch):
    native = Mock()
    native.execute_read.return_value = ([], [], 1.0)
    native_factory = Mock(return_value=native)
    monkeypatch.setattr(client_module, "ClickHouseClient", native_factory)

    reader = CHSpanReader(
        host="clickhouse.invalid",
        port=8123,
        username="readonly",
        database="futureagi",
        server_enforced_readonly=True,
        native_port=9000,
    )
    reader.list_by_ids(
        ["span-a"],
        project_id="00000000-0000-4000-8000-000000000001",
    )

    assert native_factory.call_args.kwargs["server_enforced_readonly"] is True
    assert native.execute_read.call_args.kwargs["settings"] is None


def test_server_locked_native_adapter_blocks_mutation_methods(monkeypatch):
    monkeypatch.setattr(client_module, "ClickHouseClient", Mock(return_value=Mock()))
    proxy = ServerEnforcedReadOnlyNativeClient(
        host="clickhouse.invalid",
        port=9000,
        username="readonly",
        password="",
        database="futureagi",
    )

    with pytest.raises(RuntimeError, match="mutation methods are disabled"):
        proxy.insert("spans", [])


def test_server_locked_core_client_rejects_non_read_sql_before_transport(monkeypatch):
    native = Mock()
    client = _client(server_enforced_readonly=True)
    get_client = Mock(return_value=native)
    monkeypatch.setattr(client, "_get_client", get_client)
    monkeypatch.setattr(client, "_return_client", Mock())

    with pytest.raises(RuntimeError, match="Only read statements"):
        client.execute("DROP TABLE spans")

    native.execute.assert_not_called()
    get_client.assert_not_called()


def test_server_locked_execute_iter_is_blocked_before_acquiring_connection(
    monkeypatch,
):
    client = _client(server_enforced_readonly=True)
    get_client = Mock()
    monkeypatch.setattr(client, "_get_client", get_client)

    with pytest.raises(RuntimeError, match="managed native block stream"):
        client.execute_iter("SELECT 1")

    get_client.assert_not_called()


def test_native_block_stream_returns_connection_only_after_full_exhaustion():
    connection = Mock()
    connection.execute_iter.return_value = iter([(1,), (2,)])
    pool = Mock()
    pool._get_client.return_value = connection

    with _NativeBlockStream(pool, "SELECT 1", {}, block_size=1) as blocks:
        assert list(blocks) == [[(1,)], [(2,)]]

    pool._return_client.assert_called_once_with(connection)
    connection.disconnect.assert_not_called()


def test_native_block_stream_retires_connection_when_consumer_stops_early():
    connection = Mock()
    connection.execute_iter.return_value = iter([(1,), (2,)])
    pool = Mock()
    pool._get_client.return_value = connection

    with _NativeBlockStream(pool, "SELECT 1", {}, block_size=1) as blocks:
        assert next(blocks) == [(1,)]

    pool._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()


def test_native_block_stream_retires_connection_when_iterator_raises():
    def rows():
        yield (1,)
        raise RuntimeError("native stream failed")

    connection = Mock()
    connection.execute_iter.return_value = rows()
    pool = Mock()
    pool._get_client.return_value = connection

    with pytest.raises(RuntimeError, match="native stream failed"):
        with _NativeBlockStream(pool, "SELECT 1", {}, block_size=1) as blocks:
            list(blocks)

    pool._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()


def test_native_block_stream_logs_disconnect_failure_without_surfacing(monkeypatch):
    connection = Mock()
    connection.disconnect.side_effect = RuntimeError("disconnect failed")
    warning = Mock()
    monkeypatch.setattr(server_readonly_module.logger, "warning", warning)
    stream = _NativeBlockStream(Mock(), "SELECT 1", {})
    stream._connection = connection

    stream._retire_connection()

    warning.assert_called_once_with(
        "server_readonly_native_disconnect_failed",
        error_type="RuntimeError",
        exc_info=True,
    )
    assert stream._connection is None


@pytest.mark.parametrize(
    "reader_module",
    [
        "tracer.services.clickhouse.v2.trace_session_dict_reader",
        "tracer.services.clickhouse.v2.end_user_dict_reader",
    ],
)
def test_dimension_readers_use_native_transport_for_locked_profile(
    monkeypatch, reader_module
):
    import importlib

    module = importlib.import_module(reader_module)
    module._reset_client()
    config = {
        "host": "clickhouse.invalid",
        "http_port": 8123,
        "tcp_port": 9000,
        "user": "readonly",
        "password": "",
        "database": "futureagi",
        "server_enforced_readonly": True,
    }
    native = Mock()
    native_factory = Mock(return_value=native)
    monkeypatch.setattr(module, "get_v2_config", lambda: config)
    monkeypatch.setattr(
        "tracer.services.clickhouse.server_readonly.ServerEnforcedReadOnlyNativeClient",
        native_factory,
    )

    try:
        assert module._get_client() is native
        assert native_factory.call_args.kwargs["port"] == 9000
    finally:
        module._reset_client()
