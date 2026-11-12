"""Gateway console consent: real API requests, synthetic OAuth clients only."""

import pytest
from django.core.cache import cache

from accounts.models.organization_membership import OrganizationMembership
from agentcc.mcp_oauth import authorize, clients
from agentcc.tests import mcp_oauth_helpers as h
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
LIST = "/agentcc/mcp-oauth/consents/"


@pytest.fixture(autouse=True)
def configured(settings):
    h.configure(settings)
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def grants(organization):
    row = h.preregistered()
    resolved = clients.resolve(row.client_id)
    owner, owner_api = h.member(organization, name="First member")
    peer, peer_api = h.member(organization, name="Second member")
    admin, admin_api = h.member(organization, level=Level.ADMIN, name="Admin")
    foreign = h.new_org("Foreign organization")
    outsider, outsider_api = h.member(foreign)
    consents = [
        authorize.record_consent(person, resolved, org, h.RESOURCE_BASE, ["mcp:tools"])
        for person, org in (
            (owner, organization),
            (peer, organization),
            (outsider, foreign),
        )
    ]
    return locals()


def test_admin_can_list_org_clients_but_not_foreign_grants(grants):
    response = grants["admin_api"].get(LIST, {"scope": "organization"})
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    rows = response.json()["result"]
    assert {r["id"] for r in rows} == {r.pk for r in grants["consents"][:2]}
    assert {r["user"]["name"] for r in rows} == {"First member", "Second member"}
    assert all(not r["can_revoke"] for r in rows)


def test_member_cannot_request_org_wide_list(grants):
    response = grants["owner_api"].get(LIST, {"scope": "organization"})
    assert response.status_code == 403


def test_member_list_is_self_only_and_revoke_capability_is_explicit(grants):
    rows = grants["owner_api"].get(LIST).json()["result"]
    assert [r["id"] for r in rows] == [grants["consents"][0].pk]
    assert rows[0]["can_revoke"] is True
    assert rows[0]["user"]["id"] == str(grants["owner"].pk)


def test_list_and_revoke_recheck_active_org_membership(grants):
    OrganizationMembership.no_workspace_objects.filter(
        user=grants["owner"], organization=grants["organization"]
    ).update(is_active=False)
    listed = grants["owner_api"].get(LIST)
    revoked = grants["owner_api"].delete(f"{LIST}{grants['consents'][0].pk}/")
    for response in (listed, revoked):
        assert response.status_code == 403
        assert response.json()["result"] == (
            "You must be an active member of the selected organization "
            "to manage MCP client connections."
        )


def test_list_is_selected_org_scoped_even_for_same_person(grants):
    foreign_grant = authorize.record_consent(
        grants["owner"],
        grants["resolved"],
        grants["foreign"],
        h.RESOURCE_BASE,
        ["mcp:tools"],
    )
    rows = grants["owner_api"].get(LIST).json()["result"]
    assert {r["organization"]["id"] for r in rows} == {str(grants["organization"].pk)}
    assert grants["owner_api"].delete(f"{LIST}{foreign_grant.pk}/").status_code == 404
    foreign_grant.refresh_from_db()
    assert foreign_grant.revoked_at is None


def test_admin_may_not_revoke_another_persons_consent(grants):
    assert (
        grants["admin_api"].delete(f"{LIST}{grants['consents'][0].pk}/").status_code
        == 404
    )


def test_invalid_list_scope_is_rejected(grants):
    assert grants["owner_api"].get(LIST, {"scope": "everyone"}).status_code == 400


def test_dcr_browser_handoff_preserves_state_resource_scopes_and_pkce(
    client, organization, settings, gateway_unreachable
):
    settings.AGENTCC_MCP_OAUTH_CONSENT_URL = ""
    settings.FRONTEND_BASE_URL = "http://localhost:3031"
    _, api = h.member(organization)
    verifier, challenge = h.pkce()
    registered = client.post(
        "/agentcc/mcp-oauth/register",
        {
            "client_name": "Synthetic desktop",
            "redirect_uris": [h.REDIRECT],
            "token_endpoint_auth_method": "none",
        },
        content_type="application/json",
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]
    started = client.get(
        "/agentcc/mcp-oauth/authorize",
        h.authorize_params(
            challenge,
            client_id=client_id,
            resource=h.RESOURCE_BASE,
            scope="mcp:tools mcp:resources",
            state="opaque + & state",
        ),
    )
    assert started.status_code == 302
    assert started["Location"].startswith(
        "http://localhost:3031/gateway/mcp/authorize?request="
    )
    query = h.query_of(started["Location"])
    assert set(query) == {"request"}
    path = h.consent_path(query["request"])
    detail = api.get(path).json()["result"]
    assert detail["client"]["name"] == "Synthetic desktop"
    assert detail["resource"] == h.RESOURCE_BASE
    assert detail["redirect_uri"] == h.REDIRECT
    assert [s["name"] for s in detail["scopes"]] == ["mcp:tools", "mcp:resources"]
    # A caller cannot substitute another subject or any authorization parameters.
    assert (
        api.post(
            path, {"decision": "approve", "user_id": "other"}, format="json"
        ).status_code
        == 400
    )
    decided = api.post(
        path,
        {"decision": "approve", "organization_id": str(organization.pk)},
        format="json",
    )
    target = h.query_of(decided.json()["result"]["redirect_to"])
    assert target["state"] == "opaque + & state"
    token = client.post(
        "/agentcc/mcp-oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": target["code"],
            "redirect_uri": h.REDIRECT,
            "resource": h.RESOURCE_BASE,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    assert token.json()["scope"] == "mcp:tools mcp:resources"
    consent = api.get(LIST).json()["result"][0]
    assert api.delete(f"{LIST}{consent['id']}/").status_code == 200
    refused = client.post(
        "/agentcc/mcp-oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": token.json()["refresh_token"],
            "resource": h.RESOURCE_BASE,
        },
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_grant"
    assert gateway_unreachable, "this path asks the gateway, which is not running"
