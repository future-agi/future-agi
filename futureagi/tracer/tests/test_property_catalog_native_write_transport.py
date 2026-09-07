"""No reconnect, duplicate dispatch, or late pool borrower on native writes."""

import socket
import threading
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog.native_write_transport import (
    NativeWriteTransportError,
    insert_once,
    ordinary_once,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import WriteMember


@pytest.fixture(params=[insert_once, ordinary_once], ids=["insert", "ordinary"])
def transport(request):
    return request.param


@pytest.fixture
def watchdog(monkeypatch):
    # Trigger the production expiry callback at an exact protocol boundary.
    # A real 30 ms timer can expire during test setup on a busy offline runner.
    now = [0.0]
    timer = Mock()

    def create(interval, callback):
        assert interval == 1.0

        def fire():
            now[0] = 2.0
            callback()

        timer.fire = fire
        return timer

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.threading.Timer",
        create,
    )
    return timer


def protocol(native, transport):
    return (
        native.process_insert_query
        if transport is insert_once
        else native.process_ordinary_query
    )


def setup(transport=insert_once):
    member = WriteMember(
        "db-uuid", "node", "member", "server-uuid", (), "http://node:8123"
    )
    channel = Mock()
    connection = SimpleNamespace(
        connected=True, socket=channel, connect_timeout=10, send_receive_timeout=300
    )
    native = Mock(connection=connection)
    native.execute.return_value = [("node", "server-uuid", "catalog_dev", "writer")]
    native.process_insert_query.return_value = 1
    native.process_ordinary_query.return_value = []

    @contextmanager
    def borrowed():
        yield native

    driver = SimpleNamespace(
        database="catalog_dev",
        user="writer",
        server_enforced_readonly=False,
        connection=borrowed,
    )
    options = {
        "member": member,
        "database": "catalog_dev",
        "user": "writer",
        "sql": "INSERT INTO `catalog_dev`.`property_catalog_activations` (x) VALUES",
        "values": [(1,)],
        "query_id": "recorded-query-id",
        "settings": {"async_insert": 0},
        "timeout_ms": 1000,
        "before_send": Mock(),
    }
    if transport is ordinary_once:
        options.pop("values")
        options["sql"] = (
            "ALTER TABLE `capture`.`spans_owned` ATTACH PARTITION ALL FROM `source`.`spans`"
        )
        options["settings"] = {"max_threads": 1}
    return driver, native, channel, options


def test_native_insert_occurs_once_after_durable_sent_and_without_reconnect():
    driver, native, _, options = setup()
    order = []
    options["before_send"].side_effect = lambda: order.append("sent-fsync")
    native.process_insert_query.side_effect = lambda *a, **k: (
        order.append("insert") or 1
    )
    assert insert_once(driver, **options) == 1
    assert order == ["sent-fsync", "insert"]
    assert native.execute.call_count == 1  # identity SELECT only
    native.process_insert_query.assert_called_once_with(
        options["sql"],
        [(1,)],
        external_tables=None,
        query_id="recorded-query-id",
        types_check=False,
        columnar=False,
    )
    native.make_query_settings.assert_called_once_with({"async_insert": 0})
    native.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "row",
    [
        [],
        [("other", "server-uuid", "catalog_dev", "writer")],
        [("node", "other", "catalog_dev", "writer")],
        [("node", "server-uuid", "source", "writer")],
        [("node", "server-uuid", "catalog_dev", "admin")],
    ],
)
def test_actual_connection_identity_mismatch_never_marks_sent(row, transport):
    driver, native, _, options = setup(transport)
    native.execute.return_value = row
    with pytest.raises(NativeWriteTransportError, match="another identity"):
        transport(driver, **options)
    options["before_send"].assert_not_called()
    protocol(native, transport).assert_not_called()
    native.disconnect.assert_called_once()


def test_journal_fsync_failure_sends_no_mutation(transport):
    driver, native, _, options = setup(transport)
    options["before_send"].side_effect = OSError("fsync failed")
    with pytest.raises(OSError):
        transport(driver, **options)
    protocol(native, transport).assert_not_called()
    native.disconnect.assert_called_once()


def test_response_loss_does_not_retry(transport):
    driver, native, _, options = setup(transport)
    protocol(native, transport).side_effect = TimeoutError("lost response")
    with pytest.raises(TimeoutError):
        transport(driver, **options)
    options["before_send"].assert_called_once()
    assert protocol(native, transport).call_count == 1
    assert native.execute.call_count == 1
    native.disconnect.assert_called_once()


