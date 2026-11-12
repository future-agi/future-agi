"""SIEM export destinations: an organization admin configures where the org's
MCP tool-call audit events and spans are exported (G16-SIEM).

Only an Owner or Admin can list, add, change, enable, disable, rotate or delete
a destination; every change is audited in the change's own transaction and
refused when the audit row cannot be written. A webhook's signing secret is
shown once; an OTLP header value is write-only. Destinations start disabled,
and enabling one needs the deployment's export switch (off by default).
Production URLs must be https and public; the delivery tests patch that one
seam to reach a loopback receiver.
"""

import json
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from accounts.models.audit_log import AuditLog
from accounts.models.workspace import Workspace
from agentcc.tests.siem_receiver import Receiver, admit_loopback
from agentcc.tests.test_mcp_credentials import _member_client, _org_with_owner
from conftest import WorkspaceAwareAPIClient

pytestmark = pytest.mark.integration

DESTINATIONS = "/agentcc/siem/destinations/"


def detail(destination_id, action=""):
    return f"{DESTINATIONS}{destination_id}/{action + '/' if action else ''}"


@pytest.fixture(autouse=True)
def export_on(settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True


@pytest.fixture
def receiver(monkeypatch):
    with Receiver() as running:
        admit_loopback(monkeypatch, running)
        yield running


def create(client, receiver, **changes):
    body = {
        "name": "SOC webhook",
        "kind": "webhook",
        "url": receiver.url + "/hooks/agentcc",
        "streams": ["audit_events"],
    }
    body.update(changes)
    return client.post(DESTINATIONS, body, format="json")


def audit_rows(organization, action):
    return list(
        AuditLog.objects.filter(organization=organization, action=action).order_by(
            "created_at"
        )
    )


def owner_client(org, owner):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=owner)
    client.set_workspace(Workspace.objects.get(organization=org, is_default=True))
    return client


def test_member_can_read_but_not_change_destinations(auth_client, workspace, receiver):
    created = create(auth_client, receiver)
    assert created.status_code == 201, created.content
    member = _member_client(workspace.organization, workspace)
    destination_id = created.json()["result"]["id"]
    assert member.get(DESTINATIONS).status_code == 200
    assert create(member, receiver, name="member").status_code == 403
    assert (
        member.patch(detail(destination_id), {"enabled": True}, format="json")
    ).status_code == 403
    assert member.delete(detail(destination_id)).status_code == 403
    assert member.post(detail(destination_id, "rotate-secret")).status_code == 403


def test_admin_adds_disabled_webhook_and_sees_its_secret_once(
    auth_client, organization, receiver
):
    response = create(auth_client, receiver)
    assert response.status_code == 201, response.content
    result = response.json()["result"]
    secret = result["signing_secret"]
    assert secret.startswith("whsec_") and len(secret) > 40
    assert result["enabled"] is False
    assert result["kind"] == "webhook"
    assert result["streams"] == ["audit_events"]
    assert result["secret_hint"] == secret[-4:]

    listed = auth_client.get(DESTINATIONS)
    assert listed.status_code == 200
    body = listed.json()["result"]
    assert [d["id"] for d in body["destinations"]] == [result["id"]]
    assert secret not in listed.content.decode()
    assert "signing_secret" not in body["destinations"][0]

    created = audit_rows(organization, "agentcc.siem_destination_created")
    assert len(created) == 1
    record = json.dumps([created[0].changes, created[0].metadata])
    assert secret not in record and "/hooks/agentcc" not in record


def test_otlp_header_value_is_write_only(auth_client, receiver):
    value = "Bearer " + "x" * 12 + "write-only"
    response = create(
        auth_client,
        receiver,
        name="Collector",
        kind="otlp_http",
        url=receiver.url,
        streams=["spans", "audit_events"],
        auth_header="Authorization",
        auth_value=value,
    )
    assert response.status_code == 201, response.content
    result = response.json()["result"]
    assert result["auth_header"] == "Authorization"
    assert result["has_secret"] is True
    assert "signing_secret" not in result
    assert value not in response.content.decode()
    assert value not in auth_client.get(DESTINATIONS).content.decode()


