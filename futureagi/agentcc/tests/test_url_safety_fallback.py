"""ALERT-ACCESS-1 round 2: a pinned connection falls back across the checked
addresses.

Round 1 pinned build_ssrf_safe_session and the SMTP send to the first checked
address only. A host whose first address cannot be reached (an IPv6 address on
a network without an IPv6 route, one refusing server of several) then failed,
where urllib3 and smtplib try the resolver's next address. Now both try every
checked address in the resolver's order, and only those:

- the name is resolved once, for the check; nothing resolves it again, so an
  answer that changes after the check is never dialed;
- only a connection that was never opened falls through to the next address
  (refused, unreachable, connect timeout); a request whose connection opened is
  never sent a second time;
- the Host header (and SNI, see test_url_safety_pinning) stay on the name, and
  each redirect hop is checked and pinned again.

Synthetic only: DNS answers are stubbed, the "public" addresses are routed
in-process to a server on 127.0.0.1 or refused, and nothing leaves the test.
"""

import errno
import ipaddress
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
import requests

from agentcc.services.email_sender import _PinnedSMTP
from agentcc.services.url_safety import build_ssrf_safe_session

BLOCKED = "Connection to private address blocked"
V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
V4 = "93.184.216.34"
SECOND_V4 = "93.184.216.35"
NAME = "dual.stack.test"
PORT = 8080


def _answer(addr, port):
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    number = int(port) if isinstance(port, int) or str(port or "").isdigit() else 0
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, number))


def _dns(answers):
    """A getaddrinfo stub: IP literals answer themselves; a name in
    ``answers`` answers its addresses in order, one list per lookup (the last
    list repeats); any other name does not resolve. Returns (stub, lookups)."""
    lookups = []

    def getaddrinfo(host, port=None, *args, **kwargs):
        lookups.append(host)
        try:
            return [_answer(str(ipaddress.ip_address(host)), port)]
        except ValueError:
            pass
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        rounds = answers[host]
        addrs = rounds[min(lookups.count(host), len(rounds)) - 1]
        return [_answer(addr, port) for addr in addrs]

    return getaddrinfo, lookups


