"""Pinned SOURCE reads; native protocol exercised offline, with no live server."""

import socket
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.v2.property_catalog.native_read_transport import (
    NativeReadTransportError,
    read_once,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import WriteMember

IDENTITY = [("node", "server-uuid", "source", "source_reader", 1, 1)]


@pytest.fixture(autouse=True)
def wall(monkeypatch):
    state = SimpleNamespace(now=0.0, timers=[])

    def timer(interval, callback):
        assert 0 < interval <= 120
        instance = Mock()
        instance.interval = interval

        def fire():
            state.now += interval + 1
            callback()

        instance.fire = fire
        state.timers.append(instance)
        return instance

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.monotonic",
        lambda: state.now,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.native_write_transport.threading.Timer",
        timer,
    )
    return state


def setup():
    channel = Mock()
    connection = SimpleNamespace(
        connected=True,
        socket=channel,
        connect_timeout=10,
        send_receive_timeout=300,
        hosts=deque([("node", 9000)]),
        context=SimpleNamespace(
            settings={}, client_settings={"server_side_params": False}
        ),
    )
    native = Mock(connection=connection, settings={}, connections=deque())
    native.execute.return_value = IDENTITY
    native.process_ordinary_query.return_value = ([(7,)], [("value", "UInt64")])
    native.substitute_params.side_effect = lambda sql, params, context: (
        Client.substitute_params(native, sql, params, context)
    )
    admission = Mock()
    admission.acquire.return_value = True
    driver = SimpleNamespace(
        database="source",
        user="source_reader",
        server_enforced_readonly=True,
        read_timeout_ceiling_ms=30_000,
        connection=Mock(side_effect=AssertionError("SOURCE raw access is forbidden")),
        execute_read=Mock(side_effect=AssertionError("unpinned read is forbidden")),
        _read_admission=admission,
        _get_client=Mock(return_value=native),
        _return_client=Mock(),
    )
    options = {
        "member": WriteMember(
            "db-uuid", "node", "one", "server-uuid", (), "http://node:8123"
        ),
        "database": "source",
        "user": "source_reader",
        "sql": "SELECT %(value)s AS value FROM `source`.`captured_spans` LIMIT 1",
        "params": {"value": 7},
        "timeout_ms": 1000,
        "query_id": "read-id",
    }
    return driver, native, channel, options


def assert_released(driver, native, wall):
    native.disconnect.assert_called_once()
    driver._return_client.assert_called_once_with(native)
    driver._read_admission.release.assert_called_once()
    driver.connection.assert_not_called()
    driver.execute_read.assert_not_called()
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300
    if wall.timers:
        wall.timers[0].cancel.assert_called_once()
        wall.timers[0].join.assert_called_once()


@pytest.mark.parametrize("rows", [[(7,)], []])
def test_same_verified_socket_even_for_empty_results_and_column_types(rows, wall):
    driver, native, _, options = setup()
    response = (rows, [("value", "UInt64")])
    native.process_ordinary_query.return_value = response
    assert read_once(driver, **options) is response
    driver._read_admission.acquire.assert_called_once_with(timeout=1.0)
    native.execute.assert_called_once_with(
        "SELECT hostName(), toString(serverUUID()), currentDatabase(), currentUser(), "
        "toUInt64(value), toUInt8(readonly) FROM system.settings WHERE name='readonly'",
        {},
        settings=None,
    )
    native.make_query_settings.assert_called_once_with(None)
    native.process_ordinary_query.assert_called_once_with(
        "SELECT 7 AS value FROM `source`.`captured_spans` LIMIT 1",
        params=None,
        with_column_types=True,
        external_tables=None,
        query_id="read-id",
        types_check=False,
        columnar=False,
    )
    native.process_insert_query.assert_not_called()
    assert_released(driver, native, wall)


@pytest.mark.parametrize(
    "field,value",
    [
        (0, "other"),
        (1, "other-uuid"),
        (2, "other_db"),
        (3, "admin"),
        (4, 0),
        (4, 2),
        (5, 0),
    ],
)
def test_wrong_server_database_user_or_readonly_profile_never_reads(field, value, wall):
    driver, native, _, options = setup()
    row = list(IDENTITY[0])
    row[field] = value
    native.execute.return_value = [tuple(row)]
    with pytest.raises(NativeReadTransportError, match="another identity"):
        read_once(driver, **options)
    native.process_ordinary_query.assert_not_called()
    assert_released(driver, native, wall)


