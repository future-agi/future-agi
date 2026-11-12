"""Real catalogue -> registration -> Google-shaped loopback sign-in -> revoke.

No provider endpoint is contacted: the bundle retains the shipped pilot
records, with authorization endpoints moved to the local synthetic server.
"""

import json

import pytest

from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog as catalog
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests import test_connector_catalog as catalog_fixtures
from agentcc.tests.connector_k2_helpers import (
    GOOGLE_CLIENT_ID,
    GOOGLE_SECRET,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_SECRET,
    PILOTS,
    SINGLE_TENANT,
)
from agentcc.tests.mcp_upstream_oauth_helpers import CALLBACK

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
ADD = "/agentcc/connectors/apps/gmail/add/"
BODY = {"provider": "futureagi-rest", "mode": "user"}


@pytest.fixture(autouse=True)
def setup(k2_settings, k2_bundle, purges, probe_budget, drift_free):
    pass


@pytest.fixture
def microsoft_connect_on(settings):
    """Microsoft connect switched on, as a deployment that lifts the hold.

    It is off by default (owner hold); the hold itself is covered by
    test_microsoft_connect_gate.py and test_microsoft_coming_soon_render.py.
    """
    settings.MICROSOFT_CONNECT_ENABLED = True


def _gmail(client):
    response = client.get(CATALOG)
    assert response.status_code == 200
    rows = [row for row in response.json()["results"] if row["id"] == "gmail"]
    assert len(rows) == 1, "The served Gmail pilot must be in the catalogue"
    return rows[0]


def _assert_all_coming_soon(client):
    """No pilot is served: each is a Coming soon card with no provider."""
    rows = client.get(CATALOG).json()["results"]
    assert {row["id"] for row in rows} == set(PILOTS)
    for row in rows:
        assert row["status"] == "coming_soon", row["id"]
        assert row["providers"] == [], row["id"]


def test_pilots_visible_with_registration_required(
    auth_client, runtime, servers, microsoft_connect_on
):
    response = auth_client.get(CATALOG)
    assert response.status_code == 200
    apps = response.json()["results"]
    assert {a["id"] for a in apps} == set(PILOTS)
    for app in apps:
        (provider,) = app["providers"]
        assert provider["credential"]["mode"] == "per_user"
        assert provider["account_modes"] == ["user"]
        assert provider["connect"]["state"] == "registration_required"
        assert provider["connect"]["reason"] == "org_oauth_app_required"
        assert provider["connect"]["own_app"]["redirect_uri"] == CALLBACK
        assert provider["connect"]["own_app"]["required"] is True
    assert servers.requests == []


def test_registration_state_is_not_cached_between_organizations(
    auth_client, organization, user, runtime
):
    assert (
        _gmail(auth_client)["providers"][0]["connect"]["state"]
        == "registration_required"
    )
    other_org = Organization.objects.create(name="Other synthetic K2 organization")
    flow.register(other_org, user)
    other_card = catalog.app_for_org(other_org, user, "gmail")
    assert other_card["providers"][0]["connect"]["state"] == "ready"
    assert (
        _gmail(auth_client)["providers"][0]["connect"]["state"]
        == "registration_required"
    )
    denied = auth_client.post(ADD, BODY, format="json")
    assert denied.status_code == 409
    assert denied.json()["code"] == "connector_oauth_org_client_required"
    started = auth_client.post("/agentcc/mcp/connect/gmail/start/", {}, format="json")
    assert started.status_code != 200
    assert not AgentccMCPConnectState.objects.exists()
    row = flow.register(organization, user)
    assert _gmail(auth_client)["providers"][0]["connect"]["state"] == "ready"
    row.status = "revoked"
    row.save(update_fields=["status"])
    assert (
        _gmail(auth_client)["providers"][0]["connect"]["state"]
        == "registration_required"
    )


def test_missing_registration_refuses_add_without_state_or_network(
    auth_client, organization, runtime, servers, pushes
):
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()
    response = auth_client.post(ADD, BODY, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "connector_oauth_org_client_required"
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == before
    )
    assert not AgentccMCPConnectState.objects.exists()
    assert servers.requests == []
    assert pushes == []


def test_real_catalog_add_then_registered_authorization_and_revoke(
    auth_client, organization, user, runtime, servers, pushes
):
    registration = auth_client.post(
        flow.CLIENTS,
        {
            "client_group": "google",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_SECRET,
        },
        format="json",
    )
    assert registration.status_code == 201
    assert GOOGLE_SECRET not in registration.content.decode()
    added = auth_client.post(ADD, BODY, format="json")
    assert added.status_code == 201, added.json().get("code")
    result = added.json()["result"]
    assert result["start_url"] == "/agentcc/mcp/connect/gmail/start/"
    assert result["credential"]["connected"] is False
    assert "baseline" not in result  # no fictional unauthenticated live listing
    assert servers.requests == []
    assert len(pushes) == 1
    app = _gmail(auth_client)
    assert app["servers"][0]["start_url"] == result["start_url"]
    url, page = flow.sign_in(auth_client, servers, "gmail")
    assert url.startswith(servers.origin + "/google/o/oauth2/v2/auth?")
    assert flow.query(url)["client_id"] == GOOGLE_CLIENT_ID
    assert page.status_code == 200
    assert _gmail(auth_client)["connected"] is True
    gone = auth_client.delete("/agentcc/mcp/connect/gmail/")
    assert gone.status_code == 200
    assert len(servers.revoked) == 1
    assert _gmail(auth_client)["connected"] is False


