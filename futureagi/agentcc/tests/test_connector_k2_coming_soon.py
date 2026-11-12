"""A K2 pilot the gateway cannot serve reads Coming soon, with no sign-in.

The gateway admits a K2 pilot's calls only when its catalog record declares
the org client source (``auth.oauth2.clientSources``) and the catalog owner's
connection enablement (``x-gateway.connectionEnablement``); it refuses every
other K2 call as Coming soon (agentcc-gateway internal/mcp/vendors,
client_admission.go). The shipped pilot records declare neither. With K2 on
and a runtime set, the console must say the same as the gateway: each pilot
card stays listed and reads Coming soon, offers no sign-in, and an add or a
sign-in start is refused before anything is stored or fetched. A pilot server
already in the org's config stays on the Your accounts panel and reads Coming
soon there too, for a member who never connected and for one who holds a
grant. That grant is still revoked at the declared endpoint on Disconnect, and
the gateway's token fetch answers no_credential for it. Once a record declares
what the gateway needs, its sign-in is offered as before.

Every value is a fake; the authorization server is a loopback fake
(``connector_k2_helpers``) and nothing in these tests reaches it.
"""

import json

import pytest

from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog as catalog
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests import test_connector_catalog as catalog_fixtures
from agentcc.tests.connector_k2_flow import GMAIL
from agentcc.tests.connector_k2_helpers import (
    PILOTS,
    k2_entry,
    unserved_at_gateway,
)
from agentcc.tests.mcp_upstream_oauth_helpers import START

pytestmark = pytest.mark.django_db

k2_settings = flow.k2_settings
servers = flow.servers
k2_bundle = flow.k2_bundle
runtime = flow.runtime
purges = flow.purges
probe_budget = catalog_fixtures.probe_budget
pushes = catalog_fixtures.pushes
drift_free = catalog_fixtures.drift_free

CATALOG = "/agentcc/connectors/catalog/"
ADD = f"/agentcc/connectors/apps/{GMAIL}/add/"
DISCONNECT = f"/agentcc/mcp/connect/{GMAIL}/"
BODY = {"provider": "futureagi-rest", "mode": "user"}
# The console's one Coming soon state for a provider card.
COMING_SOON = {
    "served": False,
    "state": "unavailable",
    "reason": "microsoft_coming_soon",
}
# The same state for a server's row on the Your accounts panel.
ROW_COMING_SOON = {"state": "unavailable", "reason": "microsoft_coming_soon"}


@pytest.fixture(autouse=True)
def setup(k2_settings, k2_bundle, purges, probe_budget, drift_free, settings):
    # The Microsoft hold is lifted, so Coming soon here is the gateway's alone.
    settings.MICROSOFT_CONNECT_ENABLED = True


@pytest.fixture
def shipped(k2_bundle):
    """The pilot records as the bundle ships them: no gateway declaration."""
    _edit(k2_bundle, unserved_at_gateway)
    return k2_bundle


def _edit(path, change, app_id=None):
    data = json.loads(path.read_text())
    for record in data["providers"]:
        if record["id"] == "futureagi-rest" and app_id in (None, record["app"]):
            change(record)
    path.write_text(json.dumps(data))


def _apps(client):
    response = client.get(CATALOG)
    assert response.status_code == 200
    return {app["id"]: app for app in response.json()["results"]}


def _configs(organization):
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()


def _signed_in(auth_client, organization, user, servers):
    """A member signed in to Gmail while the gateway served it."""
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)
    _, page = flow.sign_in(auth_client, servers, GMAIL)
    assert page.status_code == 200


# --- The shipped records: Coming soon, no sign-in ---------------------------------


@pytest.mark.parametrize("registered", [False, True])
def test_pilot_cards_stay_listed_and_read_coming_soon(
    auth_client, organization, user, runtime, servers, shipped, registered
):
    if registered:
        flow.register(organization, user)

    apps = _apps(auth_client)

    assert set(apps) == set(PILOTS), "every pilot card stays listed"
    for app_id in PILOTS:
        app = apps[app_id]
        assert app["status"] == "coming_soon", app_id
        assert app["default_provider"] is None, app_id
        (provider,) = app["providers"]
        assert provider["id"] == "futureagi-rest"
        assert provider["connect"] == COMING_SOON, app_id
        assert "own_app" not in provider["connect"]
    assert servers.requests == []


def test_coming_soon_add_is_refused_before_anything_is_written(
    auth_client, organization, user, runtime, servers, shipped, pushes
):
    flow.register(organization, user)
    before = _configs(organization)

    added = auth_client.post(ADD, BODY, format="json")

    assert added.status_code == 409
    assert added.json()["code"] == "connector_oauth_not_built"
    assert _configs(organization) == before
    assert pushes == []
    assert servers.requests == []
    assert not AgentccMCPConnectState.objects.exists()


def test_coming_soon_sign_in_is_refused_before_anything_is_stored(
    auth_client, organization, user, runtime, servers, shipped
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)

    started = auth_client.post(START.format(GMAIL), {}, format="json")

    assert started.status_code == 409
    assert started.json()["code"] == "sign_in_not_served"
    assert servers.requests == []
    assert not AgentccMCPConnectState.objects.exists()


def test_an_added_pilot_stays_visible_with_no_start(
    auth_client, organization, user, runtime, servers, shipped
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)

    app = catalog.app_for_org(organization, user, GMAIL)

    assert app["status"] == "coming_soon"
    assert app["providers"][0]["connect"] == COMING_SOON
    (server,) = app["servers"]
    assert server["server_id"] == GMAIL
    assert server["connected"] is False
    assert server["start_url"] is None


# --- Your accounts: the row stays, reads Coming soon, a grant stays manageable ----


