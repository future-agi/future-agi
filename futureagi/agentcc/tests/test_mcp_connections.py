"""A member's own accounts on the organization's per-person MCP servers
(M25x), as the console's "Your accounts" panel reads them:
``GET /agentcc/mcp/connect/``.

Each test is one property. Connections are made through the real connect
flow against ``FakeVendor`` (answered in place of the SSRF-guarded fetcher),
so a row reads what the callback stored. No test reaches a network.

The list's own module is imported only inside the one test that calls it
directly, so this file collects on a build without the list and each test
then fails on what it asserts.
"""

import json
from unittest.mock import patch

import pytest
from django.test import Client

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog, gateway_client
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests.mcp_oauth_helpers import member as org_member
from agentcc.tests.mcp_oauth_helpers import new_org
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_ID,
    SERVER_URL,
    START,
    FakeVendor,
    member,
    move_server,
    seed_config,
)

pytestmark = pytest.mark.django_db

LIST = "/agentcc/mcp/connect/"
DISCONNECT = "/agentcc/mcp/connect/{}/"
ORG_CLIENT_ID = "fake-own-connections-org-client-id"
ORG_SECRET = "fake-own-connections-org-secret-not-real"
ROW_KEYS = {
    "server_id",
    "connected",
    "cleanup_pending",
    "upstream_revocation",
    "status",
    "connected_at",
    "rotated_at",
    "start",
    "start_url",
    "disconnect_url",
}


@pytest.fixture(autouse=True)
def _settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)


@pytest.fixture
def vendor():
    fake = FakeVendor()
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        yield fake


@pytest.fixture(autouse=True)
def purges():
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    with patch.object(connect.gateway_credentials, "purge", purge):
        yield calls


def _servers(**extra):
    servers = {
        SERVER_ID: {
            "url": SERVER_URL,
            "transport": "http",
            "credential": {"mode": "per_user"},
        },
        "shared-one": {
            "url": "https://mcp.shared.example/mcp",
            "transport": "http",
            "credential": {"mode": "shared"},
        },
        "shared-oauth": {
            "url": "https://mcp.shared-oauth.example/mcp",
            "transport": "http",
            "credential": {"mode": "shared", "kind": "oauth"},
        },
        "no-key": {
            "url": "https://mcp.open.example/mcp",
            "transport": "http",
            "credential": {"mode": "none"},
        },
    }
    servers.update(extra)
    return servers


def _seed(org, servers=None, *, enabled=True):
    config = seed_config(org, servers if servers is not None else _servers())
    config.mcp["enabled"] = enabled
    config.save(update_fields=["mcp"])
    return config


def _set_server(org, server_id, entry):
    config = AgentccOrgConfig.no_workspace_objects.get(organization=org, is_active=True)
    if entry is None:
        config.mcp["servers"].pop(server_id, None)
    else:
        config.mcp["servers"][server_id] = entry
    config.save(update_fields=["mcp"])


@pytest.fixture
def config(organization, user):
    return _seed(organization)


def _org_app(org, actor, server_id=SERVER_ID):
    return vault.create_oauth_client(
        org,
        server_id=server_id,
        client_id=ORG_CLIENT_ID,
        client_secret=vault.SealedSecret(ORG_SECRET),
        actor=actor,
    )


def _connect(client, vendor):
    """A whole connect of the caller's own account: the start, the vendor's
    sign-in, the callback in the browser that started it."""
    response = client.post(START.format(SERVER_ID), {}, format="json")
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    browser = Client()
    browser.cookies[name] = response.cookies[name].value
    code, state = vendor.authorize(url)
    page = browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})
    assert page.status_code == 200, page.content


def _rows(client, **params):
    response = client.get(LIST, params)
    assert response.status_code == 200, response.content
    rows = response.json()["result"]["connections"]
    for row in rows:
        assert set(row) == ROW_KEYS
    return rows


def _row(client, server_id=SERVER_ID):
    (row,) = [r for r in _rows(client) if r["server_id"] == server_id]
    return row


