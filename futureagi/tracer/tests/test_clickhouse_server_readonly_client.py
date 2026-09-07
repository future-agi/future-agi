"""Contracts for server-locked read-only ClickHouse connections."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from tracer.services.clickhouse import client as client_module
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.server_readonly import (
    ServerEnforcedReadOnlyNativeClient,
    _NativeBlockStream,
    ensure_read_statement,
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


def _client(
    *,
    server_enforced_readonly: bool,
    read_timeout_ceiling_ms: int | None = None,
    allow_query_settings_with_server_readonly: bool = False,
) -> ClickHouseClient:
    return ClickHouseClient(
        host="clickhouse.invalid",
        port=9000,
        user="readonly",
        password="",
        database="futureagi",
        server_enforced_readonly=server_enforced_readonly,
        read_timeout_ceiling_ms=read_timeout_ceiling_ms,
        allow_query_settings_with_server_readonly=(
            allow_query_settings_with_server_readonly
        ),
    )


@pytest.mark.parametrize("server_enforced_readonly", [False, True])
def test_default_read_timeout_ceiling_uses_interactive_analytics_default(
    monkeypatch, server_enforced_readonly
):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=server_enforced_readonly)
    execute = Mock(return_value=([], []))
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())
    monkeypatch.setattr(client, "_execute_native_read_with_remaining_timeout", execute)

    client.execute_read("SELECT 1", timeout_ms=30_000)

    assert (
        execute.call_args.kwargs["timeout_ms"]
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


def test_reviewed_read_timeout_ceiling_allows_30000_for_dedicated_client(
    monkeypatch,
):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(
        server_enforced_readonly=True,
        read_timeout_ceiling_ms=30_000,
    )
    execute = Mock(return_value=([], []))
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())
    monkeypatch.setattr(client, "_execute_native_read_with_remaining_timeout", execute)

    client.execute_read("SELECT 1", timeout_ms=30_000)

    assert execute.call_args.kwargs["timeout_ms"] == 30_000


@pytest.mark.parametrize(
    "ceiling_ms",
    [True, 0, django_settings.CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS + 1],
)
def test_read_timeout_ceiling_rejects_unreviewed_values(ceiling_ms):
    with pytest.raises(ValueError, match="ceiling"):
        _client(
            server_enforced_readonly=True,
            read_timeout_ceiling_ms=ceiling_ms,
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


def test_server_locked_read_can_send_bounded_query_settings(monkeypatch):
    native = Mock()
    native.execute.return_value = ([("ok",)], [("value", "String")])
    client = _client(
        server_enforced_readonly=True,
        allow_query_settings_with_server_readonly=True,
    )
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    rows, columns, _ = client.execute_read(
        "SELECT 'ok' AS value\nSETTINGS max_threads = 8",
        timeout_ms=250,
        settings={
            "readonly": 1,
            "max_threads": 1,
            "max_memory_usage": 1024,
            "max_result_rows": 2,
            "max_result_bytes": 4096,
        },
    )

    assert rows == [("ok",)]
    assert columns == [("value", "String")]
    assert native.execute.call_args.args[0] == "SELECT 'ok' AS value"
    assert native.execute.call_args.kwargs["settings"] == {
        "max_threads": 1,
        "max_memory_usage": 1024,
        "max_result_rows": 2,
        "max_result_bytes": 4096,
        "max_bytes_to_read": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES,
        "result_overflow_mode": "throw",
        "max_execution_time": 0.25,
    }


def test_regular_read_keeps_client_side_guardrails(monkeypatch):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    client.execute_read(
        "SELECT 1",
        timeout_ms=250,
        settings={
            "max_threads": 1,
            "max_rows_to_read": 1,
            "max_memory_usage": 2 * 1024 * 1024 * 1024,
        },
    )

    assert native.execute.call_args.kwargs["settings"] == {
        "max_threads": 1,
        "max_memory_usage": 2 * 1024 * 1024 * 1024,
        "max_bytes_to_read": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES,
        "max_result_rows": 1_000_000,
        "max_result_bytes": 512 * 1024 * 1024,
        "result_overflow_mode": "throw",
        "readonly": 2,
        "max_execution_time": 0.25,
    }


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (2, 2),
        (8, 8),
        (64, 8),
    ],
)
def test_regular_read_allows_only_explicit_threads_up_to_eight(
    monkeypatch, requested, expected
):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    client.execute_read("SELECT 1", settings={"max_threads": requested})

    assert native.execute.call_args.kwargs["settings"]["max_threads"] == expected


@pytest.mark.parametrize("server_enforced_readonly", [False, True])
def test_read_temporarily_clamps_pooled_socket_to_remaining_wall(
    monkeypatch, server_enforced_readonly
):
    socket = Mock()
    socket.gettimeout.return_value = 300.0
    connection = SimpleNamespace(
        connected=True,
        connect_timeout=10.0,
        send_receive_timeout=300.0,
        socket=socket,
    )
    native = Mock(connection=connection)
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=server_enforced_readonly)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    client.execute_read("SELECT 1", timeout_ms=250)

    narrowed_timeout = socket.settimeout.call_args_list[0].args[0]
    assert 0 < narrowed_timeout <= 0.25
    assert socket.settimeout.call_args_list[-1] == call(300.0)
    assert connection.connect_timeout == 10.0
    assert connection.send_receive_timeout == 300.0


@pytest.mark.parametrize("server_enforced_readonly", [False, True])
def test_read_fails_before_transport_when_admission_wall_is_exhausted(
    monkeypatch, server_enforced_readonly
):
    client = _client(server_enforced_readonly=server_enforced_readonly)
    admission = Mock()
    admission.acquire.return_value = False
    client._read_admission = admission
    get_client = Mock()
    monkeypatch.setattr(client, "_get_client", get_client)

    with pytest.raises(TimeoutError, match="admission deadline"):
        client.execute_read("SELECT 1", timeout_ms=25)

    admission.acquire.assert_called_once_with(timeout=0.025)
    admission.release.assert_not_called()
    get_client.assert_not_called()


def test_regular_read_does_not_revive_exhausted_timeout(monkeypatch):
    native = Mock()
    native.execute.return_value = ([], [])
    client = _client(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(client, "_return_client", Mock())

    client.execute_read("SELECT 1", timeout_ms=0)

    assert native.execute.call_args.kwargs["settings"]["max_execution_time"] == 0.001


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
        "max_memory_usage": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES,
        "max_bytes_to_read": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES,
        "max_result_rows": 1_000_000,
        "max_result_bytes": 512 * 1024 * 1024,
        "result_overflow_mode": "throw",
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


def test_long_read_is_clamped_to_application_read_policy(monkeypatch):
    native = Mock()
    native.execute.return_value = ([], [])
    driver = Mock(return_value=native)
    monkeypatch.setattr(client_module, "CHDriver", driver)
    monkeypatch.setattr(client_module, "CLICKHOUSE_AVAILABLE", True)
    client = _client(server_enforced_readonly=False)
    get_pooled_client = Mock(return_value=native)
    monkeypatch.setattr(client, "_get_client", get_pooled_client)
    return_pooled_client = Mock()
    monkeypatch.setattr(client, "_return_client", return_pooled_client)

    client.execute_read("SELECT 1", timeout_ms=1_200_000)

    get_pooled_client.assert_called_once_with()
    return_pooled_client.assert_called_once_with(native)
    driver.assert_not_called()
    assert native.execute.call_args.kwargs["settings"] == {
        "max_memory_usage": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES,
        "max_bytes_to_read": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES,
        "max_threads": django_settings.CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS,
        "max_result_rows": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_RESULT_ROWS,
        "max_result_bytes": django_settings.CLICKHOUSE_APPLICATION_READ_MAX_RESULT_BYTES,
        "result_overflow_mode": "throw",
        "readonly": 2,
        "max_execution_time": (
            django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS / 1_000
        ),
    }


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


@pytest.mark.parametrize(
    "query",
    [
        "WITH 1 AS value INSERT INTO spans SELECT value",
        "SELECT 1; DROP TABLE spans",
        "EXPLAIN INSERT INTO spans SELECT 1",
        "EXPLAIN WITH 1 AS value INSERT INTO spans SELECT value",
        "SELECT 1;;",
    ],
)
def test_read_statement_guard_rejects_disguised_mutations(query):
    with pytest.raises(RuntimeError, match="Only read statements"):
        ensure_read_statement(query)


@pytest.mark.parametrize(
    "query",
    [
        "WITH 1 AS value SELECT value",
        "EXPLAIN PIPELINE SELECT 1",
        "SHOW CREATE TABLE spans",
        "SELECT ';' AS delimiter; -- trailing comment",
    ],
)
def test_read_statement_guard_preserves_single_read_queries(query):
    ensure_read_statement(query)


def test_server_locked_execute_iter_is_blocked_before_acquiring_connection(
    monkeypatch,
):
    client = _client(server_enforced_readonly=True)
    get_client = Mock()
    monkeypatch.setattr(client, "_get_client", get_client)

    with pytest.raises(RuntimeError, match="managed native block stream"):
        client.execute_iter("SELECT 1")

    get_client.assert_not_called()


def _managed_stream_client(monkeypatch, rows):
    socket = Mock()
    socket.gettimeout.return_value = 300.0
    transport = SimpleNamespace(
        connected=True,
        connect_timeout=10.0,
        send_receive_timeout=300.0,
        socket=socket,
    )
    connection = Mock()
    connection.connection = transport
    connection.execute_iter.return_value = iter(rows)
    client = _client(server_enforced_readonly=True)
    admission = Mock()
    admission.acquire.return_value = True
    client._read_admission = admission
    monkeypatch.setattr(client, "_get_client", Mock(return_value=connection))
    monkeypatch.setattr(client, "_return_client", Mock())
    return client, admission, connection, transport, socket


def test_native_block_stream_returns_connection_only_after_full_exhaustion(
    monkeypatch,
):
    client, admission, connection, transport, socket = _managed_stream_client(
        monkeypatch, [(1,), (2,)]
    )

    with client.execute_read_block_stream(
        "SELECT 1 SETTINGS max_threads = 8",
        timeout_ms=250,
        block_size=1,
    ) as blocks:
        assert list(blocks) == [[(1,)], [(2,)]]

    assert 0 < admission.acquire.call_args.kwargs["timeout"] <= 0.25
    admission.release.assert_called_once_with()
    client._return_client.assert_called_once_with(connection)
    connection.execute_iter.assert_called_once_with("SELECT 1", {})
    connection.disconnect.assert_not_called()
    assert 0 < socket.settimeout.call_args_list[0].args[0] <= 0.25
    assert socket.settimeout.call_args_list[-1] == call(300.0)
    assert transport.connect_timeout == 10.0
    assert transport.send_receive_timeout == 300.0


def test_native_block_stream_clamps_long_request_to_application_wall(monkeypatch):
    client, admission, _, _, socket = _managed_stream_client(monkeypatch, [(1,)])

    with client.execute_read_block_stream("SELECT 1", timeout_ms=1_200_000) as blocks:
        assert list(blocks) == [[(1,)]]

    wall_seconds = django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS / 1_000
    assert 0 < admission.acquire.call_args.kwargs["timeout"] <= wall_seconds
    assert 0 < socket.settimeout.call_args_list[0].args[0] <= wall_seconds


def test_native_block_stream_retires_connection_when_consumer_stops_early(
    monkeypatch,
):
    client, admission, connection, _, _ = _managed_stream_client(
        monkeypatch, [(1,), (2,)]
    )

    with client.execute_read_block_stream(
        "SELECT 1", timeout_ms=250, block_size=1
    ) as blocks:
        assert next(blocks) == [(1,)]

    client._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()
    admission.release.assert_called_once_with()


def test_native_block_stream_restores_blocking_socket_before_pooling(monkeypatch):
    client, _, connection, _, socket = _managed_stream_client(monkeypatch, [(1,)])
    socket.gettimeout.return_value = None

    with client.execute_read_block_stream("SELECT 1", timeout_ms=250) as blocks:
        assert list(blocks) == [[(1,)]]

    assert 0 < socket.settimeout.call_args_list[0].args[0] <= 0.25
    assert socket.settimeout.call_args_list[-1] == call(None)
    client._return_client.assert_called_once_with(connection)


def test_native_block_stream_clamps_lazy_connect_and_restores_new_socket(monkeypatch):
    client, _, connection, transport, socket = _managed_stream_client(monkeypatch, [])
    transport.connected = False
    transport.socket = None

    def rows():
        assert 0 < transport.connect_timeout <= 0.25
        assert 0 < transport.send_receive_timeout <= 0.25
        transport.connected = True
        transport.socket = socket
        yield (1,)

    connection.execute_iter.return_value = rows()

    with client.execute_read_block_stream("SELECT 1", timeout_ms=250) as blocks:
        assert list(blocks) == [[(1,)]]

    assert socket.settimeout.call_args_list[-1] == call(300.0)
    assert transport.connect_timeout == 10.0
    assert transport.send_receive_timeout == 300.0
    client._return_client.assert_called_once_with(connection)


def test_native_block_stream_retires_when_transport_cannot_be_restored(
    monkeypatch,
):
    client, admission, connection, _, socket = _managed_stream_client(
        monkeypatch, [(1,)]
    )
    socket.settimeout.side_effect = [
        None,
        None,
        None,
        None,
        RuntimeError("closed socket"),
    ]

    with client.execute_read_block_stream("SELECT 1", timeout_ms=250) as blocks:
        assert list(blocks) == [[(1,)]]

    client._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()
    admission.release.assert_called_once_with()


def test_native_block_stream_retires_connection_when_iterator_raises(monkeypatch):
    def rows():
        yield (1,)
        raise RuntimeError("native stream failed")

    client, admission, connection, _, _ = _managed_stream_client(monkeypatch, rows())

    with pytest.raises(RuntimeError, match="native stream failed"):
        with client.execute_read_block_stream(
            "SELECT 1", timeout_ms=250, block_size=1
        ) as blocks:
            list(blocks)

    client._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()
    admission.release.assert_called_once_with()


def test_native_block_stream_logs_disconnect_failure_without_surfacing(monkeypatch):
    client, admission, connection, _, _ = _managed_stream_client(
        monkeypatch, [(1,), (2,)]
    )
    connection.disconnect.side_effect = RuntimeError("disconnect failed")
    warning = Mock()
    monkeypatch.setattr(client_module.logger, "warning", warning)

    with client.execute_read_block_stream(
        "SELECT 1", timeout_ms=250, block_size=1
    ) as blocks:
        assert next(blocks) == [(1,)]

    warning.assert_called_once_with(
        "server_readonly_native_disconnect_failed",
        error_type="RuntimeError",
        exc_info=True,
    )
    admission.release.assert_called_once_with()


def test_native_block_stream_fails_before_connection_when_admission_is_saturated(
    monkeypatch,
):
    client = _client(server_enforced_readonly=True)
    admission = Mock()
    admission.acquire.return_value = False
    client._read_admission = admission
    get_client = Mock()
    monkeypatch.setattr(client, "_get_client", get_client)

    with pytest.raises(TimeoutError, match="admission deadline"):
        with client.execute_read_block_stream("SELECT 1", timeout_ms=25):
            pass

    assert 0 < admission.acquire.call_args.kwargs["timeout"] <= 0.025
    admission.release.assert_not_called()
    get_client.assert_not_called()


def test_native_block_stream_rejects_mutation_before_admission(monkeypatch):
    client = _client(server_enforced_readonly=True)
    acquire = Mock()
    client._read_admission = Mock(acquire=acquire)

    with pytest.raises(RuntimeError, match="Only read statements"):
        client.execute_read_block_stream("INSERT INTO spans SELECT 1")

    acquire.assert_not_called()


def test_native_block_stream_enforces_one_wall_during_iteration(monkeypatch):
    clock = {"now": 0.0}

    def rows():
        yield (1,)
        clock["now"] = 0.026
        yield (2,)

    client, admission, connection, _, _ = _managed_stream_client(monkeypatch, rows())
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock["now"])

    with pytest.raises(TimeoutError, match="read deadline"):
        with client.execute_read_block_stream(
            "SELECT 1", timeout_ms=25, block_size=1
        ) as blocks:
            assert next(blocks) == [(1,)]
            next(blocks)

    client._return_client.assert_not_called()
    connection.disconnect.assert_called_once_with()
    admission.release.assert_called_once_with()


def test_server_locked_stream_adapter_discards_settings(monkeypatch):
    core = Mock()
    managed = Mock()
    core.execute_read_block_stream.return_value = managed
    proxy = object.__new__(ServerEnforcedReadOnlyNativeClient)
    proxy._client = core

    stream = proxy.query_row_block_stream(
        "SELECT 1 SETTINGS max_threads = 8",
        parameters={"value": 1},
        settings={"max_threads": 8},
    )

    assert isinstance(stream, _NativeBlockStream)
    core.execute_read_block_stream.assert_called_once_with(
        "SELECT 1 SETTINGS max_threads = 8",
        {"value": 1},
        block_size=8192,
    )


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
