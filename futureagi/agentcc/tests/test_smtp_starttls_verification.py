"""SMTP STARTTLS verifies certificates before sending tenant credentials.

No database or live service is needed. Only the address-validation seam is
replaced for the loopback fixture; the actual pinned socket and TLS handshake
run under the repository's no-egress guard. Certificates/keys are generated
per test in temporary directories, never stored in the repository.
"""

import io
import smtplib
import socket
import ssl
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agentcc.services import email_sender
from model_hub.tests.network_isolation import GUARD

HOST = "smtp.example.test"
CONFIG = {
    "host": HOST,
    "port": 587,
    "from_email": "alerts@example.test",
    "username": "synthetic-user",
    "password": "synthetic-password",
    "use_tls": True,
}
RECIPIENTS = ["recipient@example.test"]


def _certificate(directory, hostname):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path = Path(directory) / "certificate.pem"
    key_path = Path(directory) / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@contextmanager
def _starttls_server(hostname):
    """Single-connection SMTP fixture; a TLS failure is recorded, not suppressed."""
    with tempfile.TemporaryDirectory(prefix="smtp-starttls-") as directory:
        certificate, key = _certificate(directory, hostname)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        seen = SimpleNamespace(
            sni=[], tls_errors=[], errors=[], certificate=certificate
        )
        context.set_servername_callback(lambda sock, name, ctx: seen.sni.append(name))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(5)
            seen.port = listener.getsockname()[1]

            def serve():
                try:
                    connection, _ = listener.accept()
                    connection.settimeout(5)
                    with connection:
                        connection.sendall(b"220 loopback SMTP fixture\r\n")
                        with connection.makefile("rb") as stream:
                            assert stream.readline().upper().startswith(b"EHLO ")
                            connection.sendall(b"250-fixture\r\n250 STARTTLS\r\n")
                            assert stream.readline() == b"STARTTLS\r\n"
                            connection.sendall(b"220 Ready for TLS\r\n")
                        try:
                            secured = context.wrap_socket(connection, server_side=True)
                        except ssl.SSLError as exc:
                            seen.tls_errors.append(exc)
                            return
                        with secured, secured.makefile("rb") as stream:
                            # login/sendmail are spies; only real quit follows TLS.
                            assert stream.readline() == b"quit\r\n"
                            secured.sendall(b"221 Goodbye\r\n")
                except Exception as exc:
                    seen.errors.append(exc)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                yield seen
            finally:
                worker.join(timeout=6)
                assert not worker.is_alive(), "SMTP fixture failed to terminate"
                assert not seen.errors, seen.errors