# --- Property: a member reads their own account, never a teammate's -----------


def test_a_member_reads_their_own_connection_and_never_a_teammates(
    auth_client, organization, user, workspace, config, vendor
):
    teammate, teammate_client = member(organization, workspace)
    _connect(auth_client, vendor)

    mine = _row(auth_client)
    theirs = _row(teammate_client)

    assert (mine["connected"], mine["status"]) == (True, "active")
    assert mine["connected_at"] is not None
    assert mine["disconnect_url"] == DISCONNECT.format(SERVER_ID)
    # The teammate's row is about the teammate alone: nothing of the member's.
    assert (theirs["connected"], theirs["status"]) == (False, None)
    assert (theirs["connected_at"], theirs["rotated_at"]) == (None, None)
    assert theirs["disconnect_url"] is None
    assert theirs["start"] == mine["start"]


def test_another_organization_reads_nothing_of_this_one(
    auth_client, organization, config, vendor
):
    _connect(auth_client, vendor)
    other = new_org("own-connections-other-org")
    _seed(other, {})
    _, outsider = org_member(other, name="Outsider")

    assert _rows(outsider) == []
    assert _rows(outsider, server_id=SERVER_ID) == []


def test_a_caller_who_is_not_a_member_is_refused(organization, config):
    stranger_org = new_org("own-connections-stranger-org")
    stranger, _ = org_member(stranger_org, name="Stranger")

    from agentcc.services import mcp_connections

    with pytest.raises(connect.ConnectRefused) as refused:
        mcp_connections.own_connections(organization, stranger)

    assert (refused.value.code, refused.value.status) == ("not_member", 403)


# --- Property: only per-person servers added by URL are offered ----------------


def test_shared_no_key_and_catalogue_servers_are_not_offered(
    auth_client, organization, config
):
    _set_server(
        organization,
        "slate-mail",
        {
            "url": "https://mcp.slate.example/mcp",
            "transport": "http",
            "credential": {"mode": "per_user"},
            "catalog_ref": "slate-mail/slate-oauth@1.0.0",
        },
    )

    def provider_record(entry):
        return {"app": "slate-mail"} if entry.get("catalog_ref") else None

    with patch.object(connector_catalog, "provider_record", provider_record):
        rows = _rows(auth_client)

    assert [row["server_id"] for row in rows] == [SERVER_ID]


def test_a_server_id_names_one_row(auth_client, organization, config):
    _set_server(
        organization,
        "tickets",
        {
            "url": "https://mcp.tickets.example/mcp",
            "transport": "http",
            "credential": {"mode": "per_user"},
        },
    )

    assert [r["server_id"] for r in _rows(auth_client)] == [SERVER_ID, "tickets"]
    assert [r["server_id"] for r in _rows(auth_client, server_id="tickets")] == [
        "tickets"
    ]
    assert _rows(auth_client, server_id="shared-one") == []


# --- Property: readiness comes from stored state, and never claims an app -----


def test_ready_only_with_the_organizations_own_app_for_the_server(
    auth_client, organization, user, config
):
    _org_app(organization, user)

    row = _row(auth_client)

    assert row["start"] == {"state": "ready", "reason": "org_oauth_app"}
    assert row["start_url"] == START.format(SERVER_ID)


def test_without_the_organizations_app_readiness_is_unknown_not_an_app_to_add(
    auth_client, config
):
    # The server may take our client metadata document or register a client
    # for us; only its metadata says so, and the read fetches nothing.
    def never(*args, **kwargs):
        raise AssertionError("the read reached the network")

    with patch.object(connect.mcp_safe_fetch, "fetch", never):
        row = _row(auth_client)

    assert row["start"] == {"state": "unknown", "reason": "client_not_confirmed"}
    assert row["start_url"] is None
    assert AgentccMCPConnectState.objects.count() == 0


def test_an_unreadable_organization_app_needs_an_owner_or_admin(
    auth_client, organization, user, config
):
    app = _org_app(organization, user)
    AgentccMCPCredential.objects.filter(pk=app.pk).update(
        encrypted_secret="enc::not-a-ciphertext"
    )

    row = _row(auth_client)

    assert row["start"] == {"state": "needs_admin", "reason": "oauth_client_unreadable"}
    assert row["start_url"] is None


