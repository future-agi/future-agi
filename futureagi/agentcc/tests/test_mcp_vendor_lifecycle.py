"""Synthetic partner lifecycle checks; never call a vendor or a production host."""

import json
from unittest.mock import patch

import pytest
from django.test import Client

from agentcc.services.credential_manager import encrypt_token
from agentcc.services.mcp_safe_fetch import FetchResponse
from agentcc.tests.mcp_oauth_helpers import member, new_org

pytestmark = pytest.mark.django_db
SECRET = "fixture-internal-secret-long-enough-not-real"
PATH = "/agentcc/mcp/vendors/internal/credential/"


@pytest.fixture(autouse=True)
def vendor_settings(settings, monkeypatch):
    settings.AGENTCC_MCP_VENDORS_ENABLED = True
    settings.AGENTCC_MCP_VENDOR_SUBJECT_KEY = "fixture-subject-key-long-enough-not-real"
    monkeypatch.setenv("AGENTCC_MCP_DJANGO_SECRET", SECRET)


def binding(org, vendor="pipedream"):
    from agentcc.models.mcp_credential import AgentccMCPCredential
    from agentcc.models.mcp_vendor import AgentccMCPVendorBinding

    vault = AgentccMCPCredential.objects.create(
        organization=org,
        server_id="partner",
        scope="shared",
        kind="oauth_client",
        origin="https://api.pipedream.com",
        encrypted_secret=encrypt_token(
            json.dumps(
                {
                    "client_id": "fixture-client",
                    "client_secret": "fixture-secret-not-real",
                }
            )
        ),
    )
    return AgentccMCPVendorBinding.objects.create(
        organization=org,
        server_id="partner",
        vendor=vendor,
        credential=vault,
        project_id="proj_fixture",
        environment="development",
        app_slug="gmail",
        enabled=True,
    )


def request_body(org, user):
    return {
        "org_id": str(org.pk),
        "server_id": "partner",
        "vendor": "pipedream",
        "end_user": {"source": "member", "id": str(user.pk)},
    }


def test_routes_404_when_disabled(settings):
    settings.AGENTCC_MCP_VENDORS_ENABLED = False
    client = Client()
    assert (
        client.post(PATH, data="{}", content_type="application/json").status_code == 404
    )
    assert client.get("/agentcc/mcp/vendor-connections/").status_code == 404


def test_wrong_secret_401():
    response = Client().post(
        PATH,
        data="{}",
        content_type="application/json",
        HTTP_X_AGENTCC_MCP_INTERNAL="wrong",
    )
    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"


def test_admin_token_refused(monkeypatch):
    from agentcc.services import gateway_client

    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", SECRET)
    assert (
        Client()
        .post(
            PATH,
            data="{}",
            content_type="application/json",
            HTTP_X_AGENTCC_MCP_INTERNAL=SECRET,
        )
        .status_code
        == 401
    )


def test_member_pseudonym_org_scoped_and_never_email():
    from agentcc.services.mcp_vendor_credentials import subject_key

    a = subject_key("org-a", "member", "person@example.test")
    assert a.startswith("fau_") and "person" not in a
    assert a != subject_key("org-b", "member", "person@example.test")
    assert a != subject_key("org-a", "asserted", "person@example.test")


def test_former_member_and_reserved_assertion_refused():
    from accounts.models.organization_membership import OrganizationMembership
    from agentcc.services.mcp_vendor_credentials import answer_request

    org = new_org()
    user, _ = member(org)
    binding(org)
    OrganizationMembership.all_objects.filter(user=user).update(is_active=False)
    assert answer_request(request_body(org, user)) == {"error": "identity_refused"}
    body = request_body(org, user)
    body["end_user"] = {"source": "asserted", "id": "fau_reserved"}
    assert answer_request(body) == {"error": "identity_refused"}


def test_asserted_refused_when_policy_member_only():
    from agentcc.services.mcp_vendor_credentials import answer_request

    org = new_org()
    user, _ = member(org)
    binding(org)
    body = request_body(org, user)
    body["end_user"] = {"source": "asserted", "id": "external-a"}
    assert answer_request(body) == {"error": "identity_refused"}