@pytest.mark.parametrize(
    "url",
    [
        "http://198.51.100.7/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.5/hook",
        "https://169.254.169.254/latest",
        "https://user:pw@198.51.100.7/hook",
        "ftp://198.51.100.7/hook",
    ],
)
def test_production_url_guard_refuses_unsafe_destinations(
    auth_client, url, provider_dns
):
    dns = provider_dns({})
    response = auth_client.post(
        DESTINATIONS,
        {"name": "unsafe", "kind": "webhook", "url": url, "streams": ["spans"]},
        format="json",
    )
    assert response.status_code == 400, response.content
    if url.startswith("https://") and "@" not in url:
        dns.assert_called()  # the URL check resolved the IP literal (stubbed)


def test_otlp_url_is_the_collector_base(auth_client, receiver):
    response = create(
        auth_client,
        receiver,
        kind="otlp_http",
        url=receiver.url + "/v1/traces",
        streams=["spans"],
    )
    assert response.status_code == 400
    assert "/v1/traces" in response.json()["message"]


def test_bad_streams_and_kinds_are_refused(auth_client, receiver):
    for changes in (
        {"streams": []},
        {"streams": ["arguments"]},
        {"kind": "splunk"},
        {"kind": "otlp_http", "auth_header": "Host", "auth_value": "x"},
    ):
        assert create(auth_client, receiver, **changes).status_code == 400, changes


def test_enable_and_disable_are_audited_with_actor(
    auth_client, organization, user, receiver, export_on
):
    destination_id = create(auth_client, receiver).json()["result"]["id"]
    for enabled in (True, False):
        response = auth_client.patch(
            detail(destination_id), {"enabled": enabled}, format="json"
        )
        assert response.status_code == 200, response.content
        assert response.json()["result"]["enabled"] is enabled
    enabled_rows = audit_rows(organization, "agentcc.siem_destination_enabled")
    disabled_rows = audit_rows(organization, "agentcc.siem_destination_disabled")
    assert len(enabled_rows) == len(disabled_rows) == 1
    for row, new in ((enabled_rows[0], True), (disabled_rows[0], False)):
        assert row.actor_id == user.id
        assert str(row.target_id) == destination_id
        assert row.changes == {"enabled": {"old": not new, "new": new}}


def test_enable_refused_while_export_is_off(
    auth_client, organization, receiver, settings
):
    destination_id = create(auth_client, receiver).json()["result"]["id"]
    settings.AGENTCC_SIEM_EXPORT_ENABLED = False
    response = auth_client.patch(
        detail(destination_id), {"enabled": True}, format="json"
    )
    unknown = auth_client.patch(
        "/agentcc/route-that-does-not-exist/", {}, format="json"
    )
    assert response.status_code == unknown.status_code == 404
    assert response.content == unknown.content
    assert auth_client.get(DESTINATIONS).status_code == 404
    assert not audit_rows(organization, "agentcc.siem_destination_enabled")
    model = apps.get_model("prism", "AgentccSIEMDestination")
    assert model.objects.get(id=destination_id).enabled is False


def test_unwritable_audit_leaves_destination_unchanged(
    auth_client, receiver, export_on
):
    destination_id = create(auth_client, receiver).json()["result"]["id"]
    with patch("tfc.utils.audit.log_audit", return_value=None):
        response = auth_client.patch(
            detail(destination_id), {"enabled": True}, format="json"
        )
        assert response.status_code == 503
        refused = create(auth_client, receiver, name="second")
        assert refused.status_code == 503
    listed = auth_client.get(DESTINATIONS).json()["result"]["destinations"]
    assert [(d["id"], d["enabled"]) for d in listed] == [(destination_id, False)]


def test_rotate_secret_shows_the_new_secret_once(auth_client, organization, receiver):
    created = create(auth_client, receiver).json()["result"]
    rotated = auth_client.post(detail(created["id"], "rotate-secret"))
    assert rotated.status_code == 200, rotated.content
    secret = rotated.json()["result"]["signing_secret"]
    assert secret.startswith("whsec_") and secret != created["signing_secret"]
    assert secret not in auth_client.get(DESTINATIONS).content.decode()
    rows = audit_rows(organization, "agentcc.siem_destination_secret_rotated")
    assert len(rows) == 1 and secret not in json.dumps(rows[0].changes)


