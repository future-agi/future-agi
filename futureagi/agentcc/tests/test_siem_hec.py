"""W14 HEC journey against a synthetic loopback collector, never a vendor.

The receiver authenticates independently, decodes the actual wire bytes, and
returns HEC acknowledgements. Connection tests must leave no database rows.
"""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.db import connection
from structlog.testing import capture_logs

from accounts.models.audit_log import AuditLog
from agentcc.models import AgentccToolCall
from agentcc.models.siem import AgentccSIEMDelivery, AgentccSIEMDestination
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.siem_receiver import admit_loopback
from agentcc.tests.test_mcp_credentials import _member_client, _org_with_owner
from agentcc.tests.test_siem_delivery import drain, ingest_call, make_due

pytestmark = pytest.mark.integration
BASE = "/agentcc/siem/destinations/"
TEST = BASE + "test-connection/"


class HECSink:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.events = []
        self.status = 200
        self.code = 0
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                valid = self.headers.get("Authorization") == "Splunk " + sink.token
                code = sink.code if valid else 4
                if valid and sink.status == 200 and code == 0:
                    sink.events.extend(json.loads(line) for line in raw.splitlines())
                body = json.dumps({"text": "synthetic response", "code": code}).encode()
                self.send_response(sink.status if valid else 403)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)


@pytest.fixture
def sink(monkeypatch, settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    sink = HECSink()
    admit_loopback(monkeypatch, sink)
    sink.thread.start()
    yield sink
    sink.server.shutdown()
    sink.server.server_close()
    sink.thread.join()


def body(sink, **changes):
    return dict(
        name="Synthetic HEC",
        kind="hec",
        url=sink.url + "/services/collector/event",
        streams=["audit_events"],
        token=sink.token,
        **changes,
    )


def create(client, sink):
    response = client.post(BASE, body(sink), format="json")
    assert response.status_code == 201, response.status_code
    return response.json()["result"]


def counts():
    return tuple(
        model.objects.count()
        for model in (
            AgentccSIEMDestination,
            AgentccSIEMDelivery,
            AuditLog,
            AgentccToolCall,
        )
    )


def test_connection_is_synthetic_and_never_persists(auth_client, sink):
    before = counts()
    with capture_logs() as logs:
        for credential, expected in [("bad-credential", 400), (sink.token, 200)]:
            response = auth_client.post(
                TEST, {"url": body(sink)["url"], "token": credential}, format="json"
            )
            assert response.status_code == expected, response.status_code
            assert sink.token not in response.content.decode()
            if expected == 400:
                assert "token" in response.content.decode().lower()
            assert counts() == before
    assert len(sink.events) == 1
    assert sink.events[0]["event"]["type"] == "siem.connection_test"
    assert sink.token not in json.dumps(logs, default=str)


def test_hec_200_auth_error_is_not_success(auth_client, sink):
    sink.code = 4
    before = counts()
    response = auth_client.post(
        TEST, {"url": body(sink)["url"], "token": sink.token}, format="json"
    )
    assert response.status_code == 400
    assert "token" in response.content.decode().lower()
    assert counts() == before


def test_hec_token_write_only_encrypted_and_rotation(auth_client, sink, organization):
    result = create(auth_client, sink)
    destination = AgentccSIEMDestination.objects.get(pk=result["id"])
    assert destination.enabled is False
    assert decrypt_token(destination.encrypted_secret) == sink.token
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT encrypted_secret FROM agentcc_siem_destination WHERE id = %s",
            [destination.pk],
        )
        assert sink.token not in cursor.fetchone()[0]
    for response in [
        result,
        auth_client.get(BASE).json(),
        auth_client.get(BASE + result["id"] + "/").json(),
    ]:
        assert sink.token not in json.dumps(response)
        assert sink.token[-4:] not in json.dumps(response)
    changed = secrets.token_urlsafe(32)
    updated = auth_client.patch(
        BASE + result["id"] + "/",
        {"token": changed, "name": "Updated HEC"},
        format="json",
    )
    assert updated.status_code == 200
    destination.refresh_from_db()
    assert decrypt_token(destination.encrypted_secret) == changed
    assert changed not in updated.content.decode()
    assert changed not in json.dumps(
        list(AuditLog.objects.filter(organization=organization).values()), default=str
    )