def test_revoked_binding_refuses():
    from agentcc.services.mcp_vendor_credentials import answer_request

    org = new_org()
    user, _ = member(org)
    row = binding(org)
    row.credential.status = "revoked"
    row.credential.save(update_fields=["status"])
    assert answer_request(request_body(org, user)) == {"error": "revoked"}


def test_cross_org_vault_binding_refused():
    from django.core.exceptions import ValidationError

    org = new_org()
    row = binding(org)
    row.organization = new_org()
    with pytest.raises(ValidationError):
        row.save()


def test_no_implicit_connection_created_for_execution():
    from agentcc.models.mcp_vendor import AgentccMCPVendorConnection
    from agentcc.services.mcp_vendor_credentials import answer_request

    org = new_org()
    user, _ = member(org)
    binding(org)
    assert answer_request(request_body(org, user))["error"] == "not_connected"
    assert AgentccMCPVendorConnection.objects.count() == 0


def test_disconnect_local_first_purge_barrier_and_receipts():
    from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
    from agentcc.services import mcp_vendor_connections as lifecycle
    from agentcc.services.mcp_revocation import Revocation

    org = new_org()
    user, _ = member(org)
    row = binding(org)
    connection = Connection.objects.create(
        binding=row, end_user_key="fau_fixture", source="member", user=user
    )
    events = []

    def purge(*args):
        connection.refresh_from_db()
        assert connection.status == "revoked"
        events.append("purge")
        return False

    def revoke(*args):
        connection.refresh_from_db()
        assert connection.status == "revoked"
        events.append("vendor")
        return Revocation("unconfirmed", "not_documented")

    with (
        patch.object(lifecycle, "purge_gateway", purge),
        patch.object(lifecycle, "revoke_vendor", revoke),
    ):
        lifecycle.disconnect(connection.pk)
    connection.refresh_from_db()
    assert connection.gateway_purge_pending and not connection.vendor_revoke_pending
    assert connection.vendor_revoke_outcome == "unconfirmed"
    assert events == ["purge", "vendor"]
    assert connection.receipts.count() >= 2


def test_connection_list_is_scoped_to_member():
    from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection

    org = new_org()
    user, client = member(org)
    other, _ = member(org)
    row = binding(org)
    own = Connection.objects.create(
        binding=row, end_user_key="fau_own", source="member", user=user
    )
    Connection.objects.create(
        binding=row, end_user_key="fau_other", source="member", user=other
    )
    response = client.get("/agentcc/mcp/vendor-connections/")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()["connections"]] == [str(own.pk)]
    assert "encrypted" not in response.content.decode()


def test_pipedream_dead_account_is_not_connected():
    from agentcc.services.mcp_vendor_pipedream import Pipedream

    def fetch(url, **kwargs):
        if url.endswith("/oauth/token"):
            return FetchResponse(
                200,
                {},
                json.dumps({"access_token": "fixture-access-not-real"}).encode(),
                url,
            )
        return FetchResponse(
            200,
            {},
            json.dumps(
                {"data": [{"id": "ac_fixture", "healthy": True, "dead": True}]}
            ).encode(),
            url,
        )

    api = Pipedream(
        "fixture-client", "fixture-secret-not-real", "proj_fixture", "development"
    )
    with patch("agentcc.services.mcp_safe_fetch.fetch", fetch):
        assert api.account("fau_fixture", "gmail") is None


@pytest.mark.parametrize(
    "code,outcome",
    [
        (200, "confirmed"),
        (400, "unconfirmed"),
        (404, "unconfirmed"),
        (409, "unconfirmed"),
        (429, "pending"),
        (500, "pending"),
    ],
)
def test_composio_revoke_outcomes_and_order(code, outcome):
    from agentcc.services.mcp_vendor_composio import Composio

    seen = []

    def fetch(url, **kwargs):
        seen.append((kwargs["method"], url))
        return FetchResponse(
            code if url.endswith("/revoke") else 204,
            {},
            b'{"revoked_tokens":[],"connected_account":{"status":"REVOKED"}}',
            url,
        )

    with patch("agentcc.services.mcp_safe_fetch.fetch", fetch):
        result = Composio("fixture-project-not-real").disconnect("ca_fixture")
    assert result.outcome == outcome
    assert seen[0][0] == "POST" and seen[0][1].endswith("/revoke")
    # Never delete a reference still owed a retry.
    assert (len(seen) == 1) == (outcome == "pending")
