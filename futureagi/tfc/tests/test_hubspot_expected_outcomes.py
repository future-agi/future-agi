"""CRM upsert outcomes at the real HTTP and Sentry transport boundaries."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest
from sentry_sdk.integrations.logging import LoggingIntegration

from accounts import utils
from tfc.tests.sentry_capture import sentry_capture

pytestmark = pytest.mark.integration


@pytest.fixture
def crm_receiver(settings, monkeypatch):
    receipts = []
    statuses = {"POST": 409, "PATCH": 200}

    class Handler(BaseHTTPRequestHandler):
        def handle_contact(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            receipts.append((self.command, self.path, payload))
            body = b'{"message":"synthetic response"}'
            self.send_response(statuses[self.command])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = handle_contact
        do_PATCH = handle_contact

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/contacts"
    settings.HUBSPOT_API_TOKEN = "synthetic-token"
    settings.HUBSPOT_URL = base
    settings.HUBSPOT_UPDATE_URL = base + "/{}?idProperty=email"
    # Only the unrelated alert delivery is stubbed; CRM uses real requests HTTP.
    monkeypatch.setattr(utils, "mixpanel_slack_notfy", lambda *a, **kw: None)
    try:
        yield statuses, receipts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def sentry_transport():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    ) as transport:
        yield transport


def test_conflict_then_update_is_not_an_error(
    user, crm_receiver, sentry_transport, caplog
):
    _, receipts = crm_receiver
    caplog.set_level(logging.INFO)
    caplog.clear()
    assert utils.send_hubspot_notification(user) == (True, None)
    assert [method for method, _, _ in receipts] == ["POST", "PATCH"]
    assert receipts[1][1] == f"/contacts/{quote(user.email, safe='@')}?idProperty=email"
    assert receipts[1][2]["properties"]["email"] == user.email
    records = [r for r in caplog.records if r.name == utils.__name__]
    assert any(r.levelno == logging.INFO for r in records)
    assert not [r for r in records if r.levelno >= logging.ERROR]
    assert sentry_transport.events == []


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
def test_create_failure_remains_actionable(
    status, user, crm_receiver, sentry_transport, caplog
):
    statuses, receipts = crm_receiver
    statuses["POST"] = status
    updated, error = utils.send_hubspot_notification(user)
    assert updated is False
    assert error
    assert [method for method, _, _ in receipts] == ["POST"]
    assert any(
        r.levelno >= logging.ERROR for r in caplog.records if r.name == utils.__name__
    )
    assert any(e.get("level") == "error" for e in sentry_transport.events)


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_conflict_then_update_failure_remains_actionable(
    status, user, crm_receiver, sentry_transport, caplog
):
    statuses, receipts = crm_receiver
    statuses["PATCH"] = status
    updated, error = utils.send_hubspot_notification(user)
    assert updated is False
    assert error
    assert [method for method, _, _ in receipts] == ["POST", "PATCH"]
    assert any(
        r.levelno >= logging.ERROR for r in caplog.records if r.name == utils.__name__
    )
    assert any(e.get("level") == "error" for e in sentry_transport.events)


def test_conflict_without_update_configuration_remains_actionable(
    user, crm_receiver, sentry_transport, settings, caplog
):
    _, receipts = crm_receiver
    settings.HUBSPOT_UPDATE_URL = ""
    updated, error = utils.send_hubspot_notification(user)
    assert updated is False
    assert "HUBSPOT_UPDATE_URL is not set" in error
    assert [method for method, _, _ in receipts] == ["POST"]
    assert any(
        r.levelno >= logging.ERROR for r in caplog.records if r.name == utils.__name__
    )
    assert any(e.get("level") == "error" for e in sentry_transport.events)
