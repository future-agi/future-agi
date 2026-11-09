"""K2 sign-in needs the organization's own app registration (W4b, G14b).

- Off by default: without ``AGENTCC_CONNECTORS_RUNTIME_URL`` a pilot server's
  sign-in stays held back exactly as before, and nothing is fetched.
- With the runtime on and no registration of the org's own, the platform
  refuses before anything is stored or fetched, even where FutureAGI's
  platform client is configured, and the panel says the registration is
  required.
- The registration goes through the existing vault route, which never
  returns its secret; the panel then offers the sign-in.
- A member's token stays tied to the registration that issued it.
- Disconnect revokes a grant only as that registration: never as the
  platform client, even one with the same client id, and with the runtime
  switched off too. While the registration is gone or replaced nothing is
  sent and the revocation stays pending, so a later Disconnect retries it.
- A pilot server whose URL is not the operator's runtime stays held back.

Each test is one property, through the real routes; the authorization server
is a loopback fake (``connector_k2_helpers``). Every value is a fake.
"""

import json

import pytest

from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import mcp_credentials as vault
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests.connector_k2_flow import CLIENTS, GMAIL
from agentcc.tests.connector_k2_helpers import (
    GOOGLE_CLIENT_ID,
    GOOGLE_SECRET,
    PLATFORM_ID,
    PLATFORM_SECRET,
    SECRETS,
    k2_entry,
)
from agentcc.tests.mcp_upstream_oauth_helpers import START

pytestmark = pytest.mark.django_db

k2_settings = flow.k2_settings
servers = flow.servers
k2_bundle = flow.k2_bundle
runtime = flow.runtime
purges = flow.purges

REQUIRED = {"state": "needs_admin", "reason": "org_oauth_app_required"}
DISCONNECT = f"/agentcc/mcp/connect/{GMAIL}/"
REPLACEMENT_ID = "k2-synthetic-google-replacement.apps.example.test"
REPLACEMENT_SECRET = "k2-synthetic-google-replacement-secret-not-real"


@pytest.fixture(autouse=True)
def _k2(k2_settings, k2_bundle, purges):
    """The runtime stays off unless a test asks for ``runtime``."""


def _start(client, server_id=GMAIL):
    return client.post(START.format(server_id), {}, format="json")


# --- Off by default ----------------------------------------------------------------


def test_off_by_default_a_pilot_server_stays_held_back(
    auth_client, organization, user, servers
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)

    started = _start(auth_client)

    assert started.status_code == 409
    assert started.json()["code"] == "sign_in_not_served"
    assert flow.listed(auth_client, GMAIL) is None
    assert servers.requests == []
    assert not AgentccMCPConnectState.objects.exists()


# --- The platform refuses an org without its own registration -------------------------------


def test_no_registration_refuses_before_anything_is_stored_and_never_uses_ours(
    auth_client, organization, user, servers, runtime, monkeypatch
):
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", PLATFORM_SECRET)
    flow.seed(organization, gmail=k2_entry(GMAIL))

    row = flow.listed(auth_client, GMAIL)
    started = _start(auth_client)
    fetched = flow.fetch(organization, user, GMAIL)

    assert row is not None, "the pilot server is not on the accounts panel"
    assert row["start"] == REQUIRED
    assert row["start_url"] is None
    assert row["connected"] is False
    assert started.status_code == 409
    body = started.json()
    assert body["code"] == "connector_oauth_org_client_required"
    assert "Google OAuth app registration" in json.dumps(body)
    assert servers.requests == []
    assert not AgentccMCPConnectState.objects.exists()
    assert fetched["error"] == "no_credential"


def test_a_pilot_server_off_the_runtime_stays_held_back(
    auth_client, organization, user, servers, runtime
):
    elsewhere = k2_entry(GMAIL, url="https://k2.elsewhere.example/gmail/mcp")
    flow.seed(organization, gmail=elsewhere)
    flow.register(organization, user)

    started = _start(auth_client)

    assert started.status_code == 409
    assert started.json()["code"] == "sign_in_not_served"
    assert flow.listed(auth_client, GMAIL) is None
    assert servers.requests == []


# --- The registration goes through the existing vault route -----------------------------------