def test_wall_watchdog_shuts_down_only_owned_socket_and_never_retries(
    transport, watchdog
):
    driver, native, channel, options = setup(transport)
    released = threading.Event()
    channel.shutdown.side_effect = lambda *a: released.set()

    def hanging(*a, **k):
        watchdog.fire()
        assert released.is_set(), "socket watchdog did not fire"
        return 1

    protocol(native, transport).side_effect = hanging
    with pytest.raises(TimeoutError):
        transport(driver, **options)
    channel.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    native.disconnect.assert_called_once()
    assert protocol(native, transport).call_count == 1
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300
    watchdog.start.assert_called_once()
    watchdog.cancel.assert_called_once()
    watchdog.join.assert_called_once()


def test_settings_cannot_swap_confirmed_connection(transport):
    driver, native, _, options = setup(transport)
    native.make_query_settings.side_effect = lambda *a: setattr(
        native, "connection", Mock()
    )
    with pytest.raises(NativeWriteTransportError, match="connection changed"):
        transport(driver, **options)
    options["before_send"].assert_not_called()
    protocol(native, transport).assert_not_called()


@pytest.mark.parametrize("timeout", [0, 30_001, True, None])
def test_invalid_deadline_never_borrows(timeout, transport):
    driver, native, _, options = setup(transport)
    options["timeout_ms"] = timeout
    with pytest.raises(NativeWriteTransportError):
        transport(driver, **options)
    native.execute.assert_not_called()


def test_readonly_driver_cannot_dispatch(transport):
    driver, native, _, options = setup(transport)
    driver.server_enforced_readonly = True
    with pytest.raises(NativeWriteTransportError):
        transport(driver, **options)
    native.execute.assert_not_called()


def test_ordinary_command_returns_response_unchanged_after_durable_intent():
    driver, native, _, options = setup(ordinary_once)
    response = [("ordinary response", 7)]
    order = []
    options["before_send"].side_effect = lambda: order.append("intent-fsync")
    native.process_ordinary_query.side_effect = lambda *a, **k: (
        order.append("command") or response
    )
    assert ordinary_once(driver, **options) is response
    assert order == ["intent-fsync", "command"]
    native.execute.assert_called_once()
    assert native.execute.call_args.args == (
        "SELECT hostName(), toString(serverUUID()), currentDatabase(), currentUser()",
        {},
    )
    native.process_ordinary_query.assert_called_once_with(
        options["sql"],
        params=None,
        with_column_types=False,
        external_tables=None,
        query_id=options["query_id"],
        types_check=False,
        columnar=False,
    )
    native.process_insert_query.assert_not_called()
    native.make_query_settings.assert_called_once_with(options["settings"])
    assert native.make_query_settings.call_args.args[0] is not options["settings"]
    native.disconnect.assert_called_once()
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300


@pytest.mark.parametrize(
    ("stage", "change"),
    [
        ("identity", "connection"),
        ("identity", "disconnected"),
        ("settings", "connection"),
        ("settings", "socket"),
        ("settings", "disconnected"),
        ("before_send", "connection"),
        ("before_send", "socket"),
        ("before_send", "disconnected"),
    ],
)
def test_connection_changes_never_authorize_mutation(transport, change, stage):
    driver, native, _, options = setup(transport)
    original = native.connection

    def change_connection(*a, **k):
        if change == "connection":
            native.connection = Mock()
        elif change == "socket":
            original.socket = Mock()
        else:
            original.connected = False
        return [("node", "server-uuid", "catalog_dev", "writer")]

    callback = {
        "identity": native.execute,
        "settings": native.make_query_settings,
        "before_send": options["before_send"],
    }[stage]
    callback.side_effect = change_connection
    with pytest.raises(
        NativeWriteTransportError, match="connection (changed|was lost)"
    ):
        transport(driver, **options)
    protocol(native, transport).assert_not_called()
    if stage != "before_send":
        options["before_send"].assert_not_called()
    else:
        options["before_send"].assert_called_once()
    native.disconnect.assert_called_once()
    assert original.connect_timeout == 10
    assert original.send_receive_timeout == 300


@pytest.mark.parametrize("stage", ["identity", "before_send"])
def test_wall_deadline_before_command_never_dispatches(transport, stage, watchdog):
    driver, native, channel, options = setup(transport)
    expired = threading.Event()
    channel.shutdown.side_effect = lambda *a: expired.set()

    def wait_for_deadline(*a, **k):
        watchdog.fire()
        assert expired.is_set(), "socket watchdog did not fire"
        return [("node", "server-uuid", "catalog_dev", "writer")]

    callback = native.execute if stage == "identity" else options["before_send"]
    callback.side_effect = wait_for_deadline
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        transport(driver, **options)
    protocol(native, transport).assert_not_called()
    channel.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    native.disconnect.assert_called_once()
    if stage == "identity":
        options["before_send"].assert_not_called()