def test_mcp_off_and_no_token_channel_make_the_start_unavailable(
    auth_client, organization, user, monkeypatch
):
    config = _seed(organization)
    _org_app(organization, user)
    config.mcp["enabled"] = False
    config.save(update_fields=["mcp"])
    assert _row(auth_client)["start"] == {"state": "unavailable", "reason": "mcp_off"}

    config.mcp["enabled"] = True
    config.save(update_fields=["mcp"])
    monkeypatch.delenv(connect.INTERNAL_SECRET_ENV)

    row = _row(auth_client)

    assert row["start"] == {"state": "unavailable", "reason": "per_user_channel_off"}
    assert row["start_url"] is None


# --- Property: an account stays manageable when it cannot be started ---------


def test_a_changed_server_keeps_the_members_disconnect(
    auth_client, organization, user, config, vendor
):
    _connect(auth_client, vendor)
    _set_server(organization, SERVER_ID, _servers()["shared-one"])

    row = _row(auth_client)

    assert row["start"] == {"state": "unavailable", "reason": "server_changed"}
    # Still stored as active, but the gateway no longer uses it.
    assert (row["status"], row["connected"]) == ("active", False)
    assert row["start_url"] is None
    assert row["disconnect_url"] == DISCONNECT.format(SERVER_ID)
    assert auth_client.delete(row["disconnect_url"]).status_code == 200
    assert _rows(auth_client) == []


def test_a_removed_server_keeps_the_members_disconnect(
    auth_client, organization, config, vendor
):
    _connect(auth_client, vendor)
    _set_server(organization, SERVER_ID, None)

    row = _row(auth_client)

    assert row["start"] == {"state": "unavailable", "reason": "server_changed"}
    assert row["connected"] is False
    assert row["disconnect_url"] == DISCONNECT.format(SERVER_ID)


def test_a_moved_server_reads_as_needing_a_new_sign_in(
    auth_client, organization, config, vendor
):
    _connect(auth_client, vendor)
    move_server(organization, "https://mcp.elsewhere.example/mcp")

    row = _row(auth_client)

    assert (row["connected"], row["status"]) == (False, "needs_reauth")
    assert row["disconnect_url"] == DISCONNECT.format(SERVER_ID)


def test_after_disconnect_the_row_reads_revoked_with_nothing_to_disconnect(
    auth_client, organization, user, config
):
    _org_app(organization, user)
    vendor = FakeVendor(confidential={ORG_CLIENT_ID: ORG_SECRET})
    with patch.object(connect.mcp_safe_fetch, "fetch", vendor.fetch):
        _connect(auth_client, vendor)
        assert auth_client.delete(DISCONNECT.format(SERVER_ID)).status_code == 200

    row = _row(auth_client)

    assert (row["connected"], row["status"]) == (False, "revoked")
    assert row["disconnect_url"] is None
    assert row["start"] == {"state": "ready", "reason": "org_oauth_app"}


# --- Property: no row carries a secret or anything of the organization's app --


def test_no_row_carries_a_token_a_client_or_where_it_signs_in(
    auth_client, organization, user, workspace, config
):
    _org_app(organization, user)
    teammate, teammate_client = member(organization, workspace)
    vendor = FakeVendor(confidential={ORG_CLIENT_ID: ORG_SECRET})
    with patch.object(connect.mcp_safe_fetch, "fetch", vendor.fetch):
        _connect(teammate_client, vendor)

    body = json.dumps([auth_client.get(LIST).json(), teammate_client.get(LIST).json()])

    tokens = [t[k] for t in vendor.issued for k in ("access_token", "refresh_token")]
    for value in [*tokens, ORG_SECRET, ORG_CLIENT_ID, ISSUER, "vendor.example"]:
        assert value not in body
    assert str(teammate.pk) not in body
    assert str(user.pk) not in body