@pytest.mark.parametrize("identity", [[], [()], IDENTITY * 2, [IDENTITY[0][:4]]])
def test_empty_or_malformed_identity_cannot_authorize_empty_scan(identity, wall):
    driver, native, _, options = setup()
    native.execute.return_value = identity
    native.process_ordinary_query.return_value = ([], [("value", "UInt64")])
    with pytest.raises(NativeReadTransportError, match="another identity"):
        read_once(driver, **options)
    native.process_ordinary_query.assert_not_called()
    assert_released(driver, native, wall)


@pytest.mark.parametrize("stage", ["identity", "settings", "binding"])
@pytest.mark.parametrize("change", ["connection", "socket", "disconnected"])
def test_connection_change_blocks_raw_dispatch(stage, change, wall):
    driver, native, _, options = setup()
    original = native.connection

    def replace(*args, **kwargs):
        if change == "connection":
            native.connection = SimpleNamespace(context=original.context)
        elif change == "socket":
            original.socket = Mock()
        else:
            original.connected = False
        return IDENTITY if stage == "identity" else "SELECT 7"

    callback = {
        "identity": native.execute,
        "settings": native.make_query_settings,
        "binding": native.substitute_params,
    }[stage]
    callback.side_effect = replace
    # Replacing a socket while the initial identity connects is legitimate;
    # the socket carrying that completed proof is what subsequent checks pin.
    if stage == "identity" and change == "socket":
        assert read_once(driver, **options) == ([(7,)], [("value", "UInt64")])
    else:
        with pytest.raises(
            NativeReadTransportError, match="connection (changed|was lost)"
        ):
            read_once(driver, **options)
        native.process_ordinary_query.assert_not_called()
    native.disconnect.assert_called_once()
    driver._return_client.assert_called_once_with(native)
    driver._read_admission.release.assert_called_once()
    assert original.connect_timeout == 10
    assert original.send_receive_timeout == 300


@pytest.mark.parametrize("stage", ["identity", "binding", "read"])
def test_lost_reply_or_binding_error_is_never_retried(stage, wall):
    driver, native, _, options = setup()
    callback = {
        "identity": native.execute,
        "binding": native.substitute_params,
        "read": native.process_ordinary_query,
    }[stage]
    failure = TimeoutError("lost response")
    callback.side_effect = failure
    with pytest.raises(TimeoutError) as caught:
        read_once(driver, **options)
    assert caught.value is failure
    native.execute.assert_called_once()
    assert native.process_ordinary_query.call_count == (1 if stage == "read" else 0)
    assert_released(driver, native, wall)


@pytest.mark.parametrize("stage", ["identity", "binding", "read"])
def test_wall_watchdog_rejects_late_response_and_closes_owned_socket(stage, wall):
    driver, native, channel, options = setup()

    def expire(*args, **kwargs):
        wall.timers[0].fire()
        return {
            "identity": IDENTITY,
            "binding": "SELECT 7",
            "read": ([], [("value", "UInt64")]),
        }[stage]

    callback = {
        "identity": native.execute,
        "binding": native.substitute_params,
        "read": native.process_ordinary_query,
    }[stage]
    callback.side_effect = expire
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        read_once(driver, **options)
    channel.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    assert native.process_ordinary_query.call_count == (1 if stage == "read" else 0)
    assert_released(driver, native, wall)


def test_watchdog_never_shutdowns_a_replacement_socket(wall):
    driver, native, channel, options = setup()
    foreign = Mock()

    def replace(*args):
        native.connection.socket = foreign
        wall.timers[0].fire()
        return "SELECT 7"

    native.substitute_params.side_effect = replace
    with pytest.raises(NativeReadTransportError, match="connection changed"):
        read_once(driver, **options)
    channel.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    foreign.shutdown.assert_not_called()
    native.process_ordinary_query.assert_not_called()
    assert_released(driver, native, wall)


def test_pool_admission_timeout_does_not_borrow_or_release_unowned_slot(wall):
    driver, native, _, options = setup()
    driver._read_admission.acquire.return_value = False
    with pytest.raises(TimeoutError, match="admission deadline"):
        read_once(driver, **options)
    driver._get_client.assert_not_called()
    driver._return_client.assert_not_called()
    driver._read_admission.release.assert_not_called()
    native.execute.assert_not_called()
    assert wall.timers == []