def test_runtime_toggle_invalidates_planning_cache(
    auth_client, settings, runtime, microsoft_connect_on
):
    assert len(auth_client.get(CATALOG).json()["results"]) == len(PILOTS)
    settings.AGENTCC_CONNECTORS_RUNTIME_URL = ""
    _assert_all_coming_soon(auth_client)


def test_unregistered_or_replaced_grants_are_not_connected(
    auth_client, organization, user, runtime, servers, pushes
):
    registration = flow.register(organization, user)
    assert auth_client.post(ADD, BODY, format="json").status_code == 201
    flow.sign_in(auth_client, servers, "gmail")
    assert _gmail(auth_client)["connected"] is True
    registration.status = "revoked"
    registration.save(update_fields=["status"])
    app = _gmail(auth_client)
    assert app["connected"] is False
    assert app["servers"][0]["start_url"] is None


def test_runtime_is_off_by_default(auth_client, microsoft_connect_on):
    _assert_all_coming_soon(auth_client)
    assert auth_client.post(ADD, BODY, format="json").status_code == 409


def test_catalogue_still_refuses_nonpilot_oauth(
    auth_client, runtime, k2_bundle, microsoft_connect_on
):
    data = json.loads(k2_bundle.read_text())
    for provider in data["providers"]:
        provider["publisher"] = "unserved"
    k2_bundle.write_text(json.dumps(data))
    _assert_all_coming_soon(auth_client)


@pytest.mark.parametrize("tenant", [None, SINGLE_TENANT])
def test_microsoft_catalogue_add_signin_and_local_revoke(
    auth_client,
    organization,
    user,
    runtime,
    servers,
    pushes,
    tenant,
    microsoft_connect_on,
):
    flow.register(
        organization, user, "microsoft", MICROSOFT_CLIENT_ID, MICROSOFT_SECRET
    )
    variables = {} if tenant is None else {"tenant": tenant}
    added = auth_client.post(
        "/agentcc/connectors/apps/microsoft-outlook/add/",
        {**BODY, "url_variables": variables},
        format="json",
    )
    assert added.status_code == 201, added.json().get("code")
    assert added.json()["result"]["url_variables"] == {
        "tenant": tenant or "organizations"
    }
    url, page = flow.sign_in(auth_client, servers, "microsoft-outlook")
    assert page.status_code == 200
    assert flow.query(url)["client_id"] == MICROSOFT_CLIENT_ID
    assert servers.tenants and set(servers.tenants) == {tenant or "organizations"}
    gone = auth_client.delete("/agentcc/mcp/connect/microsoft-outlook/")
    assert gone.status_code == 200
    assert flow.account(organization, user, "microsoft-outlook").status == "revoked"
    # Microsoft declares no revocation endpoint; do not claim vendor revocation.
    assert gone.json()["result"]["upstream_revocation"] == "unconfirmed"
    assert servers.revoked == []


def test_catalogue_nonmember_has_no_personal_start_after_add(
    auth_client, organization, user, runtime, pushes
):
    flow.register(organization, user)
    assert _gmail(auth_client)["providers"][0]["connect"]["state"] == "ready"
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(is_active=False)
    # An org-key caller can reach the catalogue without individual membership.
    # Exercise the services with that identity, not the individual-only routes.
    added = catalog.add_connector(
        organization,
        "gmail",
        provider_id="futureagi-rest",
        mode="user",
        url_variables={},
        actor=user,
    )
    assert added["start_url"] is None
    app = catalog.app_for_org(organization, user, "gmail")
    assert app["connected"] is False
    assert app["servers"][0]["start_url"] is None
    assert len(pushes) == 1


@pytest.mark.parametrize("auth", [{"kind": "none"}, {"kind": "mcp_oauth"}])
def test_arcade_hidden_and_add_refused_without_side_effects(
    auth_client, organization, runtime, k2_bundle, servers, pushes, auth
):
    # G14b's Arcade binding requires an EMPTY catalog_ref. A nominally valid
    # catalogue record cannot make that inert, customer-gateway adapter served.
    data = json.loads(k2_bundle.read_text())
    data["apps"].append(
        catalog_fixtures._app(
            "arcade",
            "Arcade",
            "Synthetic unsupported gateway",
            category="other",
            categories=["other"],
            default="gateway",
        )
    )
    data["providers"].append(
        catalog_fixtures._provider(
            "arcade",
            "gateway",
            auth=auth,
            tools=[catalog_fixtures._tool("search", posture="auto")],
        )
    )
    k2_bundle.write_text(json.dumps(data))
    response = auth_client.get(CATALOG)
    assert response.status_code == 200
    assert "arcade" not in {row["id"] for row in response.json()["results"]}
    assert auth_client.get("/agentcc/connectors/apps/arcade/").status_code == 404
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()
    added = auth_client.post(
        "/agentcc/connectors/apps/arcade/add/",
        {"provider": "gateway", "mode": "agent"},
        format="json",
    )
    assert added.status_code == 409
    assert added.json()["code"] == "connector_arcade_catalogue_unavailable"
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == before
    )
    assert not AgentccMCPConnectState.objects.exists()
    assert servers.requests == []
    assert pushes == []
