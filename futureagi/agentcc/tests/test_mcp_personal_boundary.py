"""Personal-account HTTP conformance: real bearer/key auth, no external I/O."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import mcp_connections
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests import test_mcp_connections as own
from agentcc.tests.mcp_upstream_oauth_helpers import (
    CALLBACK_PATH,
    ISSUER,
    SERVER_ID,
    START,
    member,
)
from agentcc.tests.test_mcp_connections import (
    DISCONNECT,
    LIST,
    _connect,
    _org_app,
    _row,
    _rows,
    _set_server,
)

pytestmark = pytest.mark.django_db
_settings = own._settings
config = own.config
purges = own.purges
vendor = own.vendor


def bearer(user):
    token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS,
        last_used_at=timezone.now(),
    )
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION="Bearer "
        + generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    )
    return client


def key_client(user, org, kind="user"):
    key = OrgApiKey.objects.create(organization=org, user=user, type=kind)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def counts():
    return (
        OrganizationMembership.all_objects.count(),
        WorkspaceMembership.all_objects.count(),
        AgentccMCPConnectState.objects.count(),
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", LIST),
        ("post", START.format(SERVER_ID)),
        ("delete", DISCONNECT.format(SERVER_ID)),
        ("delete", DISCONNECT.format(SERVER_ID) + "?mode=shared"),
    ],
)
def test_org_key_is_not_an_individual_before_any_provisioning(
    organization,
    user,
    config,
    vendor,
    method,
    path,
):
    # The fixture organization may already have its system key.
    key = OrgApiKey.objects.filter(organization=organization, type="system").first()
    if key is None:
        key = OrgApiKey.objects.create(organization=organization, type="system")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    before = counts()
    response = getattr(client, method)(path)
    assert response.status_code in (401, 403)
    assert counts() == before
    assert not vendor.requests


@pytest.mark.parametrize("kind", ["user", "mcp"])
@pytest.mark.parametrize("membership", ["missing", "inactive", "deleted"])
def test_key_membership_is_checked_before_workspace_resolution(
    organization,
    user,
    workspace,
    config,
    vendor,
    kind,
    membership,
):
    person, _ = member(organization, workspace)
    client = key_client(person, organization, kind)
    WorkspaceMembership.all_objects.filter(user=person).delete()
    rows = OrganizationMembership.all_objects.filter(user=person)
    if membership == "missing":
        rows.delete()
        # No legacy organization pointer can count as membership for this key.
        person.organization = None
        person.save(update_fields=["organization"])
    else:
        rows.update(
            **({"is_active": False} if membership == "inactive" else {"deleted": True})
        )
    before = counts()
    for method, path in (
        ("get", LIST),
        ("post", START.format(SERVER_ID)),
        ("delete", DISCONNECT.format(SERVER_ID)),
    ):
        assert getattr(client, method)(path).status_code in (401, 403)
        assert counts() == before
    assert not vendor.requests


@pytest.mark.parametrize("auth", ["bearer", "user", "mcp"])
def test_individual_identity_survives_real_auth_and_never_provisions(
    organization,
    user,
    workspace,
    config,
    vendor,
    auth,
):
    person, _ = member(organization, workspace)
    client = (
        bearer(person) if auth == "bearer" else key_client(person, organization, auth)
    )
    before = counts()
    witnessed = []
    original = mcp_connections.own_connections

    def observe(org, actor, server_id=None):
        witnessed.append((org.pk, actor.pk))
        return original(org, actor, server_id)

    with patch.object(mcp_connections, "own_connections", observe):
        assert client.get(LIST).status_code == 200
    assert witnessed == [(organization.pk, person.pk)]
    assert counts() == before
    _connect(client, vendor)
    assert Credential.objects.get(kind="oauth", scope="user").user_id == person.pk
    assert (
        client.delete(DISCONNECT.format(SERVER_ID) + "?mode=shared").status_code == 403
    )
    assert client.delete(DISCONNECT.format(SERVER_ID)).status_code == 200


@pytest.mark.parametrize("kind", ["user", "mcp"])
def test_sealed_personal_key_pair_authenticates_and_a_wrong_secret_is_refused(
    organization,
    user,
    workspace,
    config,
    vendor,
    kind,
):
    # A key saved through OrgApiKey.save() is sealed at rest: the row keeps the
    # secret's keyed hash and an empty plaintext column, and only the issuing
    # instance holds the secret (accounts/api_key_lookup.py seal_before_save).
    # The account-connect routes verify the pair as every other key path does
    # (find_api_key), never by comparing the plaintext column, so an empty or
    # wrong secret is refused and the issued one is accepted.
    person, _ = member(organization, workspace)
    key = OrgApiKey.objects.create(organization=organization, user=person, type=kind)
    secret = key.secret_key
    row = OrgApiKey.no_workspace_objects.get(pk=key.pk)
    assert secret
    assert row.secret_key == ""
    assert row.secret_key_hash

    def presenting(presented):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=presented)
        return client

    wrong = secret[:-1] + ("0" if secret[-1] != "0" else "1")
    before = counts()
    witnessed = []
    original = mcp_connections.own_connections

    def observe(org, actor, server_id=None):
        witnessed.append((org.pk, actor.pk))
        return original(org, actor, server_id)

    with patch.object(mcp_connections, "own_connections", observe):
        for refused in (wrong, ""):
            response = presenting(refused).get(LIST)
            assert response.status_code in (401, 403)
        assert witnessed == []
        assert presenting(secret).get(LIST).status_code == 200
    assert witnessed == [(organization.pk, person.pk)]
    assert counts() == before
    assert not vendor.requests


def test_read_does_not_create_workspace_membership(organization, user, config):
    WorkspaceMembership.all_objects.filter(user=user).delete()
    before = counts()
    assert bearer(user).get(LIST).status_code == 200
    assert counts() == before


def test_synthetic_principal_is_refused(config, organization):
    client = APIClient()
    client.force_authenticate(
        user=SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            pk="service-principal",
            get_membership_level=lambda org: 15,
        )
    )
    before = counts()
    assert client.get(LIST).status_code == 403
    assert counts() == before


def test_callback_requires_attested_individual_start(auth_client, config, vendor):
    response = auth_client.post(START.format(SERVER_ID), {}, format="json")
    row = AgentccMCPConnectState.objects.get()
    assert getattr(row, "individual_authenticated", False) is True
    AgentccMCPConnectState.objects.filter(pk=row.pk).update(
        individual_authenticated=False
    )
    browser = Client()
    browser.cookies = response.cookies
    code, state = vendor.authorize(response.json()["result"]["authorize_url"])
    page = browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})
    assert page.status_code == 400
    assert not Credential.objects.filter(kind="oauth").exists()
    assert not vendor.issued


@pytest.mark.parametrize("change", ["inactive", "removed"])
def test_callback_rechecks_person_membership(
    auth_client,
    organization,
    user,
    config,
    vendor,
    change,
):
    response = auth_client.post(START.format(SERVER_ID), {}, format="json")
    if change == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    else:
        OrganizationMembership.all_objects.filter(user=user).update(deleted=True)
    browser = Client()
    browser.cookies = response.cookies
    code, state = vendor.authorize(response.json()["result"]["authorize_url"])
    assert (
        browser.get(
            CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER}
        ).status_code
        == 400
    )
    assert not vendor.issued


def test_generic_metadata_is_caller_only_and_generic_oauth_delete_requires_disconnect(
    auth_client,
    organization,
    user,
    workspace,
    config,
    vendor,
):
    _connect(auth_client, vendor)
    row = Credential.objects.get(kind="oauth", scope="user")
    person, _ = member(organization, workspace)
    reader = bearer(person)
    for suffix in ("", "?server_id=" + SERVER_ID):
        response = reader.get("/agentcc/mcp/credentials/" + suffix)
        assert response.status_code == 200
        assert str(row.pk) not in response.content.decode()
    before = (row.status, row.encrypted_refresh)
    response = auth_client.delete(f"/agentcc/mcp/credentials/{row.pk}/")
    assert response.status_code == 400
    row.refresh_from_db()
    assert (row.status, row.encrypted_refresh) == before[:2]
    assert auth_client.delete(DISCONNECT.format(SERVER_ID)).status_code == 200
    assert vendor.revoked


@pytest.mark.parametrize("refreshable", [False, True])
def test_expiry_is_projected_before_first_gateway_fetch(
    auth_client,
    config,
    vendor,
    refreshable,
):
    _connect(auth_client, vendor)
    updates = {"token_expires_at": timezone.now() - timedelta(seconds=60)}
    if not refreshable:
        updates["encrypted_refresh"] = None
    Credential.objects.filter(kind="oauth", scope="user").update(**updates)
    before = len(vendor.requests)
    row = _row(auth_client)
    assert row["connected"] is refreshable
    assert row["status"] == ("active" if refreshable else "expired")
    assert row["disconnect_url"]
    assert len(vendor.requests) == before


@pytest.mark.parametrize("disabled", ["mcp", "server", "channel"])
def test_disabled_execution_never_advertises_connected(
    auth_client,
    organization,
    user,
    config,
    vendor,
    monkeypatch,
    disabled,
):
    _connect(auth_client, vendor)
    _org_app(organization, user)
    if disabled == "channel":
        monkeypatch.delenv(connect.INTERNAL_SECRET_ENV)
    else:
        target = config.mcp if disabled == "mcp" else config.mcp["servers"][SERVER_ID]
        target["enabled"] = False
        config.save(update_fields=["mcp"])
    row = _row(auth_client)
    assert row["connected"] is False
    assert row["start"]["state"] == "unavailable"
    assert row["start_url"] is None
    assert row["disconnect_url"]


@pytest.mark.parametrize("ref", ["asana/mcp@0.9.0", "retired/app@1.0.0", ""])
def test_catalogue_provenance_does_not_depend_on_bundle_lookup(
    auth_client,
    organization,
    user,
    config,
    vendor,
    ref,
):
    entry = {**config.mcp["servers"][SERVER_ID], "catalog_ref": ref}
    _set_server(organization, SERVER_ID, entry)
    _org_app(organization, user)
    assert _rows(auth_client) == []


@pytest.mark.parametrize("failure", ["purge", "revoke"])
def test_cleanup_failure_is_persistent_and_retryable(
    auth_client,
    config,
    vendor,
    failure,
):
    _connect(auth_client, vendor)
    target = connect.gateway_credentials if failure == "purge" else connect
    method = "purge" if failure == "purge" else "_revoke_upstream"
    with patch.object(target, method, return_value=False):
        result = auth_client.delete(DISCONNECT.format(SERVER_ID))
    assert result.status_code == 200
    assert result.json()["result"].get("cleanup_pending") is True
    row = _row(auth_client)
    assert row["connected"] is False
    assert row.get("cleanup_pending") is True
    assert row["disconnect_url"]
    retry = auth_client.delete(row["disconnect_url"])
    assert retry.status_code == 200
    assert retry.json()["result"]["gateway_synced"] is True
    assert retry.json()["result"]["cleanup_pending"] is False
    assert _row(auth_client)["disconnect_url"] is None


@pytest.mark.parametrize("query", ["mode=SHARED", "unknown=1", "mode=user&mode=shared"])
def test_disconnect_query_is_strict(auth_client, config, query):
    assert (
        auth_client.delete(DISCONNECT.format(SERVER_ID) + "?" + query).status_code
        == 400
    )
