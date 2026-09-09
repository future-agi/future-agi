"""Transport policy for explicitly application-scoped ClickHouse readers."""

from contextlib import contextmanager
from math import isfinite

from tracer.services.clickhouse.application_read_policy import application_read_settings


def create_application_read_http_client(**kwargs):
    """Initialize with finite health checks, then allow unbounded response reads.

    clickhouse-connect accepts ``Timeout(read=None)`` at request time, but some
    versions subtract five from the constructor's send_receive_timeout when
    computing progress headers. Do not pass None through that initialization.
    This is a dedicated application client, never a mutation of a shared
    diagnostic client or of its pool manager's defaults.
    """
    import clickhouse_connect
    from urllib3.util import Timeout

    connect = kwargs.setdefault("connect_timeout", 10)
    bootstrap = kwargs.setdefault("send_receive_timeout", 10)
    for value in (connect, bootstrap):
        if value is None or not isfinite(float(value)) or int(value) <= 0:
            raise ValueError(
                "ClickHouse initialization timeouts must be finite and positive"
            )
    kwargs["settings"] = application_read_settings(kwargs.get("settings"))
    client = clickhouse_connect.get_client(**kwargs)
    client.timeout = Timeout(connect=int(connect), read=None)
    return client


@contextmanager
def application_native_read_transport(client):
    """Temporarily remove a pooled native connection's response timeout.

    TCP connect and the driver's sync-request/ping timeout remain finite and
    independent. Native server settings (including locked-profile limits) are
    unaffected. Restore even an original blocking (None) socket timeout.
    """
    connection = getattr(client, "connection", None)
    if getattr(connection, "connected", None) not in {True, False}:
        yield
        return

    original_receive = connection.send_receive_timeout
    socket = getattr(connection, "socket", None)
    original_socket_timeout = (
        socket.gettimeout() if socket is not None else original_receive
    )
    try:
        connection.send_receive_timeout = None
        if socket is not None:
            socket.settimeout(None)
        yield
    finally:
        connection.send_receive_timeout = original_receive
        # A lazy connect/reconnect may have replaced the socket during execute.
        current_socket = getattr(connection, "socket", None)
        if current_socket is not None:
            try:
                current_socket.settimeout(
                    original_socket_timeout
                    if current_socket is socket
                    else original_receive
                )
            except Exception:
                # Do not reuse a socket whose original policy could not be restored.
                client.disconnect()
                raise