def test_watchdog_finishes_before_client_returns_to_pool(transport, monkeypatch):
    driver, native, channel, options = setup(transport)
    order = []

    class Watchdog:
        def __init__(self, interval, callback):
            assert 0 < interval <= 1

        def start(self):
            order.append("start")

        def cancel(self):
            order.append("cancel")

        def join(self):
            order.append("join")

    @contextmanager
    def borrowed():
        yield native
        assert native.connection.connect_timeout == 10
        assert native.connection.send_receive_timeout == 300
        order.append("return-to-pool")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.threading.Timer",
        Watchdog,
    )
    driver.connection = borrowed
    options["before_send"].side_effect = lambda: order.append("sent-fsync")
    protocol(native, transport).side_effect = lambda *a, **k: order.append("command")
    native.disconnect.side_effect = lambda: order.append("disconnect")
    transport(driver, **options)
    assert order == [
        "start",
        "sent-fsync",
        "command",
        "cancel",
        "join",
        "disconnect",
        "return-to-pool",
    ]
    channel.shutdown.assert_not_called()


def test_socket_timeout_setup_failure_disconnects_without_dispatch(transport):
    driver, native, channel, options = setup(transport)
    channel.settimeout.side_effect = OSError("closed pooled socket")
    with pytest.raises(OSError, match="closed pooled socket"):
        transport(driver, **options)
    native.execute.assert_not_called()
    protocol(native, transport).assert_not_called()
    options["before_send"].assert_not_called()
    native.disconnect.assert_called_once()
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300


def test_elapsed_deadline_while_borrowing_cannot_leave_a_client_connected(
    transport,
    monkeypatch,
):
    driver, native, _, options = setup(transport)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.monotonic",
        Mock(side_effect=[0, 2]),
    )
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        transport(driver, **options)
    native.execute.assert_not_called()
    protocol(native, transport).assert_not_called()
    options["before_send"].assert_not_called()
    native.disconnect.assert_called_once()
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300


@pytest.mark.parametrize("disconnect_before_send", [False, True])
def test_installed_ordinary_protocol_has_no_reconnect_or_parameter_substitution(
    disconnect_before_send,
):
    from clickhouse_driver import Client

    driver, _, channel, options = setup(ordinary_once)
    # Constructing Client does not connect. Exercise the installed ordinary
    # method and Connection.send_query against an in-memory wire, not a server.
    native = Client("unused.invalid")
    connection = native.connection
    connection.connected = True
    connection.socket = channel
    connection.fout = BytesIO()
    connection.server_info = SimpleNamespace(used_revision=0)
    connection.connect = Mock(side_effect=AssertionError("unexpected reconnect"))
    connection.force_connect = Mock(side_effect=AssertionError("unexpected handshake"))
    connection.ping = Mock(side_effect=AssertionError("unexpected ping"))
    connection.disconnect = Mock()
    native.execute = Mock(
        return_value=[("node", "server-uuid", "catalog_dev", "writer")]
    )
    native.substitute_params = Mock(side_effect=AssertionError("SQL changed"))
    connection.send_external_tables = Mock()
    response = []
    native.receive_result = Mock(return_value=response)
    options["settings"] = {}

    @contextmanager
    def borrowed():
        yield native

    driver.connection = borrowed
    if disconnect_before_send:
        options["before_send"].side_effect = lambda: setattr(
            connection, "connected", False
        )
        with pytest.raises(NativeWriteTransportError, match="connection was lost"):
            ordinary_once(driver, **options)
        assert connection.fout.getvalue() == b""
        native.receive_result.assert_not_called()
    else:
        assert ordinary_once(driver, **options) is response
        wire = connection.fout.getvalue()
        assert wire.count(options["sql"].encode()) == 1
        assert wire.count(options["query_id"].encode()) == 1
        connection.send_external_tables.assert_called_once_with(None, types_check=False)
        native.receive_result.assert_called_once_with(
            with_column_types=False, columnar=False
        )
    native.execute.assert_called_once()  # The identity read only.
    native.substitute_params.assert_not_called()
    connection.connect.assert_not_called()
    connection.force_connect.assert_not_called()
    connection.ping.assert_not_called()
    options["before_send"].assert_called_once()
    connection.disconnect.assert_called_once()


def test_installed_native_enum_codec_preserves_string_names_without_integer_mask():
    from clickhouse_driver.columns.enumcolumn import Enum8Column
    from clickhouse_driver.context import Context
    from clickhouse_driver.errors import LogicalError

    context = Context()
    context.client_settings = {}
    codec = Enum8Column(
        {1: "always"}, {"always": 1}, types_check=False, context=context
    )
    result = BytesIO()
    codec.write_data(["always"], result)
    assert result.getvalue() == b"\x01"
    with pytest.raises(LogicalError, match="Unknown element"):
        codec.write_data(["unrecognized"], BytesIO())