def test_pool_creation_failure_releases_admission_without_retry():
    driver, native, _, options = setup()
    driver._get_client.side_effect = OSError("pool creation failed")
    with pytest.raises(OSError, match="pool creation"):
        read_once(driver, **options)
    driver._get_client.assert_called_once()
    driver._read_admission.release.assert_called_once()
    driver._return_client.assert_not_called()
    native.execute.assert_not_called()


def test_admission_and_pool_borrow_share_whole_wall(wall):
    driver, native, _, options = setup()

    def borrow():
        wall.now = 2.0
        return native

    driver._get_client.side_effect = borrow
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        read_once(driver, **options)
    native.execute.assert_not_called()
    assert_released(driver, native, wall)


def test_cleanup_finishes_before_pool_release(wall):
    driver, native, _, options = setup()
    events = []

    def execute(*args, **kwargs):
        wall.timers[0].cancel.side_effect = lambda: events.append("cancel")
        wall.timers[0].join.side_effect = lambda: events.append("join")
        return IDENTITY

    native.execute.side_effect = execute
    native.disconnect.side_effect = lambda: events.append("disconnect")
    driver._return_client.side_effect = lambda client: events.append("pool")
    driver._read_admission.release.side_effect = lambda: events.append("release")
    read_once(driver, **options)
    assert events == ["cancel", "join", "disconnect", "pool", "release"]


@pytest.mark.parametrize("failure", ["socket_setup", "disconnect", "pool_return"])
def test_cleanup_failure_still_releases_admission(failure, wall):
    driver, native, channel, options = setup()
    callback = {
        "socket_setup": channel.settimeout,
        "disconnect": native.disconnect,
        "pool_return": driver._return_client,
    }[failure]
    callback.side_effect = OSError(failure)
    with pytest.raises(OSError, match=failure):
        read_once(driver, **options)
    native.disconnect.assert_called_once()
    driver._return_client.assert_called_once_with(native)
    driver._read_admission.release.assert_called_once()
    assert native.connection.connect_timeout == 10
    assert native.connection.send_receive_timeout == 300
    if failure == "socket_setup":
        native.execute.assert_not_called()
        wall.timers[0].join.assert_not_called()


@pytest.mark.parametrize("kind", ["settings", "alternate_connection", "alternate_host"])
def test_no_inherited_settings_or_driver_failover_before_identity(kind, wall):
    driver, native, _, options = setup()
    if kind == "settings":
        native.settings = {"readonly": 2}
    elif kind == "alternate_connection":
        native.connections.append(Mock())
    else:
        native.connection.hosts.append(("other", 9000))
    with pytest.raises(NativeReadTransportError, match="one settings-free direct"):
        read_once(driver, **options)
    native.execute.assert_not_called()
    assert_released(driver, native, wall)


@pytest.mark.parametrize("kind", ["client", "query", "server_side_params"])
def test_settings_inherited_after_identity_cannot_reach_source(kind, wall):
    driver, native, _, options = setup()
    if kind == "client":
        native.make_query_settings.side_effect = lambda settings: (
            native.settings.update(readonly=2)
        )
    elif kind == "query":
        native.connection.context.settings = {"max_threads": 1}
    else:
        native.connection.context.client_settings["server_side_params"] = True
    with pytest.raises(NativeReadTransportError, match="inherited query settings"):
        read_once(driver, **options)
    native.process_ordinary_query.assert_not_called()
    assert_released(driver, native, wall)


@pytest.mark.parametrize("value", [0, 30_001, True, None])
def test_invalid_deadline_never_borrows(value):
    driver, _, _, options = setup()
    options["timeout_ms"] = value
    with pytest.raises(NativeReadTransportError):
        read_once(driver, **options)
    driver._read_admission.acquire.assert_not_called()


@pytest.mark.parametrize(
    "ceiling,timeout",
    [
        (30_000, 30_000),
        (60_000, 30_001),
        (60_000, 60_000),
        (120_000, 60_000),
        (120_000, 120_000),
        (240_000, 120_000),
    ],
)
def test_read_uses_configured_source_ceiling_up_to_120_seconds(ceiling, timeout, wall):
    driver, native, channel, options = setup()
    driver.read_timeout_ceiling_ms = ceiling
    options["timeout_ms"] = timeout
    assert read_once(driver, **options) == ([(7,)], [("value", "UInt64")])
    driver._read_admission.acquire.assert_called_once_with(timeout=timeout / 1000)
    assert wall.timers[0].interval == timeout / 1000
    assert all(
        call.args == (timeout / 1000,) for call in channel.settimeout.call_args_list
    )
    assert native.execute.call_args.kwargs["settings"] is None
    native.make_query_settings.assert_called_once_with(None)
    assert_released(driver, native, wall)


