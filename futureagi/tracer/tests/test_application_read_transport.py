"""Offline transport contracts, using installed drivers; no server/SLO claims."""

import importlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import clickhouse_connect
import pytest
from clickhouse_connect.driver.client import Client as HttpBaseClient
from clickhouse_connect.driver.models import SettingDef
from django.test import override_settings
from urllib3 import PoolManager

from tracer.services.clickhouse import client as client_module
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    application_read_context,
    application_read_settings,
    is_application_read,
)
from tracer.services.clickhouse.application_read_transport import (
    create_application_read_http_client,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.server_readonly import (
    ServerEnforcedReadOnlyNativeClient,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Disable integration DDL for this offline module."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class Socket:
    def __init__(self, timeout):
        self.timeout = timeout
        self.timeouts = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeouts.append(timeout)
        self.timeout = timeout


def native_client(
    monkeypatch,
    *,
    receive=10,
    socket_timeout=10,
    locked=False,
    allow_settings=False,
    lazy=False,
):
    owner = ClickHouseClient(
        host="clickhouse.invalid",
        pool_size=1,
        connect_timeout=3,
        receive_timeout=receive or 10,
        send_timeout=receive or 10,
        server_enforced_readonly=locked,
        allow_query_settings_with_server_readonly=allow_settings,
    )
    socket = Socket(socket_timeout)
    connection = SimpleNamespace(
        connected=not lazy,
        socket=None if lazy else socket,
        connect_timeout=3,
        send_receive_timeout=receive,
        sync_request_timeout=1,
    )
    native = Mock(connection=connection)
    native.execute.return_value = ([(1,)], [("value", "UInt8")])
    native.execute_iter.return_value = iter([(1,), (2,), (3,)])
    monkeypatch.setattr(owner, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(owner, "_return_client", Mock())
    return owner, native, connection, socket


def assert_unlimited(settings):
    assert all(settings[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
    assert settings["max_memory_usage"] > 0


@pytest.mark.parametrize("receive", [2, 10, 300, None])
@pytest.mark.parametrize("socket_timeout", [2, None])
@pytest.mark.parametrize(
    "locked,allow_settings", [(False, False), (True, False), (True, True)]
)
def test_service_native_unlimited_read_restores_pool_and_keeps_diagnostics_bounded(
    monkeypatch,
    receive,
    socket_timeout,
    locked,
    allow_settings,
):
    owner, native, connection, socket = native_client(
        monkeypatch,
        receive=receive,
        socket_timeout=socket_timeout,
        locked=locked,
        allow_settings=allow_settings,
    )

    def execute(sql, params, **kwargs):
        assert sql == "SELECT 1 LIMIT 25"
        assert connection.connect_timeout == 3
        assert connection.sync_request_timeout == 1
        assert connection.send_receive_timeout is None
        assert socket.gettimeout() is None
        if locked and not allow_settings:
            assert kwargs["settings"] is None
        else:
            assert_unlimited(kwargs["settings"])
            assert kwargs["settings"]["max_memory_usage"] == 123456
            assert kwargs["settings"]["max_threads"] == 2
            assert kwargs["settings"]["max_block_size"] == 2048
            if locked:
                assert "readonly" not in kwargs["settings"]
        return [(1,)], [("value", "UInt8")]

    native.execute.side_effect = execute
    AnalyticsQueryService(ch_client=owner).execute_ch_query(
        "SELECT 1 LIMIT 25",
        timeout_ms=1,
        settings={"max_memory_usage": 123456, "max_threads": 2, "max_block_size": 2048},
    )
    assert not is_application_read()
    assert connection.send_receive_timeout == receive
    assert socket.gettimeout() == socket_timeout
    owner._return_client.assert_called_once_with(native)

    def diagnostic(*args, **kwargs):
        assert 0 < socket.gettimeout() <= 0.123
        if not locked or allow_settings:
            assert kwargs["settings"]["max_execution_time"] == 0.123
            assert kwargs["settings"]["max_result_rows"] == 2
        return [], []

    native.execute.side_effect = diagnostic
    owner.execute_read("SELECT 1", timeout_ms=123, settings={"max_result_rows": 2})
    assert socket.gettimeout() == socket_timeout
    assert connection.send_receive_timeout == receive
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()


@pytest.mark.parametrize("failure", [RuntimeError("read failed"), KeyboardInterrupt()])
def test_native_application_failure_restores_transport_and_admission(
    monkeypatch, failure
):
    owner, native, connection, socket = native_client(monkeypatch)
    native.execute.side_effect = failure
    with pytest.raises(type(failure)):
        with application_read_context():
            owner.execute_read("SELECT 1")
    assert connection.send_receive_timeout == socket.gettimeout() == 10
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()
    assert not is_application_read()


@pytest.mark.parametrize("streaming", [False, True])
def test_native_lazy_application_connection_keeps_finite_connect(
    monkeypatch, streaming
):
    owner, native, connection, socket = native_client(monkeypatch, lazy=True)

    def connect_and_read(*args, **kwargs):
        assert connection.connect_timeout == 3
        assert connection.send_receive_timeout is None
        connection.connected = True
        connection.socket = socket
        socket.settimeout(connection.send_receive_timeout)
        return iter([(1,)]) if streaming else ([(1,)], [("value", "UInt8")])

    native.execute_iter.side_effect = connect_and_read
    native.execute.side_effect = connect_and_read
    with application_read_context():
        if streaming:
            with owner.execute_read_block_stream("SELECT 1") as blocks:
                assert list(blocks) == [[(1,)]]
        else:
            owner.execute_read("SELECT 1")
    assert socket.gettimeout() == connection.send_receive_timeout == 10
    assert connection.connect_timeout == 3


@pytest.mark.parametrize(
    "locked,allow_settings", [(False, False), (True, False), (True, True)]
)
def test_stream_captures_application_mode_across_context_exit(
    monkeypatch, locked, allow_settings
):
    owner, native, connection, socket = native_client(
        monkeypatch,
        locked=locked,
        allow_settings=allow_settings,
    )
    clock = {"now": 0.0}
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock["now"])
    settings = {
        "max_execution_time": 1,
        "max_rows_to_read": 1,
        "max_memory_usage": 123456,
        "max_threads": 2,
        "max_block_size": 2048,
    }
    with application_read_context():
        stream = owner.execute_read_block_stream(
            "SELECT 1 LIMIT 25",
            timeout_ms=1,
            block_size=2,
            settings=settings,
        )
    assert settings["max_rows_to_read"] == 1
    assert not is_application_read()
    clock["now"] = 1000
    with stream as blocks:
        assert next(blocks) == [(1,), (2,)]
        assert socket.gettimeout() is None
        assert connection.send_receive_timeout is None
        assert not owner._read_admission.acquire(blocking=False)
        clock["now"] = 100000
        assert list(blocks) == [[(3,)]]
    if locked and not allow_settings:
        native.execute_iter.assert_called_once_with("SELECT 1 LIMIT 25", {})
    else:
        actual = native.execute_iter.call_args.kwargs["settings"]
        assert_unlimited(actual)
        assert actual["max_memory_usage"] == 123456
        assert actual["max_threads"] == 2
        assert actual["max_block_size"] == 2048
    assert socket.gettimeout() == connection.send_receive_timeout == 10
    assert connection.connect_timeout == 3
    owner._return_client.assert_called_once_with(native)
    native.disconnect.assert_not_called()


def test_diagnostic_stream_created_outside_context_stays_bounded_inside_it(monkeypatch):
    owner, native, connection, socket = native_client(monkeypatch)
    stream = owner.execute_read_block_stream("SELECT 1", timeout_ms=25)
    with application_read_context():
        with stream as blocks:
            assert 0 < socket.gettimeout() <= 0.025
            assert list(blocks) == [[(1,), (2,), (3,)]]
    assert socket.gettimeout() == connection.send_receive_timeout == 10


@pytest.mark.parametrize(
    "failure", [None, RuntimeError("read failed"), KeyboardInterrupt()]
)
def test_application_stream_early_stop_or_error_retires_connection(
    monkeypatch, failure
):
    owner, native, connection, socket = native_client(monkeypatch)

    def consume():
        with application_read_context():
            stream = owner.execute_read_block_stream("SELECT 1", block_size=1)
        with stream as blocks:
            assert next(blocks) == [(1,)]
            if failure is not None:
                raise failure

    if failure is None:
        consume()
    else:
        with pytest.raises(type(failure)):
            consume()
    owner._return_client.assert_not_called()
    native.disconnect.assert_called_once_with()
    assert socket.gettimeout() == connection.send_receive_timeout == 10
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()


def test_application_stream_admission_is_still_bounded(monkeypatch):
    owner, native, _, _ = native_client(monkeypatch)
    admission = Mock(acquire=Mock(return_value=False))
    owner._read_admission = admission
    with application_read_context():
        stream = owner.execute_read_block_stream("SELECT 1")
    with pytest.raises(TimeoutError, match="admission"):
        with stream:
            pytest.fail("admission must reject")
    admission.acquire.assert_called_once_with(
        timeout=owner.read_timeout_ceiling_ms / 1000
    )
    admission.release.assert_not_called()
    owner._get_client.assert_not_called()


@pytest.mark.parametrize("application", [None, False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("outer_mode", [False, True])
def test_locked_adapter_explicit_application_and_diagnostic_modes(
    monkeypatch, application, streaming, outer_mode
):
    owner, native, connection, socket = native_client(monkeypatch, locked=True)
    monkeypatch.setattr(client_module, "ClickHouseClient", Mock(return_value=owner))
    mode_kwargs = {} if application is None else {"application_read": application}
    application = bool(application)
    proxy = ServerEnforcedReadOnlyNativeClient(
        host="clickhouse.invalid",
        port=9000,
        username="readonly",
        password="",
        database="default",
        **mode_kwargs,
    )
    if streaming:
        with application_read_context(outer_mode):
            stream = proxy.query_row_block_stream(
                "SELECT 1 SETTINGS max_execution_time=1"
            )
            assert is_application_read() is outer_mode
        assert not is_application_read()
        with stream as blocks:
            assert (socket.gettimeout() is None) is application
            assert list(blocks) == [[(1,), (2,), (3,)]]
        assert native.execute_iter.call_args.args[0] == "SELECT 1"
        assert not native.execute_iter.call_args.kwargs
    else:

        def read(*args, **kwargs):
            assert (socket.gettimeout() is None) is application
            assert kwargs["settings"] is None
            return [(1,)], []

        native.execute.side_effect = read
        with application_read_context(outer_mode):
            assert proxy.query(
                "SELECT 1 SETTINGS max_execution_time=1"
            ).result_rows == [(1,)]
            assert is_application_read() is outer_mode
    assert not is_application_read()
    assert socket.gettimeout() == connection.send_receive_timeout == 10


@pytest.mark.parametrize("kind", ["spans", "users", "sessions"])
def test_v2_native_constructors_explicitly_opt_into_application_reads(
    monkeypatch, kind
):
    monkeypatch.setattr(client_module, "ClickHouseClient", Mock(return_value=Mock()))
    if kind == "spans":
        from tracer.services.clickhouse.v2.span_reader import CHSpanReader

        client = CHSpanReader(
            host="clickhouse.invalid", server_enforced_readonly=True, native_port=9000
        )._client
    else:
        name = (
            "end_user_dict_reader" if kind == "users" else "trace_session_dict_reader"
        )
        module = importlib.import_module(f"tracer.services.clickhouse.v2.{name}")
        monkeypatch.setattr(module, "_client", None)
        monkeypatch.setattr(
            module,
            "get_v2_config",
            lambda: {
                "host": "clickhouse.invalid",
                "tcp_port": 9000,
                "user": "readonly",
                "password": "",
                "database": "default",
                "server_enforced_readonly": True,
            },
        )
        client = module._get_client()
        assert module._get_client() is client
    assert isinstance(client, ServerEnforcedReadOnlyNativeClient)
    assert client._application_read is True


@pytest.fixture
def installed_http(monkeypatch):
    """Only stub server discovery and I/O; run the installed HTTP driver code."""
    bootstrap_timeouts = []

    def initialize(client, _tz_source):
        bootstrap_timeouts.append(client.timeout)
        names = set(application_read_settings()) | {
            "send_progress_in_http_headers",
            "http_headers_progress_interval_ms",
            "max_block_size",
            "optimize_use_projections",
            "max_bytes_before_external_group_by",
        }
        client.server_settings = {name: SettingDef(name, "0", 0) for name in names}

    monkeypatch.setattr(HttpBaseClient, "_init_common_settings", initialize)
    pool = Mock()
    pool.request.return_value = SimpleNamespace(status=200, headers={}, data=b"1\n")
    return pool, bootstrap_timeouts


def http_options(pool):
    return {
        "host": "clickhouse.invalid",
        "port": 8123,
        "username": "default",
        "password": "",
        "database": "default",
        "pool_mgr": pool,
        "connect_timeout": 3,
        "send_receive_timeout": 9.5,
        "autogenerate_session_id": False,
    }


def test_installed_http_none_constructor_pitfall_is_real(installed_http):
    pool, _ = installed_http
    options = {**http_options(pool), "send_receive_timeout": None}
    with pytest.raises(TypeError, match="NoneType"):
        clickhouse_connect.get_client(**options)


def test_installed_http_unlimited_requests_keep_finite_bootstrap_and_raw_diagnostic(
    installed_http,
):
    pool, bootstrap = installed_http
    supplied = {
        "max_execution_time": 1,
        "max_rows_to_read": 1,
        "max_memory_usage": 123456,
        "max_threads": 2,
        "max_block_size": 2048,
        "optimize_use_projections": 0,
        "max_bytes_before_external_group_by": 100000,
    }
    with application_read_context():
        client = create_application_read_http_client(
            **http_options(pool), settings=supplied
        )
    assert not is_application_read()
    assert bootstrap[0].read_timeout == 9  # Installed driver truncates 9.5.
    assert bootstrap[0].connect_timeout == 3
    assert int(client._progress_interval) >= 10000
    assert client.timeout.read_timeout is None
    assert client.timeout.connect_timeout == 3
    assert supplied["max_execution_time"] == supplied["max_rows_to_read"] == 1
    assert client.raw_query("SELECT 1 LIMIT 25", fmt="TabSeparated") == b"1\n"
    request = pool.request.call_args
    assert request.kwargs["timeout"].read_timeout is None
    assert request.kwargs["timeout"].connect_timeout == 3
    assert b"LIMIT 25" in request.kwargs["body"]
    params = parse_qs(urlparse(request.args[1]).query)
    assert all(params[key] == ["0"] for key in UNLIMITED_STATEMENT_SETTINGS)
    assert params["max_memory_usage"] == ["123456"]
    assert params["max_threads"] == ["2"]
    assert params["max_block_size"] == ["2048"]
    assert params["optimize_use_projections"] == ["0"]
    assert params["max_bytes_before_external_group_by"] == ["100000"]
    client.raw_stream("SELECT 1 LIMIT 25")
    assert pool.request.call_args.kwargs["preload_content"] is False
    assert pool.request.call_args.kwargs["timeout"].read_timeout is None
    diagnostic = clickhouse_connect.get_client(**http_options(pool))
    diagnostic.raw_query("SELECT 1")
    assert pool.request.call_args.kwargs["timeout"].read_timeout == 9
    client.ping()
    assert pool.request.call_args.kwargs["timeout"] == 3


@pytest.mark.parametrize(
    "key,value",
    [
        ("connect_timeout", None),
        ("connect_timeout", 0),
        ("send_receive_timeout", None),
        ("send_receive_timeout", float("inf")),
    ],
)
def test_http_bootstrap_rejects_unbounded_health_settings(monkeypatch, key, value):
    factory = Mock()
    monkeypatch.setattr(clickhouse_connect, "get_client", factory)
    with pytest.raises(ValueError, match="finite"):
        create_application_read_http_client(**{key: value})
    factory.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["spans", "users", "sessions", "sessions_no_overrides"]
)
def test_v2_http_constructors_use_application_transport(
    monkeypatch, installed_http, kind
):
    pool, _ = installed_http
    real_factory = clickhouse_connect.get_client

    def factory(**kwargs):
        return real_factory(
            **{**kwargs, "pool_mgr": pool, "autogenerate_session_id": False}
        )

    monkeypatch.setattr(clickhouse_connect, "get_client", factory)
    if kind == "spans":
        from tracer.services.clickhouse.v2.span_reader import CHSpanReader

        reader = CHSpanReader(host="clickhouse.invalid")
        client = reader._client
    else:
        module_name = (
            "end_user_dict_reader" if kind == "users" else "trace_session_dict_reader"
        )
        module = importlib.import_module(f"tracer.services.clickhouse.v2.{module_name}")
        monkeypatch.setattr(module, "_client", None)
        if hasattr(module, "_settings_tls"):
            monkeypatch.setattr(module, "_settings_tls", SimpleNamespace())
        if kind == "sessions_no_overrides":
            monkeypatch.setattr(module, "current_settings", lambda: {})
        monkeypatch.setattr(
            module,
            "get_v2_config",
            lambda: {
                "host": "clickhouse.invalid",
                "http_port": 8123,
                "user": "default",
                "password": "",
                "database": "default",
                "server_enforced_readonly": False,
            },
        )
        client = module._get_client()
        assert module._get_client() is client
    assert client.timeout.read_timeout is None
    assert client.timeout.connect_timeout > 0
    assert int(client.params["max_memory_usage"]) > 0
    assert all(client.params[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
    client.close()


@override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=987654321)
def test_http_application_memory_is_finite_and_configurable(installed_http):
    pool, _ = installed_http
    client = create_application_read_http_client(**http_options(pool))
    assert client.params["max_memory_usage"] == 987654321


@pytest.mark.parametrize("streaming", [False, True])
def test_installed_driver_delayed_loopback_response_survives_application_timeout(
    monkeypatch,
    installed_http,
    streaming,
):
    """Real urllib3 I/O: a 1.15s response exceeds the old 1s socket envelope.

    Server discovery alone is stubbed. No production host is reachable: the
    only allowed connect target is this test's ephemeral loopback port.
    """
    import _socket

    from clickhouse_connect.driver.exceptions import OperationalError

    delayed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if streaming:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.flush()
            # No database sleep/query: controlled local HTTP response latency.
            threading.Event().wait(1.15)
            delayed.set()
            try:
                if not streaming:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                self.wfile.write(b"1\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # The bounded diagnostic is expected to close its socket.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = server.server_address

    def loopback_only(sock, target):
        if target != address:
            pytest.fail(f"Unexpected nonfixture connection: {target}")
        return _socket.socket.connect(sock, target)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pool = PoolManager(retries=False)
    options = {
        **http_options(pool),
        "host": address[0],
        "port": address[1],
        "send_receive_timeout": 1,
    }
    try:
        client = create_application_read_http_client(**options)
        if streaming:
            response = client.raw_stream("SELECT 1 LIMIT 25", fmt="TabSeparated")
            assert response.read() == b"1\n"
            response.close()
        else:
            assert client.raw_query("SELECT 1 LIMIT 25", fmt="TabSeparated") == b"1\n"
        assert delayed.is_set()
        assert client.timeout.read_timeout is None
        assert client.timeout.connect_timeout == 3
        # Same driver/server/query, finite read mode: actually fails, not just
        # a field assertion. Both delayed headers and delayed bodies are tested.
        diagnostic = clickhouse_connect.get_client(**options)
        diagnostic.http_retries = 0
        with pytest.raises(OperationalError):
            diagnostic.raw_query("SELECT 1 LIMIT 25", fmt="TabSeparated")
    finally:
        pool.clear()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
