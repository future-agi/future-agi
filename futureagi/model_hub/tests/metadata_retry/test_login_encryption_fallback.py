"""Login refusals that libpq joins across its sslmode=prefer attempts.

Under sslmode=prefer (libpq's default) a login the server refuses over TLS
before authentication is retried in plaintext, and libpq reports both refusals
in one message without a SQLSTATE. PostgreSQL says "sorry, too many clients
already" before it reads pg_hba.conf, so if a slot frees between the two
attempts an SSL-only server refuses the plaintext attempt in pg_hba.conf
instead. That pair is a capacity refusal and gets the bounded retry. A
pg_hba.conf or authentication refusal with no capacity refusal beside it is
configuration and is not retried.

The joined errors come from real libpq connection attempts against a loopback
server that completes the TLS upgrade, plus one message as libpq reported it
for a real PostgreSQL 16 server. Synthetic data only.
"""

import contextlib
import datetime
import socket
import ssl
import threading
from types import SimpleNamespace

import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.db import connection

from model_hub.tests.metadata_retry.test_login_rejection import (
    PATHS,
    TABLES,
    assert_evaluated_once,
    assert_not_started,
    classify,
    db_backed,
    reconnects_rejected,
    terminated_backend,
)
from model_hub.tests.test_eval_metadata_retry import ReadFault, run_case

_SSL_REQUEST = 80877103
_GSSENC_REQUEST = 80877104

# (SQLSTATE the server sends, message), as PostgreSQL words them.
TOO_MANY_CLIENTS = ("53300", "sorry, too many clients already")
HBA_REJECTS_PLAINTEXT = (
    "28000",
    'pg_hba.conf rejects connection for host "127.0.0.1", user "metadata_test", '
    'database "metadata_test", no encryption',
)
NO_HBA_ENTRY_FOR_PLAINTEXT = (
    "28000",
    'no pg_hba.conf entry for host "127.0.0.1", user "metadata_test", '
    'database "metadata_test", no encryption',
)
NO_HBA_ENTRY_FOR_TLS = (
    "28000",
    'no pg_hba.conf entry for host "127.0.0.1", user "metadata_test", '
    'database "metadata_test", SSL encryption',
)
PASSWORD_FAILED = (
    "28P01",
    'password authentication failed for user "metadata_test"',
)
MISSING_ROLE = ("28000", 'role "metadata_missing" does not exist')

# libpq 18 (psycopg-binary 3.3) against PostgreSQL 16 with hostssl/hostnossl
# reject rules: the server was full for the TLS attempt and a slot freed before
# the plaintext attempt.
REAL_SERVER_CAPACITY_THEN_HBA = (
    'connection failed: connection to server at "127.0.0.1", port 40423 failed: '
    "FATAL:  sorry, too many clients already\n"
    'connection to server at "127.0.0.1", port 40423 failed: '
    'FATAL:  pg_hba.conf rejects connection for host "172.17.0.1", user "bulk", '
    'database "qa", no encryption'
)

# The TLS attempt's refusal, then the plaintext retry's.
CAPACITY_REFUSALS = {
    "capacity_then_hba_reject": [
        ("ssl", *TOO_MANY_CLIENTS),
        ("plain", *HBA_REJECTS_PLAINTEXT),
    ],
    "capacity_then_no_hba_entry": [
        ("ssl", *TOO_MANY_CLIENTS),
        ("plain", *NO_HBA_ENTRY_FOR_PLAINTEXT),
    ],
    # A server that refuses TLS in pg_hba.conf and is full for plaintext.
    "hba_for_tls_then_capacity": [
        ("ssl", *NO_HBA_ENTRY_FOR_TLS),
        ("plain", *TOO_MANY_CLIENTS),
    ],
    "capacity_twice": [
        ("ssl", *TOO_MANY_CLIENTS),
        ("plain", *TOO_MANY_CLIENTS),
    ],
}

CONFIGURATION_REFUSALS = {
    "hba_for_both": [
        ("ssl", *NO_HBA_ENTRY_FOR_TLS),
        ("plain", *HBA_REJECTS_PLAINTEXT),
    ],
    "password_then_hba": [
        ("ssl", *PASSWORD_FAILED),
        ("plain", *HBA_REJECTS_PLAINTEXT),
    ],
    "capacity_then_password": [
        ("ssl", *TOO_MANY_CLIENTS),
        ("plain", *PASSWORD_FAILED),
    ],
    "capacity_then_missing_role": [
        ("ssl", *TOO_MANY_CLIENTS),
        ("plain", *MISSING_ROLE),
    ],
}