@pytest.mark.parametrize(
    "ceiling,timeout",
    [
        (9500, 9501),
        (30_000, 30_001),
        (60_000, 60_001),
        (120_000, 120_001),
        (240_000, 120_001),
    ],
)
def test_read_refuses_above_actual_ceiling_or_120_seconds_before_borrow(
    ceiling, timeout
):
    driver, native, _, options = setup()
    driver.read_timeout_ceiling_ms = ceiling
    options["timeout_ms"] = timeout
    with pytest.raises(NativeReadTransportError, match="deadline invalid"):
        read_once(driver, **options)
    driver._read_admission.acquire.assert_not_called()
    driver._get_client.assert_not_called()
    native.execute.assert_not_called()


@pytest.mark.parametrize("ceiling", [None, 0, -1, True, 120_000.0, "120000"])
def test_invalid_source_ceiling_is_not_replaced_with_a_larger_default(ceiling):
    driver, _, _, options = setup()
    driver.read_timeout_ceiling_ms = ceiling
    with pytest.raises(NativeReadTransportError, match="deadline invalid"):
        read_once(driver, **options)
    driver._get_client.assert_not_called()


def test_missing_source_ceiling_is_rejected_before_borrow():
    driver, _, _, options = setup()
    del driver.read_timeout_ceiling_ms
    with pytest.raises(NativeReadTransportError, match="deadline invalid"):
        read_once(driver, **options)
    driver._get_client.assert_not_called()


def test_120_second_read_still_has_one_wall_watchdog_and_no_retry(wall):
    driver, native, channel, options = setup()
    driver.read_timeout_ceiling_ms = 120_000
    options["timeout_ms"] = 120_000

    def delayed(*args, **kwargs):
        assert wall.timers[0].interval == 120
        wall.timers[0].fire()
        return ([], [("value", "UInt64")])

    native.process_ordinary_query.side_effect = delayed
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        read_once(driver, **options)
    channel.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    native.execute.assert_called_once()
    native.process_ordinary_query.assert_called_once()
    assert_released(driver, native, wall)


@pytest.mark.parametrize(
    "field,value",
    [("server_enforced_readonly", False), ("database", "other"), ("user", "writer")],
)
def test_non_source_driver_is_rejected_before_borrow(field, value):
    driver, _, _, options = setup()
    setattr(driver, field, value)
    with pytest.raises(NativeReadTransportError):
        read_once(driver, **options)
    driver._get_client.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO source.spans VALUES (1)",
        "ALTER TABLE source.spans DELETE WHERE 1",
        "WITH 1 AS x INSERT INTO source.spans SELECT x",
        "SELECT 1; DROP TABLE source.spans",
        "SELECT 1 SETTINGS max_threads=1",
        "SELECT 1 INTO OUTFILE '/tmp/no'",
        "SELECT 1 FORMAT JSON",
        "SHOW TABLES",
    ],
)
def test_mutations_multistatements_settings_and_output_redirection_never_borrow(sql):
    driver, _, _, options = setup()
    options["sql"] = sql
    with pytest.raises(RuntimeError):
        read_once(driver, **options)
    driver._get_client.assert_not_called()


@pytest.mark.parametrize(
    "params",
    [
        {"value": object()},
        {"value": float("inf")},
        {"value": datetime(2026, 1, 1)},
        {"value": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
        {1: "bad name"},
        [("value", 7)],
    ],
)
def test_invalid_parameters_fail_before_io(params):
    driver, _, _, options = setup()
    options["params"] = params
    with pytest.raises(NativeReadTransportError):
        read_once(driver, **options)
    driver._get_client.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        [],
        ([], []),
        ([], [("value",)]),
        ([], [("value", None)]),
        ([(1, 2)], [("value", "UInt64")]),
        (None, [("value", "UInt64")]),
    ],
)
def test_malformed_native_result_is_not_success(response, wall):
    driver, native, _, options = setup()
    native.process_ordinary_query.return_value = response
    with pytest.raises(NativeReadTransportError, match="rows/column types"):
        read_once(driver, **options)
    assert_released(driver, native, wall)


@pytest.mark.parametrize("params", [None, {}])
def test_optional_parameters_and_query_id_are_supported(params, wall):
    driver, native, _, options = setup()
    options.update(sql="SELECT 7", params=params, query_id=None)
    read_once(driver, **options)
    assert native.substitute_params.call_count == (params is not None)
    assert native.process_ordinary_query.call_args.kwargs["query_id"] is None
    assert_released(driver, native, wall)