@pytest.mark.parametrize("registered", [False, True])
def test_an_unserved_pilot_stays_in_your_accounts_as_coming_soon(
    auth_client, organization, user, runtime, servers, shipped, registered
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    if registered:
        flow.register(organization, user)

    row = flow.listed(auth_client, GMAIL)

    assert row is not None, "the pilot server left the accounts panel"
    assert row["start"] == ROW_COMING_SOON
    assert row["start_url"] is None
    assert row["connected"] is False
    assert row["status"] is None
    assert row["disconnect_url"] is None
    assert servers.requests == []


def test_an_unserved_pilot_off_the_runtime_stays_off_your_accounts(
    auth_client, organization, user, runtime, servers, shipped
):
    elsewhere = k2_entry(GMAIL, url="https://k2.elsewhere.example/gmail/mcp")
    flow.seed(organization, gmail=elsewhere)
    flow.register(organization, user)

    assert flow.listed(auth_client, GMAIL) is None


def test_a_member_holding_a_grant_sees_the_same_coming_soon_row(
    auth_client, organization, user, runtime, servers, k2_bundle
):
    _signed_in(auth_client, organization, user, servers)
    # The record as the bundle ships it; the smaller file is parsed again.
    _edit(k2_bundle, unserved_at_gateway)

    row = flow.listed(auth_client, GMAIL)

    assert row is not None, "the pilot server left the accounts panel"
    assert row["start"] == ROW_COMING_SOON
    assert row["start_url"] is None
    assert row["connected"] is False
    assert row["status"] == "active"
    assert row["disconnect_url"] == DISCONNECT


def test_disconnect_still_revokes_an_unserved_pilots_grant_at_the_declared_endpoint(
    auth_client, organization, user, runtime, servers, k2_bundle, purges
):
    _signed_in(auth_client, organization, user, servers)
    _edit(k2_bundle, unserved_at_gateway)

    gone = auth_client.delete(DISCONNECT)

    assert gone.status_code == 200
    assert gone.json()["result"]["upstream_revocation"] == "confirmed"
    assert servers.revoked == [servers.issued[0]["refresh_token"]]
    revokes = [(r[1], r[4], r[5]) for r in servers.requests if r[2] == "revoke"]
    assert revokes == [("google", "post", True)]
    assert flow.account(organization, user, GMAIL).status == "revoked"
    assert (str(organization.pk), GMAIL, str(user.pk)) in purges


def test_the_token_fetch_for_an_unserved_pilots_grant_answers_no_credential(
    auth_client, organization, user, runtime, servers, k2_bundle
):
    _signed_in(auth_client, organization, user, servers)
    served = flow.fetch(organization, user, GMAIL)
    _edit(k2_bundle, unserved_at_gateway)
    before = len(servers.requests)

    fetched = flow.fetch(organization, user, GMAIL)

    assert "access_token" in served
    assert fetched["error"] == "no_credential"
    assert "access_token" not in fetched
    assert "connect_hint" not in fetched
    # The grant is neither refreshed nor sent to any endpoint.
    assert servers.requests[before:] == []
    assert servers.refreshed == []


# --- Each declaration the gateway reads, mirrored ---------------------------------


def _drop_sources(record):
    record["auth"]["oauth2"].pop("clientSources")


def _sources(value):
    def change(record):
        record["auth"]["oauth2"]["clientSources"] = value

    return change


def _drop_enablement(record):
    record["x-gateway"].pop("connectionEnablement")


def _enablement(**fields):
    def change(record):
        record["x-gateway"]["connectionEnablement"].update(fields)

    return change


@pytest.mark.parametrize(
    "change",
    [
        _drop_sources,
        _sources([]),
        _sources(["platform"]),
        _sources(["org", "partner"]),
        _drop_enablement,
        _enablement(enabled=False),
        _enablement(schemaVersion=2),
        _enablement(schemaVersion=True),
        _enablement(operations=["connect", "list_tools"]),
        _enablement(operations=["connect", "list_tools", "invoke", "refresh"]),
    ],
    ids=[
        "no-client-sources",
        "empty-client-sources",
        "platform-source-only",
        "unknown-source",
        "no-enablement",
        "disabled",
        "schema-version-2",
        "schema-version-bool",
        "no-invoke",
        "extra-operation",
    ],
)
def test_a_record_short_of_the_gateway_rule_reads_coming_soon(
    auth_client, organization, user, runtime, servers, k2_bundle, change
):
    # Edited before anything reads the bundle, so no parse of it is cached.
    _edit(k2_bundle, change, GMAIL)
    flow.register(organization, user)

    app = catalog.app_for_org(organization, user, GMAIL)

    assert app["status"] == "coming_soon"
    assert app["providers"][0]["connect"] == COMING_SOON
    assert auth_client.post(ADD, BODY, format="json").status_code == 409
    assert servers.requests == []


# --- Declared as the gateway needs: the sign-in is offered ------------------------


def test_a_record_the_gateway_serves_offers_its_sign_in(
    auth_client, organization, user, runtime, servers, k2_bundle, pushes
):
    apps = _apps(auth_client)
    for app_id in PILOTS:
        connect = apps[app_id]["providers"][0]["connect"]
        assert "status" not in apps[app_id], app_id
        assert connect["served"] is True, app_id
        assert connect["state"] == "registration_required", app_id

    flow.register(organization, user)
    (provider,) = catalog.app_for_org(organization, user, GMAIL)["providers"]
    assert provider["connect"]["state"] == "ready"
    added = auth_client.post(ADD, BODY, format="json")
    assert added.status_code == 201, added.json().get("code")
    assert added.json()["result"]["start_url"] == START.format(GMAIL)
