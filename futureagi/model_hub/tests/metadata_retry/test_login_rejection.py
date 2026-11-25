"""Login-time rejections, which libpq reports without the server's SQLSTATE.

PostgreSQL refuses new logins while it starts, stops, recovers or is full
(57P03 cannot_connect_now, 53300 too_many_connections), and PgBouncer refuses
them at its pool limits or while the server is down. psycopg raises these as a
plain OperationalError whose sqlstate is None and whose text carries "FATAL:".
The errors here come from real libpq connection attempts against a loopback
server speaking the startup protocol, never from hand-written driver messages.
"""

import contextlib
import socket
import threading
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
import pytest
from django.db import OperationalError, connection, connections
from psycopg.errors import AdminShutdown, CannotConnectNow, TooManyConnections

from model_hub.models.choices import CellStatus
from model_hub.tests.test_eval_metadata_retry import ReadFault, run_case
from model_hub.utils.eval_metadata import _is_connection_failure

db_backed = pytest.mark.django_db(transaction=True, databases="__all__")

PATHS = ["threaded", "direct", "preview"]
TABLES = ["model_hub_column", "accounts_workspace"]

# (SQLSTATE the server sends, message). PostgreSQL texts as emitted by the
# postmaster and InitPostgres; PgBouncer texts as sent by its pooler.
LOGIN_UNAVAILABLE = {
    "starting_up": ("57P03", "the database system is starting up"),
    "shutting_down": ("57P03", "the database system is shutting down"),
    "recovery_mode": ("57P03", "the database system is in recovery mode"),
    "not_yet_accepting": (
        "57P03",
        "the database system is not yet accepting connections",
    ),
    "standby_not_accepting": (
        "57P03",
        "the database system is not accepting connections",
    ),
    "too_many_clients": ("53300", "sorry, too many clients already"),
    "reserved_slots": (
        "53300",
        "remaining connection slots are reserved for roles with the SUPERUSER "
        "attribute",
    ),
    "role_limit": ("53300", 'too many connections for role "metadata_test"'),
    "database_limit": (
        "53300",
        'too many connections for database "metadata_test"',
    ),
    "pool_client_limit": ("08P01", "no more connections allowed (max_client_conn)"),
    "pool_login_backoff": (
        "08P01",
        "server login has been failing, try again later (server_login_retry)",
    ),
    "pool_fast_fail": ("08P01", "pgbouncer cannot connect to server"),
    "pool_server_down": ("08P01", "client_login_timeout (server down)"),
    "pool_wait_timeout": ("08P01", "query_wait_timeout"),
}

LOGIN_MISCONFIGURED = {
    "bad_password": (
        "28P01",
        'password authentication failed for user "metadata_test"',
    ),
    "missing_database": ("3D000", 'database "metadata_missing" does not exist'),
    "missing_role": ("28000", 'role "metadata_missing" does not exist'),
    "no_hba_entry": (
        "28000",
        'no pg_hba.conf entry for host "127.0.0.1", user "metadata_test", '
        'database "metadata_test", no encryption',
    ),
    "login_not_permitted": ("28000", 'role "metadata_test" is not permitted to log in'),
    "database_closed": (
        "55000",
        'database "metadata_test" is not currently accepting connections',
    ),
    "no_connect_privilege": ("42501", 'permission denied for database "metadata_test"'),
    "pool_unknown_database": ("08P01", "no such database: metadata_missing"),
}

_SSL_REQUEST = 80877103
_GSSENC_REQUEST = 80877104


def _recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("client went away")
        data += chunk
    return data


class LoginRejectingServer:
    """Loopback server that answers every login with one FATAL ErrorResponse.

    It declines SSL/GSS negotiation, reads the startup packet, sends the error,
    then half-closes and drains so the client sees an orderly end of stream
    rather than a reset (which libpq would report as a transport failure).
    """

    def __init__(self, code, message):
        fields = b"SFATAL\0VFATAL\0C%s\0M%s\0\0" % (code.encode(), message.encode())
        self.response = b"E" + (len(fields) + 4).to_bytes(4, "big") + fields
        self.logins = 0
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._stop.set()
        self._thread.join(timeout=5)
        self._listener.close()

    def _serve(self):
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with client, contextlib.suppress(OSError, ConnectionError):
                client.settimeout(5)
                self._reject(client)

    def _reject(self, client):
        while True:
            length = int.from_bytes(_recv_exact(client, 4), "big")
            request = int.from_bytes(_recv_exact(client, length - 4)[:4], "big")
            if request not in (_SSL_REQUEST, _GSSENC_REQUEST):
                break
            client.sendall(b"N")
        self.logins += 1
        client.sendall(self.response)
        client.shutdown(socket.SHUT_WR)
        while client.recv(4096):
            pass


def login_rejection(code, message):
    with LoginRejectingServer(code, message) as server:
        with pytest.raises(psycopg.OperationalError) as failure:
            psycopg.connect(
                host="127.0.0.1",
                port=server.port,
                user="metadata_test",
                dbname="metadata_test",
                connect_timeout=3,
            )
    error = failure.value
    # The premise for matching texts at all: libpq does not expose the code.
    assert type(error) is psycopg.OperationalError
    assert error.sqlstate is None
    assert f"FATAL:  {message}" in str(error)
    return error


def django_error(driver_error):
    error = OperationalError(str(driver_error))
    error.__cause__ = driver_error
    return error