class _Receiver(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        self.do_POST()

    def do_POST(self):  # noqa: N802 (http.server's name)
        length = int(self.headers.get("Content-Length") or 0)
        self.server.seen.append(
            {
                "host": self.headers.get("Host"),
                "path": self.path,
                "body": self.rfile.read(length),
            }
        )
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    """An HTTP server on 127.0.0.1 that records what it was sent."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def dropper():
    """A server on 127.0.0.1 that reads one request and hangs up unanswered."""
    listener = socket.create_server(("127.0.0.1", 0))
    got = []

    def serve():
        conn, _ = listener.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            got.append(data)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], got
    thread.join(timeout=5)
    listener.close()


def _route(routes):
    """Patch socket.socket.connect: an address in ``routes`` connects to the
    local port it maps to, or raises the exception it maps to; any other
    address is refused. Returns (patcher, dials)."""
    real_connect = socket.socket.connect
    dials = []

    def connect(sock, address):
        dials.append(tuple(address[:2]))
        target = routes.get(address[0])
        if isinstance(target, int):
            return real_connect(sock, ("127.0.0.1", target))
        raise target or ConnectionRefusedError(errno.ECONNREFUSED, "synthetic")

    return patch.object(socket.socket, "connect", connect), dials


FIRST_ADDRESS_FAILURES = [
    ConnectionRefusedError(errno.ECONNREFUSED, "synthetic: refused"),
    OSError(errno.ENETUNREACH, "synthetic: no IPv6 route"),
    TimeoutError("synthetic: connect timed out"),
]


@pytest.mark.parametrize(
    "failure", FIRST_ADDRESS_FAILURES, ids=lambda exc: type(exc).__name__
)
def test_a_dual_stack_host_falls_back_to_its_second_checked_address(receiver, failure):
    # The check's answer is [V6, V4]; any later answer is loopback, which
    # nothing may ask for.
    getaddrinfo, lookups = _dns({NAME: [[V6, V4], ["127.0.0.1"]]})
    connect, dials = _route({V6: failure, V4: receiver.server_address[1]})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo), connect:
        response = session.post(f"http://{NAME}:{PORT}/hook", data=b"{}", timeout=5)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert dials == [(V6, PORT), (V4, PORT)]
    assert lookups.count(NAME) == 1
    (request,) = receiver.seen
    assert request["host"] == f"{NAME}:{PORT}"
    assert (request["path"], request["body"]) == ("/hook", b"{}")


def test_every_checked_address_is_tried_in_order_and_no_other():
    getaddrinfo, lookups = _dns({NAME: [[V6, V4, SECOND_V4], ["127.0.0.1"]]})
    connect, dials = _route({})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo), connect:
        with pytest.raises(requests.exceptions.ConnectionError):
            session.get(f"http://{NAME}:{PORT}/models", timeout=5)

    assert dials == [(V6, PORT), (V4, PORT), (SECOND_V4, PORT)]
    assert lookups.count(NAME) == 1


def test_a_request_that_reached_a_server_is_not_sent_again(dropper):
    port, got = dropper
    getaddrinfo, _ = _dns({NAME: [[V4, SECOND_V4]]})
    connect, dials = _route({V4: port})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo), connect:
        with pytest.raises(requests.exceptions.ConnectionError):
            session.post(f"http://{NAME}:{PORT}/hook", data=b"{}", timeout=5)

    assert dials == [(V4, PORT)]
    assert len(got) == 1


def test_an_internal_address_among_the_answers_refuses_them_all():
    # The fallback never reaches past the check: one internal answer refuses
    # the host before any connect.
    getaddrinfo, _ = _dns({NAME: [[V4, "10.0.0.5"]]})
    connect, dials = _route({})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo), connect:
        with pytest.raises(ConnectionError, match=BLOCKED):
            session.get(f"http://{NAME}:{PORT}/models", timeout=5)

    assert dials == []


def test_a_redirect_hop_is_checked_and_falls_back_on_its_own_addresses(receiver):
    names = {"first.dual.test": [[V4]], "second.dual.test": [[V6, SECOND_V4]]}
    getaddrinfo, lookups = _dns(names)
    port = receiver.server_address[1]
    redirector = ThreadingHTTPServer(("127.0.0.1", 0), _redirect_to("second"))
    thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread.start()
    connect, dials = _route(
        {V4: redirector.server_address[1], V6: None, SECOND_V4: port}
    )
    session = build_ssrf_safe_session(BLOCKED)
    try:
        with patch("socket.getaddrinfo", getaddrinfo), connect:
            response = session.get(f"http://first.dual.test:{PORT}/start", timeout=5)
    finally:
        redirector.shutdown()
        redirector.server_close()

    assert response.status_code == 200
    assert dials == [(V4, PORT), (V6, PORT), (SECOND_V4, PORT)]
    assert lookups.count("first.dual.test") == 1
    assert lookups.count("second.dual.test") == 1
    (request,) = receiver.seen
    assert (request["host"], request["path"]) == (f"second.dual.test:{PORT}", "/next")


def _redirect_to(label):
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's name)
            self.send_response(302)
            self.send_header("Location", f"http://{label}.dual.test:{PORT}/next")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Redirect


# --- SMTP --------------------------------------------------------------------------------


def _smtp_peer(ours_and_theirs):
    """Answer one SMTP session on the far end of a socket pair, recording it."""
    ours, theirs = ours_and_theirs
    lines = []

    def serve():
        stream = theirs.makefile("rb")

        def reply(text):
            theirs.sendall(text.encode() + b"\r\n")

        reply("220 synthetic ESMTP")
        while line := stream.readline():
            lines.append(line)
            verb = line[:4].upper()
            if verb == b"DATA":
                reply("354 go on")
                while stream.readline() not in (b".\r\n", b""):
                    pass
                reply("250 queued")
            elif verb == b"QUIT":
                reply("221 bye")
                break
            else:
                reply("250 OK")
        theirs.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return ours, thread, lines


def test_the_pinned_smtp_connection_falls_back_in_order_and_keeps_the_name():
    ours, thread, _ = _smtp_peer(socket.socketpair())
    dialed = []

    def create_connection(address, timeout=None, source_address=None):
        dialed.append(address)
        if address[0] == V6:
            raise OSError(errno.ENETUNREACH, "synthetic: no IPv6 route")
        return ours

    with patch("socket.create_connection", create_connection):
        server = _PinnedSMTP([V6, V4], host="mail.dual.test", port=587, timeout=5)
    try:
        assert dialed == [(V6, 587), (V4, 587)]
        assert server._host == "mail.dual.test"
    finally:
        server.quit()
        thread.join(timeout=5)


@pytest.mark.django_db
def test_an_smtp_alert_reaches_the_second_checked_address(
    organization, provider_dns, settings, monkeypatch
):
    from cryptography.fernet import Fernet

    from agentcc.models.email_alert import AgentccEmailAlert
    from agentcc.services.email_sender import deliver_email
    from integrations.services.credentials import CredentialManager

    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    monkeypatch.delenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", raising=False)
    lookups = provider_dns({"mail.dual.test": [V6, V4]}, passthrough=True)
    alert = AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name="dual-stack-alert",
        recipients=["ops@example.com"],
        events=["error.occurred"],
        provider="smtp",
        encrypted_config=CredentialManager.encrypt(
            {
                "host": "mail.dual.test",
                "port": 587,
                "from_email": "alerts@example.com",
                "use_tls": False,
            }
        ),
    )
    ours, thread, lines = _smtp_peer(socket.socketpair())
    dialed = []

    def create_connection(address, timeout=None, source_address=None):
        dialed.append(address)
        if address[0] == V6:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "synthetic: refused")
        if address[0] == V4:
            return ours
        raise AssertionError(f"dialed an address nobody checked: {address}")

    with patch("socket.create_connection", create_connection):
        result = deliver_email(alert, ["ops@example.com"], "subject", "<p>body</p>")
    thread.join(timeout=5)

    assert result == {"success": True}
    assert dialed == [(V6, 587), (V4, 587)]
    assert [c.args[0] for c in lookups.call_args_list].count("mail.dual.test") == 1
    assert any(line.upper().startswith(b"RCPT TO:<OPS@EXAMPLE.COM>") for line in lines)


@pytest.mark.django_db
def test_an_smtp_alert_whose_every_address_refuses_reports_a_fixed_reason(
    organization, provider_dns, settings, monkeypatch
):
    from cryptography.fernet import Fernet

    from agentcc.models.email_alert import AgentccEmailAlert
    from agentcc.services.email_sender import deliver_email
    from integrations.services.credentials import CredentialManager

    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    monkeypatch.delenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", raising=False)
    provider_dns({"mail.dual.test": [V6, V4]}, passthrough=True)
    alert = AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name="dual-stack-refused",
        recipients=["ops@example.com"],
        events=["error.occurred"],
        provider="smtp",
        encrypted_config=CredentialManager.encrypt(
            {"host": "mail.dual.test", "port": 587, "from_email": "a@example.com"}
        ),
    )
    dialed = []

    def create_connection(address, timeout=None, source_address=None):
        dialed.append(address)
        raise ConnectionRefusedError(errno.ECONNREFUSED, f"refused by {address[0]}")

    with patch("socket.create_connection", create_connection):
        result = deliver_email(alert, ["ops@example.com"], "subject", "<p>body</p>")

    assert dialed == [(V6, 587), (V4, 587)]
    assert result["success"] is False
    assert V4 not in result["error"]
    from agentcc.services.email_sender import SMTP_CONNECT_FAILED

    assert result["error"] == SMTP_CONNECT_FAILED