def test_hec_org_exact_events_and_bounded_failure(auth_client, sink, organization):
    result = create(auth_client, sink)
    endpoint = BASE + result["id"] + "/"
    assert (
        auth_client.patch(endpoint, {"enabled": True}, format="json").status_code == 200
    )
    other, _ = _org_with_owner("hec-other")
    a, _ = ingest_call(organization, server_id="server-a")
    b, _ = ingest_call(organization, server_id="server-b")
    foreign, _ = ingest_call(other)
    with capture_logs() as logs:
        drain()
    expected = set(
        AgentccToolCall.objects.filter(organization=organization).values_list(
            "event_id", flat=True
        )
    )
    actual = [entry["event"]["event_id"] for entry in sink.events]
    assert set(actual) == expected == {a["event_id"], b["event_id"]}
    assert len(actual) == len(expected)
    assert foreign["event_id"] not in actual
    assert set(AgentccSIEMDelivery.objects.values_list("status", flat=True)) == {
        "delivered"
    }
    assert sink.token not in json.dumps(logs, default=str)
    sink.status = 503
    ingest_call(organization)
    for _ in range(6):
        make_due()
        drain()
    failed = AgentccSIEMDelivery.objects.get(status="dead_letter")
    assert failed.attempts == failed.max_attempts == 6
    assert failed.next_retry_at is None
    drain()
    failed.refresh_from_db()
    assert failed.attempts == 6
    assert auth_client.delete(endpoint).status_code == 200
    assert not AgentccSIEMDelivery.objects.exists()


@pytest.mark.parametrize("role", ["Member", "Viewer"])
def test_readers_cannot_mutate_or_test(auth_client, workspace, sink, role):
    result = create(auth_client, sink)
    reader = _member_client(workspace.organization, workspace)
    if role == "Viewer":
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import WorkspaceMembership
        from tfc.constants.levels import Level

        person = reader.handler._force_user
        person.organization_role = role
        person.save(update_fields=["organization_role"])
        OrganizationMembership.no_workspace_objects.filter(user=person).update(
            role=role, level=Level.VIEWER
        )
        WorkspaceMembership.no_workspace_objects.filter(user=person).update(
            role="workspace_viewer", level=Level.VIEWER
        )
    listed = reader.get(BASE)
    assert listed.status_code == 200
    assert listed.json()["result"]["can_manage"] is False
    endpoint = BASE + result["id"] + "/"
    assert reader.get(endpoint).status_code == 200
    assert reader.get("/agentcc/siem/deliveries/").status_code == 200
    assert reader.post(BASE, body(sink), format="json").status_code == 403
    assert reader.patch(endpoint, {"name": "Denied"}, format="json").status_code == 403
    assert reader.delete(endpoint).status_code == 403
    assert (
        reader.post(
            TEST, {"url": body(sink)["url"], "token": sink.token}, format="json"
        ).status_code
        == 403
    )
    assert not sink.events


def test_connection_and_delivery_default_off(auth_client, sink, settings):
    result = create(auth_client, sink)
    settings.AGENTCC_SIEM_EXPORT_ENABLED = False
    unknown = auth_client.post("/agentcc/route-that-does-not-exist/", {}, format="json")
    for response in [
        auth_client.post(
            TEST, {"url": body(sink)["url"], "token": sink.token}, format="json"
        ),
        auth_client.post(BASE, body(sink), format="json"),
        auth_client.patch(BASE + result["id"] + "/", {"enabled": True}, format="json"),
    ]:
        assert response.status_code == unknown.status_code == 404
        assert response.content == unknown.content
        assert response["Cache-Control"] == "no-store"
    assert not sink.events


def test_hec_rejects_spans_missing_token_and_url_credentials(auth_client, sink):
    for change in [
        {"streams": ["spans"]},
        {"token": ""},
        {"token": "enc::not-plaintext"},
        {"url": "https://user:secret@example.invalid/"},
    ]:
        payload = {**body(sink), **change}
        assert auth_client.post(BASE, payload, format="json").status_code == 400
    assert not AgentccSIEMDestination.objects.exists()
