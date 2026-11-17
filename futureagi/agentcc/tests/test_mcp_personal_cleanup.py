"""Stored cleanup and real-session edge cases; all upstream calls are mocked."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests import test_mcp_connections as own
from agentcc.tests.mcp_oauth_helpers import member as org_member
from agentcc.tests.mcp_oauth_helpers import new_org
from agentcc.tests.mcp_upstream_oauth_helpers import SERVER_ID
from agentcc.tests.test_mcp_personal_boundary import bearer, counts
from agentcc.views.mcp_credentials import MCPCredentialListView

pytestmark = pytest.mark.django_db
_settings = own._settings
config = own.config
purges = own.purges
vendor = own.vendor


def test_migration_state_matches_personal_models():
    # Read-only state comparison, not a command that enables startup mutations.
    loader = MigrationLoader(connection)
    changes = MigrationAutodetector(
        loader.project_state(), ProjectState.from_apps(apps)
    ).changes(graph=loader.graph)
    assert changes.get("prism", []) == []


def test_existing_revoked_account_gains_a_retry_receipt(
    auth_client,
    config,
    vendor,
):
    own._connect(auth_client, vendor)
    row = Credential.objects.get(scope="user", kind="oauth")
    Credential.objects.filter(pk=row.pk).update(status="revoked")
    migration = import_module("agentcc.migrations.0061_mcp_personal_principal_cleanup")
    migration.preserve_existing_cleanup(apps, SimpleNamespace(connection=connection))
    current = own._row(auth_client)
    assert current["cleanup_pending"] is True
    assert current["disconnect_url"]
    response = auth_client.delete(current["disconnect_url"])
    assert response.status_code == 200
    assert response.json()["result"]["cleanup_pending"] is False
    assert vendor.revoked
    row.refresh_from_db()
    assert row.encrypted_refresh is None


@pytest.mark.parametrize("filtered", [False, True])
def test_real_bearer_cannot_select_a_foreign_organization(
    organization,
    user,
    config,
    filtered,
):
    stranger, _ = org_member(new_org("personal-foreign-org"), name="Stranger")
    client = bearer(stranger)
    before = counts()
    response = client.get(
        own.LIST,
        {"server_id": SERVER_ID} if filtered else {},
        HTTP_X_ORGANIZATION_ID=str(organization.pk),
    )
    assert response.status_code == 403
    assert counts() == before
    assert b"connections" not in response.content


@pytest.mark.parametrize("authorization", ["", "Bearer ", "   "])
def test_system_key_never_enters_shared_key_authentication(
    organization,
    user,
    config,
    authorization,
):
    key = OrgApiKey.objects.filter(organization=organization, type="system").first()
    if key is None:
        key = OrgApiKey.objects.create(organization=organization, type="system")
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_AUTHORIZATION=authorization,
    )
    with patch("accounts.authentication.APIKeyAuthentication.authenticate") as shared:
        response = client.get(own.LIST)
    assert response.status_code in (401, 403)
    shared.assert_not_called()


def test_generic_system_key_has_no_personal_metadata(
    auth_client,
    organization,
    user,
    config,
    vendor,
):
    own._connect(auth_client, vendor)
    row = Credential.objects.get(scope="user", kind="oauth")
    key = OrgApiKey.objects.filter(organization=organization, type="system").first()
    if key is None:
        key = OrgApiKey.objects.create(organization=organization, type="system")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.get("/agentcc/mcp/credentials/")
    # Organization keys are retired, so the key itself is refused.
    assert response.status_code == 401
    assert str(row.pk) not in response.content.decode()
    # A service-shaped key has no person behind it, so the listing it would
    # get holds no person's credential.
    request = SimpleNamespace(user=user, org_api_key=key, organization=organization)
    assert MCPCredentialListView()._person(request) is None
    listed = vault.credentials_for_org(organization, None, user=None)
    assert row.pk not in {credential.pk for credential in listed}


def test_upstream_refusal_retains_only_retryable_encrypted_material(
    auth_client,
    config,
    vendor,
):
    own._connect(auth_client, vendor)
    original = vendor.fetch

    def refused(url, **kwargs):
        if url.endswith("/revoke"):
            from agentcc.tests.mcp_upstream_oauth_helpers import _response

            return _response(503)
        return original(url, **kwargs)

    with patch.object(connect.mcp_safe_fetch, "fetch", refused):
        response = auth_client.delete(own.DISCONNECT.format(SERVER_ID))
    assert response.json()["result"]["cleanup_pending"] is True
    row = Credential.objects.get(scope="user", kind="oauth")
    assert row.status == "revoked"
    assert row.encrypted_refresh is not None
    assert row.oauth_revoke_pending is True
    assert row.gateway_purge_pending is False
    assert own._row(auth_client)["connected"] is False
    assert (
        auth_client.delete(own.DISCONNECT.format(SERVER_ID)).json()["result"][
            "cleanup_pending"
        ]
        is False
    )
    row.refresh_from_db()
    assert row.encrypted_refresh is None


def test_no_revocation_endpoint_is_not_claimed_as_vendor_invalidation(
    auth_client,
    config,
    vendor,
):
    vendor.revocation = False
    own._connect(auth_client, vendor)
    response = auth_client.delete(own.DISCONNECT.format(SERVER_ID))
    assert response.json()["result"]["gateway_synced"] is True
    assert response.json()["result"]["cleanup_pending"] is False
    assert vendor.revoked == []
    assert own._row(auth_client)["connected"] is False


def test_removed_server_disconnect_remains_an_http_action(
    auth_client,
    organization,
    config,
    vendor,
):
    own._connect(auth_client, vendor)
    own._set_server(organization, SERVER_ID, None)
    response = auth_client.delete(own._row(auth_client)["disconnect_url"])
    assert response.status_code == 200
    assert response.json()["result"]["gateway_synced"] is True
    assert own._rows(auth_client) == []