@pytest.fixture(scope="module")
def server_tls(tmp_path_factory):
    """Server-side TLS context with a throwaway self-signed certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    directory = tmp_path_factory.mktemp("login-tls")
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def _recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("client went away")
        data += chunk
    return data


def _error_response(code, message):
    fields = b"SFATAL\0VFATAL\0C%s\0M%s\0\0" % (code.encode(), message.encode())
    return b"E" + (len(fields) + 4).to_bytes(4, "big") + fields


class EncryptionFallbackServer:
    """Loopback server that refuses each login with the next scripted FATAL.

    Steps are (transport, SQLSTATE, message), used in turn for each accepted
    connection and then repeated. An "ssl" step accepts the SSLRequest and
    completes the TLS upgrade before refusing, as PostgreSQL does for a refusal
    raised after the upgrade; a "plain" step declines encryption. `logins`
    records the transport each refused login actually used.
    """

    def __init__(self, steps, tls):
        self.steps = list(steps)
        self.logins = []
        self._tls = tls
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
        accepted = 0
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            step = self.steps[accepted % len(self.steps)]
            accepted += 1
            with client, contextlib.suppress(OSError, ConnectionError):
                client.settimeout(5)
                self._refuse(client, *step)

    def _refuse(self, client, transport, code, message):
        sock = client
        try:
            while True:
                length = int.from_bytes(_recv_exact(sock, 4), "big")
                request = int.from_bytes(_recv_exact(sock, length - 4)[:4], "big")
                if request == _SSL_REQUEST and transport == "ssl" and sock is client:
                    sock.sendall(b"S")
                    sock = self._tls.wrap_socket(client, server_side=True)
                    continue
                if request in (_SSL_REQUEST, _GSSENC_REQUEST):
                    sock.sendall(b"N")
                    continue
                break
            self.logins.append("plain" if sock is client else "ssl")
            sock.sendall(_error_response(code, message))
            # Drain until the client hangs up, so libpq reads the refusal
            # rather than a reset it would report as a transport failure.
            while sock.recv(4096):
                pass
        finally:
            if sock is not client:
                sock.close()


def prefer_login_failure(steps, tls):
    with EncryptionFallbackServer(steps, tls) as server:
        with pytest.raises(psycopg.OperationalError) as failure:
            psycopg.connect(
                host="127.0.0.1",
                port=server.port,
                user="metadata_test",
                dbname="metadata_test",
                sslmode="prefer",
                connect_timeout=5,
            )
    error = failure.value
    # The premise: libpq retried the TLS refusal in plaintext and reported both
    # refusals in one message, without the server's SQLSTATE.
    assert server.logins == ["ssl", "plain"]
    assert type(error) is psycopg.OperationalError
    assert error.sqlstate is None
    for _transport, _code, message in steps:
        assert f"FATAL:  {message}" in str(error)
    return error


@pytest.mark.unit
def test_real_server_capacity_then_plaintext_hba_refusal_is_connection_failure():
    driver = psycopg.OperationalError(REAL_SERVER_CAPACITY_THEN_HBA)
    assert classify(driver) is True


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(CAPACITY_REFUSALS))
def test_capacity_refusal_on_either_attempt_is_connection_failure(server_tls, kind):
    assert classify(prefer_login_failure(CAPACITY_REFUSALS[kind], server_tls)) is True


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(CONFIGURATION_REFUSALS))
def test_configuration_refusal_without_capacity_is_not_retried(server_tls, kind):
    driver = prefer_login_failure(CONFIGURATION_REFUSALS[kind], server_tls)
    assert classify(driver) is False
    # A broken driver connection must not override a configuration fault.
    assert classify(driver, SimpleNamespace(broken=True)) is False


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_reconnect_refused_at_capacity_then_by_hba_is_retried(
    evaluation_case, server_tls, path, table
):
    # Attempt 1 loses its session. The retry's real reconnect is refused at
    # capacity over TLS and by pg_hba.conf in plaintext; the next one reads.
    fault = ReadFault(table, error_factory=terminated_backend)
    steps = CAPACITY_REFUSALS["capacity_then_hba_reject"]
    with EncryptionFallbackServer(steps, server_tls) as server:
        with (
            reconnects_rejected(fault, server, count=1, sslmode="prefer") as reconnects,
            connection.execute_wrapper(fault),
        ):
            result = run_case(evaluation_case, path)
    assert (reconnects.redirected, server.logins) == (1, ["ssl", "plain"])
    assert fault.calls == 2, result
    assert_evaluated_once(evaluation_case, path, result)


@db_backed
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("table", TABLES)
def test_reconnect_refused_at_capacity_until_exhausted_is_infrastructure(
    evaluation_case, server_tls, path, table
):
    # Both retry reconnects meet the same refusals. The result is the
    # infrastructure outcome, not the raw driver error as an evaluation error.
    fault = ReadFault(table, error_factory=terminated_backend)
    steps = CAPACITY_REFUSALS["capacity_then_hba_reject"]
    with EncryptionFallbackServer(steps, server_tls) as server:
        with (
            reconnects_rejected(fault, server, count=2, sslmode="prefer") as reconnects,
            connection.execute_wrapper(fault),
        ):
            result = run_case(evaluation_case, path)
    assert (reconnects.redirected, server.logins) == (2, ["ssl", "plain"] * 2)
    assert fault.calls == 1, result
    assert_not_started(evaluation_case, result)
