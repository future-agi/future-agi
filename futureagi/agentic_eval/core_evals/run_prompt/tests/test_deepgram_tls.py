"""TLS regressions: no provider calls, with one wrong-cert loopback handshake.

For isolated cloud execution, disable plugin autoload and use this directory as
--confcutdir with -c /dev/null (the parent conftests bootstrap Django/services).
The cloud runner must install its fail-closed egress guard before collection;
only test_wrong_certificate_rejected_before_authorization may use loopback.
"""

import ast
import importlib.util
import ipaddress
import socket
import ssl
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_RUN_PROMPT = Path(__file__).resolve().parents[1]
_TIMEOUT = 5


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    """Deny DNS and outbound sockets except the one explicitly allowed endpoint.

    Wrap (never replace/bypass) any outer runner guard. The allowlist stays empty
    for the mock/source tests and contains only the TLS listener for loopback.
    """
    allowed = set()
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    sendto = socket.socket.sendto
    getaddrinfo = socket.getaddrinfo

    def require_allowed(address):
        if address not in allowed:
            raise AssertionError("TLS regression attempted a non-allowlisted socket")

    def guarded_connect(sock, address):
        require_allowed(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        require_allowed(address)
        return connect_ex(sock, address)

    def guarded_sendto(sock, data, *args):
        require_allowed(args[-1])
        return sendto(sock, data, *args)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        require_allowed((host, port))
        return getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", guarded_sendto)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    return allowed


@pytest.fixture
def deepgram():
    """Execute the real module; stub only its unused audio/storage dependency."""
    source = _RUN_PROMPT / "other_services" / "deepgram_response.py"
    spec = importlib.util.spec_from_file_location("_deepgram_tls_under_test", source)
    module = importlib.util.module_from_spec(spec)
    storage = ModuleType("tfc.utils.storage")
    storage.audio_bytes_from_url_or_base64 = Mock(
        side_effect=AssertionError("TLS tests must not load audio/storage")
    )
    with patch.dict(sys.modules, {"tfc.utils.storage": storage}):
        spec.loader.exec_module(module)
    return module


def test_websocket_requires_verified_certificate_and_ca_bundle(deepgram):
    # Abort at the connection boundary: no sender/receiver threads or networking.
    with patch.object(
        deepgram.websocket,
        "create_connection",
        side_effect=ConnectionAbortedError("mock connection boundary"),
    ) as connect:
        with pytest.raises(ConnectionAbortedError, match="mock connection boundary"):
            deepgram._transcribe_via_websocket(
                "wss://127.0.0.1:1/v2/listen", b"", "not-a-provider-key"
            )

    connect.assert_called_once()
    options = connect.call_args.kwargs["sslopt"]
    assert options["cert_reqs"] is ssl.CERT_REQUIRED
    assert options.get("check_hostname") is not False
    assert Path(options["ca_certs"]).is_file()
    # A real, loadable trust bundle, not merely a filename or an empty file.
    context = ssl.create_default_context(cafile=options["ca_certs"])
    assert context.get_ca_certs()


def _self_signed_server_context(tmp_path):
    """Generate a disposable localhost key/cert; never read repository keys."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = tmp_path / "throwaway-key.pem"
    key_path.touch(mode=0o600, exist_ok=False)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path = tmp_path / "throwaway-cert.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    return context


def test_wrong_certificate_rejected_before_authorization(
    deepgram, tmp_path, network_guard
):
    server_context = _self_signed_server_context(tmp_path)
    application_bytes = bytearray()
    accepted = []
    server_errors = []

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.settimeout(_TIMEOUT)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        endpoint = listener.getsockname()
        network_guard.add(endpoint)

        def serve_once():
            try:
                raw, peer = listener.accept()
                accepted.append(peer)
                with raw:
                    raw.settimeout(_TIMEOUT)
                    with server_context.wrap_socket(raw, server_side=True) as tls:
                        # If verification regresses, capture the HTTP upgrade and
                        # close immediately: never wait for audio or a transcript.
                        application_bytes.extend(tls.recv(8192))
            except Exception as error:
                server_errors.append(error)

        server = threading.Thread(target=serve_once, daemon=True)
        server.start()
        real_connect = deepgram.websocket.create_connection

        def bounded_connect(url, **kwargs):
            # Preserve the production sslopt/header exactly; only bound waits and
            # prevent ambient proxy settings from routing loopback off-host.
            return real_connect(
                url, timeout=_TIMEOUT, http_no_proxy=["127.0.0.1"], **kwargs
            )

        try:
            with patch.object(deepgram.websocket, "create_connection", bounded_connect):
                with pytest.raises(ssl.SSLCertVerificationError) as rejected:
                    deepgram._transcribe_via_websocket(
                        f"wss://127.0.0.1:{endpoint[1]}/v2/listen",
                        b"\x00\x00",
                        "not-a-provider-key",
                    )
            assert rejected.value.verify_code == 18  # self-signed leaf certificate
        finally:
            server.join(timeout=2 * _TIMEOUT)
            assert not server.is_alive(), "TLS server did not stop within its deadline"

    assert len(accepted) == 1, "The real TLS handshake must reach the loopback server"
    assert len(server_errors) == 1 and isinstance(server_errors[0], ssl.SSLError)
    assert application_bytes == b"", "HTTP upgrade bytes reached an untrusted server"
    assert b"authorization:" not in application_bytes.lower()


def _tls_bypasses(tree):
    """Find bypass symbols and disabled verification in calls, dicts and assigns."""
    failures = []
    forbidden = {"CERT_NONE", "_create_unverified_context"}
    options = {"verify", "check_hostname"}

    def disabled(name, value, line):
        if name in options and isinstance(value, ast.Constant) and value.value is False:
            failures.append((line, f"{name}=False"))

    for node in ast.walk(tree):
        symbol = None
        if isinstance(node, ast.Attribute):
            symbol = node.attr
        elif isinstance(node, ast.Name):
            symbol = node.id
        elif isinstance(node, ast.alias):
            # Also catches `from ssl import CERT_NONE as disabled_verification`.
            symbol = node.name.rsplit(".", 1)[-1]
        if symbol in forbidden:
            failures.append((node.lineno, symbol))
        if isinstance(node, ast.keyword):
            disabled(node.arg, node.value, node.lineno)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant):
                    disabled(key.value, value, key.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = target.attr if isinstance(target, ast.Attribute) else None
                if isinstance(target, ast.Name):
                    name = target.id
                disabled(name, node.value, node.lineno)
    return failures


def test_run_prompt_source_has_no_tls_verification_bypasses():
    failures = []
    modules = sorted(_RUN_PROMPT.rglob("*.py"))
    assert modules, "The guard must scan the real run_prompt source tree"
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for line, reason in _tls_bypasses(tree):
            failures.append(f"{module.relative_to(_RUN_PROMPT)}:{line}: {reason}")
    assert not failures, "TLS verification bypasses:\n" + "\n".join(failures)


def test_source_guard_detects_bypasses_without_flagging_strings():
    for source in (
        "sslopt = {'cert_reqs': ssl.CERT_NONE}",
        "from ssl import CERT_NONE as unsafe",
        "requests.get(url, verify=False)",
        "sslopt = {'check_hostname': False}",
        "ws.connect(url, check_hostname=False)",
        "context.check_hostname = False",
        "verify: bool = False",
        "ssl._create_unverified_context()",
        "from ssl import _create_unverified_context as unsafe",
    ):
        assert _tls_bypasses(ast.parse(source)), source
    assert not _tls_bypasses(
        ast.parse(
            "# ssl.CERT_NONE is forbidden\n"
            "message = 'verify=False'\n"
            "sslopt = {'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True}\n"
        )
    )