def test_url_and_streams_changes_are_audited_without_path(
    auth_client, organization, receiver
):
    destination_id = create(auth_client, receiver).json()["result"]["id"]
    response = auth_client.patch(
        detail(destination_id),
        {"url": receiver.url + "/private/path", "streams": ["audit_events", "spans"]},
        format="json",
    )
    assert response.status_code == 200, response.content
    rows = audit_rows(organization, "agentcc.siem_destination_updated")
    assert len(rows) == 1
    assert rows[0].changes["streams"] == {
        "old": ["audit_events"],
        "new": ["audit_events", "spans"],
    }
    assert "/private/path" not in json.dumps(rows[0].changes)


def test_delete_is_audited(auth_client, organization, receiver):
    destination_id = create(auth_client, receiver).json()["result"]["id"]
    assert auth_client.delete(detail(destination_id)).status_code == 200
    assert auth_client.get(DESTINATIONS).json()["result"]["destinations"] == []
    assert len(audit_rows(organization, "agentcc.siem_destination_deleted")) == 1


def test_other_org_destinations_are_invisible(auth_client, receiver):
    other_org, other_owner = _org_with_owner("SIEM other org")
    other = owner_client(other_org, other_owner)
    foreign = create(other, receiver, name="foreign").json()["result"]["id"]
    create(auth_client, receiver, name="mine")
    names = [
        d["name"]
        for d in auth_client.get(DESTINATIONS).json()["result"]["destinations"]
    ]
    assert names == ["mine"]
    assert (
        auth_client.patch(detail(foreign), {"name": "x"}, format="json").status_code
        == 404
    )
    assert auth_client.delete(detail(foreign)).status_code == 404
    assert auth_client.post(detail(foreign, "rotate-secret")).status_code == 404
    assert [
        d["name"] for d in other.get(DESTINATIONS).json()["result"]["destinations"]
    ] == ["foreign"]


def test_an_org_holds_at_most_five_destinations(auth_client, receiver):
    for n in range(5):
        assert create(auth_client, receiver, name=f"dest-{n}").status_code == 201
    assert create(auth_client, receiver, name="dest-6").status_code == 400


def test_webhook_auth_header_patch_is_refused_and_keeps_its_secret(
    auth_client, organization, receiver
):
    created = create(auth_client, receiver)
    assert created.status_code == 201, created.content
    destination_id = created.json()["result"]["id"]
    model = apps.get_model("prism", "AgentccSIEMDestination")
    before = model.objects.get(id=destination_id).encrypted_secret
    assert before
    audits = AuditLog.objects.filter(organization=organization).count()
    for header in ("", "Authorization"):
        response = auth_client.patch(
            detail(destination_id), {"auth_header": header}, format="json"
        )
        assert response.status_code == 400, response.content
    assert model.objects.get(id=destination_id).encrypted_secret == before
    assert AuditLog.objects.filter(organization=organization).count() == audits
    shown = auth_client.get(detail(destination_id)).json()["result"]
    assert shown["has_secret"] is True


def test_clearing_an_otlp_auth_header_is_audited(auth_client, organization, receiver):
    created = create(
        auth_client,
        receiver,
        name="Collector",
        kind="otlp_http",
        url=receiver.url,
        streams=["spans"],
        auth_header="Authorization",
        auth_value="Bearer " + "x" * 16,
    )
    assert created.status_code == 201, created.content
    destination_id = created.json()["result"]["id"]
    cleared = auth_client.patch(
        detail(destination_id), {"auth_header": ""}, format="json"
    )
    assert cleared.status_code == 200, cleared.content
    assert cleared.json()["result"]["has_secret"] is False
    [row] = audit_rows(organization, "agentcc.siem_destination_updated")
    assert row.changes["auth_header"] == {"old": "Authorization", "new": ""}
    assert row.changes["auth_value"] == "cleared"


def test_creates_hold_the_organization_lock_while_counting(
    auth_client, organization, receiver
):
    table = Organization._meta.db_table
    with CaptureQueriesContext(connection) as queries:
        assert create(auth_client, receiver).status_code == 201
    locks = [
        q["sql"]
        for q in queries
        if "FOR NO KEY UPDATE" in q["sql"].upper() and f'"{table}"' in q["sql"]
    ]
    assert locks, "create did not lock the organization row"
    lock_at = next(i for i, q in enumerate(queries) if q["sql"] in locks)
    counted_at = next(
        i
        for i, q in enumerate(queries)
        if 'FROM "agentcc_siem_destination"' in q["sql"] and i > lock_at
    )
    assert counted_at > lock_at