def classify(driver_error, driver_connection=None):
    database = SimpleNamespace(connection=driver_connection)
    return _is_connection_failure(django_error(driver_error), database)


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(LOGIN_UNAVAILABLE))
def test_login_rejected_while_unavailable_is_connection_failure(kind):
    assert classify(login_rejection(*LOGIN_UNAVAILABLE[kind])) is True


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(LOGIN_MISCONFIGURED))
def test_login_rejected_by_configuration_is_not_retried(kind):
    driver = login_rejection(*LOGIN_MISCONFIGURED[kind])
    assert classify(driver) is False
    # A broken driver connection must not override a configuration fault.
    assert classify(driver, SimpleNamespace(broken=True)) is False


@pytest.mark.unit
def test_configuration_fault_takes_precedence_over_unavailability():
    # libpq joins per-attempt failures (e.g. SSL then plaintext) in one message.
    driver = psycopg.OperationalError(
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
        'FATAL:  password authentication failed for user "metadata_test"\n'
        'connection to server at "127.0.0.1", port 5432 failed: '
        "FATAL:  the database system is starting up"
    )
    assert classify(driver) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "driver",
    [
        CannotConnectNow("the database system is starting up"),
        TooManyConnections("sorry, too many clients already"),
    ],
)
def test_login_unavailability_with_sqlstate_is_connection_failure(driver):
    assert classify(driver) is True


def test_real_server_missing_database_is_not_retried():
    settings = connections["default"].settings_dict
    with pytest.raises(psycopg.OperationalError) as failure:
        psycopg.connect(
            host=settings["HOST"],
            port=settings["PORT"],
            user=settings["USER"],
            password=settings["PASSWORD"],
            dbname="metadata_missing_database",
            connect_timeout=5,
        )
    assert failure.value.sqlstate is None
    assert "does not exist" in str(failure.value)
    assert classify(failure.value) is False


def assert_evaluated_once(case, path, result):
    assert result.get("reason") == "matched", result
    case.logger.assert_called_once()
    if path == "preview":
        case.evaluator.run.assert_called_once_with(text="sample")
        assert result["output"] == "1.0"
    else:
        assert case.output_cell.status == CellStatus.PASS.value
        assert case.output_cell.value == "1.0"
        case.runner._run_evaluation.assert_called_once()
        case.runner._emit_cell_usage.assert_called_once()


def assert_not_started(case, result):
    assert result.get("error_code") == "EVALUATION_METADATA_UNAVAILABLE", result
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()
    case.evaluator.run.assert_not_called()
    case.runner._emit_cell_usage.assert_not_called()


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_transient_login_rejection_recovers(evaluation_case, path, table):
    driver = login_rejection(*LOGIN_UNAVAILABLE["starting_up"])
    fault = ReadFault(table, error_factory=lambda: django_error(driver))
    with connection.execute_wrapper(fault):
        result = run_case(evaluation_case, path)
    assert fault.calls == 2, result
    assert_evaluated_once(evaluation_case, path, result)


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_persistent_login_rejection_is_infrastructure(evaluation_case, path, table):
    driver = login_rejection(*LOGIN_UNAVAILABLE["pool_client_limit"])
    fault = ReadFault(table, failures=10, error_factory=lambda: django_error(driver))
    with connection.execute_wrapper(fault):
        result = run_case(evaluation_case, path)
    assert fault.calls == 3, result
    assert_not_started(evaluation_case, result)


@contextlib.contextmanager
def reconnects_rejected(fault, server, count, **overrides):
    """Send the next `count` reconnects after the metadata fault to `server`.

    Entry points call close_old_connections and may reconnect before the
    metadata read, so only reconnects made by the retry itself are redirected.
    `overrides` are extra libpq parameters for the redirected reconnects.
    """
    database = connections["default"]
    connect = database.get_new_connection
    state = SimpleNamespace(redirected=0)

    def get_new_connection(conn_params):
        if fault.calls >= 1 and state.redirected < count:
            state.redirected += 1
            conn_params = {
                **conn_params,
                **overrides,
                "host": "127.0.0.1",
                "port": server.port,
            }
        return connect(conn_params)

    with patch.object(database, "get_new_connection", get_new_connection):
        yield state


def terminated_backend():
    return django_error(
        AdminShutdown("terminating connection due to administrator command")
    )


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_reconnect_rejected_at_login_is_retried(evaluation_case, path, table):
    # Attempt 1 loses its session; the retry's real reconnect is refused while
    # the server restarts; attempt 3 reconnects and reads the metadata.
    fault = ReadFault(table, error_factory=terminated_backend)
    with LoginRejectingServer(*LOGIN_UNAVAILABLE["starting_up"]) as server:
        with (
            reconnects_rejected(fault, server, count=1) as reconnects,
            connection.execute_wrapper(fault),
        ):
            result = run_case(evaluation_case, path)
    assert (reconnects.redirected, server.logins) == (1, 1)
    assert fault.calls == 2, result
    assert_evaluated_once(evaluation_case, path, result)


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_reconnect_rejected_until_exhausted_is_infrastructure(
    evaluation_case, path, table
):
    # Both retry reconnects are refused at the pool limit. The result is the
    # infrastructure outcome, not the raw driver error as an evaluation error.
    fault = ReadFault(table, error_factory=terminated_backend)
    with LoginRejectingServer(*LOGIN_UNAVAILABLE["pool_client_limit"]) as server:
        with (
            reconnects_rejected(fault, server, count=2) as reconnects,
            connection.execute_wrapper(fault),
        ):
            result = run_case(evaluation_case, path)
    assert (reconnects.redirected, server.logins) == (2, 2)
    assert fault.calls == 1, result
    assert_not_started(evaluation_case, result)