def test_registration_is_stored_through_the_vault_route_and_never_returned(
    auth_client, organization, user, servers, runtime
):
    flow.seed(organization, gmail=k2_entry(GMAIL))

    stored = auth_client.post(
        CLIENTS,
        {
            "client_group": "google",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_SECRET,
        },
        format="json",
    )
    clients = auth_client.get(CLIENTS)
    row = flow.listed(auth_client, GMAIL)

    assert stored.status_code in (200, 201), stored.status_code
    shown = stored.content.decode() + clients.content.decode()
    assert [s for s in SECRETS if s in shown] == []
    assert row is not None, "the pilot server is not on the accounts panel"
    assert row["start"] == {"state": "ready", "reason": "org_oauth_app"}
    assert row["start_url"] == START.format(GMAIL)


def test_an_unreadable_registration_needs_an_admin(
    auth_client, organization, user, servers, runtime
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    type(registration).objects.filter(pk=registration.pk).update(
        encrypted_secret="enc::not-a-ciphertext"
    )

    row = flow.listed(auth_client, GMAIL)
    started = _start(auth_client)

    assert row is not None, "the pilot server is not on the accounts panel"
    assert row["start"] == {"state": "needs_admin", "reason": "oauth_client_unreadable"}
    assert started.status_code == 409
    assert started.json()["code"] == "connector_oauth_client_unreadable"
    assert servers.requests == []


# --- A token stays tied to the registration that issued it -------------------------------------


def test_removing_the_registration_stops_serving_the_token(
    auth_client, organization, user, servers, runtime
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    flow.sign_in(auth_client, servers, GMAIL)
    before = flow.listed(auth_client, GMAIL)

    vault.revoke_oauth_client(registration, user)
    after = flow.listed(auth_client, GMAIL)
    fetched = flow.fetch(organization, user, GMAIL)

    assert before["connected"] is True
    assert fetched["error"] == "needs_reauth"
    assert "access_token" not in fetched
    assert after["connected"] is False
    assert after["start"] == REQUIRED


# --- A grant is revoked only as the registration that issued it -----------------------------


def test_disconnect_never_revokes_as_the_platform_client_with_the_same_id(
    auth_client, organization, user, servers, runtime, purges, monkeypatch
):
    flow.platform_has_the_same_app(monkeypatch)
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    flow.sign_in(auth_client, servers, GMAIL)
    vault.revoke_oauth_client(registration, user)
    reads = flow.platform_reads(monkeypatch)

    gone = auth_client.delete(DISCONNECT)

    assert reads == []
    flow.assert_revocation_waits(gone, organization, user, servers, purges)


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_disconnect_sends_nothing_while_the_registration_is_gone(
    change, auth_client, organization, user, servers, runtime, purges
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    flow.sign_in(auth_client, servers, GMAIL)
    vault.revoke_oauth_client(registration, user)
    if change == "replaced":
        flow.register(organization, user, "google", REPLACEMENT_ID, REPLACEMENT_SECRET)

    gone = auth_client.delete(DISCONNECT)

    flow.assert_revocation_waits(gone, organization, user, servers, purges)


def test_a_grant_stays_the_registrations_after_the_runtime_is_switched_off(
    auth_client, organization, user, servers, runtime, purges, monkeypatch, settings
):
    flow.platform_has_the_same_app(monkeypatch)
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    flow.sign_in(auth_client, servers, GMAIL)
    vault.revoke_oauth_client(registration, user)
    setattr(settings, flow.SETTING, "")
    flow.published_metadata(monkeypatch, servers)
    reads = flow.platform_reads(monkeypatch)

    gone = auth_client.delete(DISCONNECT)

    assert reads == []
    flow.assert_revocation_waits(gone, organization, user, servers, purges)


def test_a_pending_revocation_confirms_once_the_same_app_is_registered_again(
    auth_client, organization, user, servers, runtime, purges
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    flow.sign_in(auth_client, servers, GMAIL)
    vault.revoke_oauth_client(registration, user)
    first = auth_client.delete(DISCONNECT)
    flow.register(organization, user)

    retried = auth_client.delete(DISCONNECT)

    assert first.json()["result"]["upstream_revocation"] == "pending"
    assert retried.status_code == 200
    assert retried.json()["result"]["upstream_revocation"] == "confirmed"
    assert servers.revoked == [servers.issued[0]["refresh_token"]]
    revokes = [(r[4], r[5]) for r in servers.requests if r[2] == "revoke"]
    assert revokes == [("post", True)]