@pytest.mark.parametrize("admission_held", [False, True])
@pytest.mark.parametrize("timeout_ms", [1000, 60_000, 120_000])
def test_real_source_client_pool_and_semaphore_keep_public_raw_guard(
    admission_held, timeout_ms, monkeypatch
):
    from tracer.services.clickhouse.client import ClickHouseClient

    _, native, _, options = setup()
    options["timeout_ms"] = timeout_ms
    driver = ClickHouseClient(
        host="unused.invalid",
        port=2,
        user="source_reader",
        password="offline-only",
        database="source",
        server_enforced_readonly=True,
        pool_size=1,
        read_timeout_ceiling_ms=120_000,
    )
    create = Mock(side_effect=AssertionError("offline test must not create a client"))
    monkeypatch.setattr(driver, "_create_client", create)
    driver._pool.put_nowait(native)
    with pytest.raises(RuntimeError, match="Raw ClickHouse connections are disabled"):
        with driver.connection():
            pytest.fail("SOURCE raw connection guard was bypassed")
    if admission_held:
        assert driver._read_admission.acquire(blocking=False)
        try:
            with pytest.raises(TimeoutError, match="admission deadline"):
                read_once(driver, **{**options, "timeout_ms": 1})
            assert not driver._read_admission.acquire(blocking=False)
        finally:
            driver._read_admission.release()
        native.execute.assert_not_called()
    else:
        assert read_once(driver, **options) == ([(7,)], [("value", "UInt64")])
        native.disconnect.assert_called_once()
    assert driver._pool.get_nowait() is native
    assert driver._read_admission.acquire(blocking=False)
    assert not driver._read_admission.acquire(blocking=False)
    driver._read_admission.release()
    create.assert_not_called()
    assert driver.server_enforced_readonly is True
    assert driver.allow_query_settings_with_server_readonly is False


@pytest.mark.parametrize("disconnect_during_binding", [False, True])
def test_installed_protocol_escapes_params_once_without_reconnect_or_settings(
    disconnect_during_binding, wall
):
    driver, _, channel, options = setup()
    # Client construction does not connect; the actual installed ordinary
    # protocol and send_query write only to this in-memory native wire.
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
    connection.send_external_tables = Mock()
    native.execute = Mock(return_value=IDENTITY)
    response = ([], [("value", "String")])
    native.receive_result = Mock(return_value=response)
    real_bind = native.substitute_params

    def bind(*args):
        result = real_bind(*args)
        if disconnect_during_binding:
            connection.connected = False
        return result

    native.substitute_params = Mock(side_effect=bind)
    driver._get_client.return_value = native
    options.update(
        sql="SELECT %(text)s AS value FROM `source`.`captured_spans` WHERE start_time < toDateTime64(%(until)s,6,'UTC') AND project_id IN %(projects)s",
        params={
            "text": "O'Reilly; DROP TABLE x --",
            "until": datetime(2026, 9, 6, 1, 2, 3, 123456, tzinfo=UTC),
            "projects": (UUID("11111111-1111-4111-8111-111111111111"),),
        },
    )
    if disconnect_during_binding:
        with pytest.raises(NativeReadTransportError, match="connection was lost"):
            read_once(driver, **options)
        assert connection.fout.getvalue() == b""
        native.receive_result.assert_not_called()
    else:
        assert read_once(driver, **options) is response
        wire = connection.fout.getvalue()
        assert wire.count(b"SELECT ") == 1
        assert b"'O\\'Reilly; DROP TABLE x --'" in wire
        assert b"'2026-09-06 01:02:03.123456'" in wire
        assert b"('11111111-1111-4111-8111-111111111111')" in wire
        assert wire.count(b"read-id") == 1
        assert connection.context.settings == {}
        native.receive_result.assert_called_once_with(
            with_column_types=True, columnar=False
        )
        connection.send_external_tables.assert_called_once_with(None, types_check=False)
    native.substitute_params.assert_called_once()
    native.execute.assert_called_once()
    assert native.execute.call_args.kwargs["settings"] is None
    connection.connect.assert_not_called()
    connection.force_connect.assert_not_called()
    connection.ping.assert_not_called()
    connection.disconnect.assert_called_once()
    driver._return_client.assert_called_once_with(native)
    driver._read_admission.release.assert_called_once()
