"""The deployed Granian access log must be safe after Django unwinds.

Ruling 681 addendum 5: the sink is what Granian's own access handler writes
(its stdout), not the in-memory record.
"""

import http.client
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Real ROOT_URLCONF and deployed middleware, with loopback-only configuration.
APP = """import logging
from django.conf import settings
from django.core.asgi import get_asgi_application
from tfc.settings.settings import MIDDLEWARE
settings.MIDDLEWARE = MIDDLEWARE
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
settings.APPEND_SLASH = True
settings.DEBUG = False
settings.SECURE_SSL_REDIRECT = False
application = get_asgi_application()
from tfc.logging.config import configure_structlog
configure_structlog(cache_logger_on_first_use=False)
logging.getLogger("granian.access").setLevel(logging.INFO)
"""
ACCESS = ' HTTP/1.1"'


def access_lines(output):
    if not output.exists():
        return []
    return [line for line in output.read_text().splitlines() if ACCESS in line]


def test_real_granian_access_logger_cannot_disclose_checkout_session(tmp_path):
    supplied = "synthetic-granian-session-681"
    (tmp_path / "access_fixture.py").write_text(APP)
    output = tmp_path / "server.log"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        (str(tmp_path), os.getcwd(), env.get("PYTHONPATH", ""))
    )
    command = [
        str(Path(sys.executable).parent / "granian"),
        "--interface",
        "asgi",
        "access_fixture:application",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
        "--log-level",
        "warning",
        "--access-log",
    ]
    statuses = []
    with output.open("w") as log:
        child = subprocess.Popen(
            command,
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                if child.poll() is not None:
                    pytest.fail("Granian stopped before serving")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        pytest.fail("Granian did not become ready")
                    time.sleep(0.1)
            for route in [
                f"/agentcc/managed-credits/checkout/{supplied}",
                f"/ordinary/{supplied}/",
            ]:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
                conn.request("GET", route)
                response = conn.getresponse()
                response.read()
                statuses.append(response.status)
                conn.close()
            deadline = time.monotonic() + 5
            while len(access_lines(output)) < 2 and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
    assert statuses == [301, 404]
    rows = access_lines(output)
    assert len(rows) == 2, "real Granian access logging must be enabled"
    checkout = [row for row in rows if "managed-credits/checkout" in row]
    ordinary = [row for row in rows if "/ordinary/" in row]
    assert checkout and ordinary
    assert all("/checkout/[redacted]" in row for row in checkout)
    assert supplied in str(ordinary), "unrelated access logs must remain unchanged"
    # Nothing the server wrote (access log or application log) holds the value
    # outside the unrelated route's own lines.
    written = output.read_text().splitlines()
    assert all("/ordinary/" in line for line in written if supplied in line), (
        "supplied value reached Granian's output"
    )