class TestSMTPStartTLSVerification(unittest.TestCase):
    def setUp(self):
        # This focused suite needs no configured database/compose DNS discovery.
        self.enterContext(
            patch(
                "model_hub.tests.network_isolation.configured_service_hosts",
                return_value=(),
            )
        )
        self.attempts = self.enterContext(GUARD.watch())
        self.addCleanup(self.assertEqual, self.attempts, [])
        # smtplib's local EHLO identity must not perform host DNS discovery.
        self.enterContext(patch("socket.getfqdn", return_value="client.example.test"))

    def test_starttls_receives_a_verifying_context_and_hostname(self):
        with (
            patch.object(
                email_sender, "smtp_connect_addresses", return_value=["127.0.0.1"]
            ),
            patch.object(email_sender, "_PinnedSMTP") as smtp,
        ):
            result = email_sender._send_via_smtp(CONFIG, RECIPIENTS, "Subject", "Body")

        self.assertEqual(result, {"success": True})
        smtp.assert_called_once_with(["127.0.0.1"], host=HOST, port=587, timeout=15)
        smtp.return_value.starttls.assert_called_once()
        context = smtp.return_value.starttls.call_args.kwargs.get("context")
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        smtp.return_value.login.assert_called_once_with(
            CONFIG["username"], CONFIG["password"]
        )

    def test_pinned_socket_preserves_smtplib_hostname(self):
        connection = MagicMock()
        connection.makefile.return_value = io.BytesIO(b"220 fixture\r\n")
        with patch("socket.create_connection", return_value=connection) as dial:
            smtp = email_sender._PinnedSMTP(
                ["127.0.0.1"],
                host=HOST,
                port=587,
                timeout=15,
                local_hostname="client.example.test",
            )
            try:
                self.assertEqual(smtp._host, HOST)
                dial.assert_called_once_with(("127.0.0.1", 587), 15, None)
            finally:
                smtp.close()

    def _deliver_to_fixture(self, server, *, trust_certificate=False):
        config = {**CONFIG, "port": server.port}
        alert = SimpleNamespace(
            id="synthetic-alert", provider="smtp", encrypted_config=b"fixture"
        )
        create_context = ssl.create_default_context

        def test_context():
            context = create_context()
            if trust_certificate:
                context.load_verify_locations(cafile=str(server.certificate))
            return context

        with (
            patch.object(
                email_sender, "smtp_connect_addresses", return_value=["127.0.0.1"]
            ),
            patch.object(
                email_sender.CredentialManager, "decrypt", return_value=config
            ),
            patch.object(
                email_sender.ssl, "create_default_context", side_effect=test_context
            ),
            patch.object(smtplib.SMTP, "login", autospec=True) as login,
            patch.object(smtplib.SMTP, "sendmail", autospec=True) as sendmail,
            patch.object(
                email_sender, "_failure_reason", wraps=email_sender._failure_reason
            ) as failure,
            patch.object(email_sender, "logger"),
        ):
            result = email_sender.deliver_email(alert, RECIPIENTS, "Subject", "Body")
        return result, login, sendmail, failure

    def _assert_rejected(self, server, *, trust_certificate=False):
        result, login, sendmail, failure = self._deliver_to_fixture(
            server, trust_certificate=trust_certificate
        )
        # Check credential exclusion even if delivery returned a misleading result.
        login.assert_not_called()
        sendmail.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], email_sender.SEND_FAILED)
        self.assertEqual(
            email_sender.public_error(result["error"]), email_sender.SEND_FAILED
        )
        failure.assert_called_once()
        provider, error = failure.call_args.args
        self.assertEqual(provider, "smtp")
        self.assertIsInstance(error, ssl.SSLCertVerificationError)
        self.assertIsInstance(error, ssl.SSLError)
        self.assertTrue(result["detail"].startswith("SSLCertVerificationError:"))
        self.assertEqual(server.sni, [HOST])

    def test_self_signed_certificate_fails_closed_before_login(self):
        with _starttls_server(HOST) as server:
            self._assert_rejected(server)
        self.assertEqual(len(server.tls_errors), 1)

    def test_trusted_wrong_hostname_fails_closed_before_login(self):
        with _starttls_server("wrong.example.test") as server:
            self._assert_rejected(server, trust_certificate=True)
        self.assertEqual(len(server.tls_errors), 1)

    def test_trusted_matching_hostname_can_authenticate(self):
        with _starttls_server(HOST) as server:
            result, login, sendmail, failure = self._deliver_to_fixture(
                server, trust_certificate=True
            )
            self.assertEqual(result, {"success": True})
            login.assert_called_once()
            smtp, username, password = login.call_args.args
            self.assertEqual(smtp._host, HOST)
            self.assertEqual(
                (username, password), (CONFIG["username"], CONFIG["password"])
            )
            sendmail.assert_called_once()
            failure.assert_not_called()
            self.assertEqual(server.sni, [HOST])
        self.assertEqual(server.tls_errors, [])

    def test_explicit_cleartext_mode_is_unchanged(self):
        # Recorded residual: use_tls=False still permits plaintext authentication.
        with (
            patch.object(
                email_sender, "smtp_connect_addresses", return_value=["127.0.0.1"]
            ),
            patch.object(email_sender, "_PinnedSMTP") as smtp,
        ):
            result = email_sender._send_via_smtp(
                {**CONFIG, "use_tls": False}, RECIPIENTS, "Subject", "Body"
            )
        self.assertEqual(result, {"success": True})
        smtp.return_value.starttls.assert_not_called()
        smtp.return_value.login.assert_called_once_with(
            CONFIG["username"], CONFIG["password"]
        )
