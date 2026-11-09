"""The MCP authorization server (M24): one property per test.

Every test names the guarantee it holds; the red run is the same test on the
commit before the piece that makes it pass.
"""

import hashlib
import json
import runpy
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlencode

import jwt
import pytest
from django.core.cache import cache
from django.utils import timezone
from jsonschema import Draft202012Validator

from accounts.models.organization_membership import OrganizationMembership
from agentcc.mcp_oauth import keys
from agentcc.models.mcp_oauth import (
    AgentccMCPOAuthConsent,
    AgentccMCPOAuthRefreshToken,
    AgentccMCPOAuthSigningKey,
)
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests import mcp_oauth_helpers as h
from agentcc.tests.mcp_oauth_helpers import ISSUER, configure
from tfc import asgi_startup
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture(autouse=True)
def _as_settings(settings):
    configure(settings)
    cache.clear()
    yield
    cache.clear()


# --- Discovery ---------------------------------------------------------------

METADATA_PATHS = [
    "/.well-known/oauth-authorization-server/agentcc/mcp-oauth",
    "/.well-known/openid-configuration/agentcc/mcp-oauth",
    "/agentcc/mcp-oauth/.well-known/openid-configuration",
]


@pytest.mark.django_db
@pytest.mark.parametrize("path", METADATA_PATHS)
def test_metadata_path_inserted_s256_only_iss_supported(client, path):
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["issuer"] == ISSUER
    assert body["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert body["token_endpoint"] == f"{ISSUER}/token"
    assert body["jwks_uri"] == f"{ISSUER}/jwks.json"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["authorization_response_iss_parameter_supported"] is True
    assert body["response_types_supported"] == ["code"]
    assert set(body["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert body["scopes_supported"] == ["mcp:tools", "mcp:resources", "mcp:prompts"]


@pytest.fixture
def asgi_module(monkeypatch):
    """tfc/asgi.py with telemetry, the Django app and the URL warm-up stubbed,
    so only its router runs (the pattern of test_asgi_embedded_worker.py)."""
    calls = {"django": [], "platform_oauth": []}

    async def django_app(scope, receive, send):
        calls["django"].append(scope["path"])

    async def platform_oauth_app(scope, receive, send):
        calls["platform_oauth"].append(scope["path"])

    monkeypatch.setattr("tfc.telemetry.init_telemetry", lambda *, component: None)
    monkeypatch.setattr("django.core.asgi.get_asgi_application", lambda: django_app)
    monkeypatch.setattr(asgi_startup, "warm_http_urlconf", lambda: 0)
    module = runpy.run_path(str(Path(__file__).parents[2] / "tfc" / "asgi.py"))
    namespace = module["http_router"].__globals__
    namespace["_get_oauth_app"] = lambda: platform_oauth_app
    namespace["_get_mcp_starlette_app"] = lambda: None
    return namespace, calls


async def _route(namespace, path):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await namespace["http_router"](
        {"type": "http", "path": path, "method": "GET"}, receive, send
    )


async def test_served_app_hands_gateway_metadata_to_django_and_keeps_platform_paths(
    asgi_module,
):
    namespace, calls = asgi_module

    for path in METADATA_PATHS[:2]:
        await _route(namespace, path)
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/openid-configuration",
    ):
        await _route(namespace, path)

    assert calls["django"] == METADATA_PATHS[:2]
    assert calls["platform_oauth"] == [
        "/.well-known/oauth-authorization-server",
        # The platform app's own suffix stripping is unchanged.
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/openid-configuration",
    ]


# --- Authorization request -----------------------------------------------------

AUTHORIZE = "/agentcc/mcp-oauth/authorize"


@pytest.fixture
def org_a(organization):
    h.org_with_virtual_server(organization, h.VS_A)
    return organization


@pytest.fixture
def client_row(db):
    return h.preregistered()


@pytest.mark.django_db
def test_unknown_client_rejected_without_resolvers(client, client_row):
    _, challenge = h.pkce()

    response = client.get(
        AUTHORIZE, h.authorize_params(challenge, client_id="nobody-registered-this")
    )

    assert response.status_code == 400
    assert "Location" not in response
    assert b"invalid_client" in response.content


@pytest.mark.django_db
def test_redirect_exact_match_and_iss(client, client_row):
    _, challenge = h.pkce()

    unregistered = client.get(
        AUTHORIZE,
        h.authorize_params(challenge, redirect_uri=h.REDIRECT + "/elsewhere"),
    )
    error = client.get(AUTHORIZE, h.authorize_params(challenge, response_type="token"))

    assert unregistered.status_code == 400
    assert "Location" not in unregistered
    assert error.status_code == 302
    assert error["Location"].startswith(h.REDIRECT + "?")
    query = h.query_of(error["Location"])
    assert query["error"] == "unsupported_response_type"
    assert query["state"] == "fake-state-123"
    assert query["iss"] == ISSUER


@pytest.mark.django_db
def test_loopback_redirect_may_use_any_port_but_nothing_else_varies(client):
    h.preregistered(redirect_uris=["http://127.0.0.1/callback"])
    _, challenge = h.pkce()

    other_port = client.get(
        AUTHORIZE,
        h.authorize_params(challenge, redirect_uri="http://127.0.0.1:53111/callback"),
    )
    other_path = client.get(
        AUTHORIZE,
        h.authorize_params(challenge, redirect_uri="http://127.0.0.1:53111/other"),
    )
    other_host = client.get(
        AUTHORIZE,
        h.authorize_params(challenge, redirect_uri="http://10.0.0.5:53111/callback"),
    )

    assert other_port.status_code == 302
    assert other_port["Location"].startswith(h.CONSENT_URL)
    assert other_path.status_code == 400
    assert other_host.status_code == 400


@pytest.mark.django_db
def test_app_scheme_redirect_registered_exactly_is_used(client):
    app_redirect = "cursor://anysphere.cursor-mcp/oauth/callback"
    h.preregistered(redirect_uris=[app_redirect])
    _, challenge = h.pkce()

    response = client.get(
        AUTHORIZE,
        h.authorize_params(
            challenge, redirect_uri=app_redirect, code_challenge_method="plain"
        ),
    )

    assert response.status_code == 302
    assert response["Location"].startswith(app_redirect + "?")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"resource": None}, "invalid_target"),
        ({"resource": "https://elsewhere.example.test/mcp"}, "invalid_target"),
        ({"resource": f"{h.RESOURCE_BASE}/v/{h.VS_A}/"}, "invalid_target"),
        ({"resource": f"{h.RESOURCE_BASE}/v/Not_A_Slug"}, "invalid_target"),
        ({"resource": f"{h.RESOURCE_BASE}/v/crm/extra"}, "invalid_target"),
        ({"scope": "mcp:tools admin"}, "invalid_scope"),
    ],
)
def test_authorize_requires_pkce_s256_and_a_gateway_resource(
    client, client_row, overrides, error
):
    _, challenge = h.pkce()

    response = client.get(AUTHORIZE, h.authorize_params(challenge, **overrides))

    assert response.status_code == 302
    assert h.query_of(response["Location"])["error"] == error
    assert not response["Location"].startswith(h.CONSENT_URL)


def test_plain_pkce_rejected(client, client_row):
    _, challenge = h.pkce()

    response = client.get(
        AUTHORIZE, h.authorize_params(challenge, code_challenge_method="plain")
    )

    assert h.query_of(response["Location"])["error"] == "invalid_request"


@pytest.mark.django_db
def test_authorize_answer_cannot_be_framed(client, client_row):
    _, challenge = h.pkce()

    ok = client.get(AUTHORIZE, h.authorize_params(challenge))
    refused = client.get(AUTHORIZE, h.authorize_params(challenge, client_id="nobody"))

    for response in (ok, refused):
        assert response["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
        assert response["Cache-Control"] == "no-store"


# --- Consent ---------------------------------------------------------------------


def test_consent_requires_console_sign_in(client, api_client, client_row, org_a):
    _, challenge = h.pkce()
    request_id = h.start(client, challenge)

    read = api_client.get(h.consent_path(request_id))
    decide = api_client.post(
        h.consent_path(request_id), {"decision": "approve"}, format="json"
    )

    assert read.status_code in (401, 403)
    assert decide.status_code in (401, 403)


def test_consent_shows_the_org_the_client_and_the_scopes(
    client, auth_client, client_row, org_a
):
    _, challenge = h.pkce()
    request_id = h.start(client, challenge, scope="mcp:tools mcp:prompts")

    response = auth_client.get(h.consent_path(request_id))

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["organizations"] == [{"id": str(org_a.id), "name": org_a.name}]
    assert [s["name"] for s in result["scopes"]] == ["mcp:tools", "mcp:prompts"]
    assert all(s["description"] for s in result["scopes"])
    assert result["client"]["name"] == "Fake Client"
    assert result["client"]["verified"] is True
    assert result["redirect_host"] == "client.example.test"
    assert result["resource"] == f"{h.RESOURCE_BASE}/v/{h.VS_A}"
    assert result["virtual_server"] == h.VS_A
    assert result["pre_approved"] is False


def test_resource_must_be_users_org_virtual_server(
    client, auth_client, client_row, org_a
):
    other = h.new_org("Org B")
    h.org_with_virtual_server(other, h.VS_B)
    _, challenge = h.pkce()
    request_id = h.start(client, challenge, resource=f"{h.RESOURCE_BASE}/v/{h.VS_B}")

    read = auth_client.get(h.consent_path(request_id))
    decide = auth_client.post(
        h.consent_path(request_id), {"decision": "approve"}, format="json"
    )

    assert read.status_code == 403
    assert decide.status_code == 403
    assert "code=" not in str(decide.content)


def test_virtual_server_addressed_by_slug_is_granted_in_the_org_that_holds_it(
    client, auth_client, client_row, org_a
):
    _, challenge = h.pkce()
    by_slug = f"{h.RESOURCE_BASE}/v/crm"
    request_id = h.start(client, challenge, resource=by_slug)

    shown = auth_client.get(h.consent_path(request_id)).json()["result"]
    redirect_to = h.approve(auth_client, request_id)

    assert shown["organizations"] == [{"id": str(org_a.id), "name": org_a.name}]
    assert shown["virtual_server"] == "crm"
    assert shown["resource"] == by_slug
    assert "code" in h.query_of(redirect_to)


def test_slug_held_only_by_another_org_is_not_granted(
    client, auth_client, client_row, org_a
):
    other = h.new_org("Org Billing")
    h.org_with_virtual_server(other, h.VS_B, slug="billing")
    _, challenge = h.pkce()
    request_id = h.start(client, challenge, resource=f"{h.RESOURCE_BASE}/v/billing")

    read = auth_client.get(h.consent_path(request_id))
    decide = auth_client.post(
        h.consent_path(request_id), {"decision": "approve"}, format="json"
    )

    assert read.status_code == 403
    assert decide.status_code == 403


def test_mcp_endpoint_offers_every_org_of_the_person_and_needs_a_choice(
    client, user, auth_client, client_row, org_a
):
    second = h.new_org("Org Second")
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=second,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    _, challenge = h.pkce()
    request_id = h.start(client, challenge, resource=h.RESOURCE_BASE)

    offered = auth_client.get(h.consent_path(request_id)).json()["result"]
    unchosen = auth_client.post(
        h.consent_path(request_id), {"decision": "approve"}, format="json"
    )
    chosen = h.approve(auth_client, request_id, organization_id=second.id)

    assert {o["id"] for o in offered["organizations"]} == {
        str(org_a.id),
        str(second.id),
    }
    assert unchosen.status_code == 400
    assert "code" in h.query_of(chosen)


def test_pending_request_belongs_to_the_first_person_who_opens_it(
    client, auth_client, client_row, org_a
):
    _, challenge = h.pkce()
    request_id = h.start(client, challenge)
    _, other_client = h.member(org_a, name="Other Member")

    assert auth_client.get(h.consent_path(request_id)).status_code == 200
    assert other_client.get(h.consent_path(request_id)).status_code == 404
    assert (
        other_client.post(
            h.consent_path(request_id), {"decision": "approve"}, format="json"
        ).status_code
        == 404
    )


def test_state_single_use_after_consent(client, auth_client, client_row, org_a):
    _, challenge = h.pkce()
    request_id = h.start(client, challenge)

    redirect_to = h.approve(auth_client, request_id)
    again = auth_client.post(
        h.consent_path(request_id), {"decision": "approve"}, format="json"
    )

    query = h.query_of(redirect_to)
    assert redirect_to.startswith(h.REDIRECT + "?")
    assert query["state"] == "fake-state-123"
    assert query["iss"] == ISSUER
    assert len(query["code"]) >= 32
    assert again.status_code == 404


def test_consent_recorded_for_person_client_org_resource_and_scopes(
    client, user, auth_client, client_row, org_a
):
    _, challenge = h.pkce()
    h.sign_in(client, auth_client, challenge, scope="mcp:tools mcp:resources")
    h.sign_in(client, auth_client, challenge, scope="mcp:tools")

    rows = list(AgentccMCPOAuthConsent.objects.filter(user=user))
    assert len(rows) == 1
    assert rows[0].client_id == client_row.id
    assert rows[0].organization_id == org_a.id
    assert rows[0].resource == f"{h.RESOURCE_BASE}/v/{h.VS_A}"
    assert rows[0].scopes == ["mcp:tools"]
    assert rows[0].revoked_at is None


def test_declining_sends_access_denied_and_records_nothing(
    client, auth_client, client_row, org_a
):
    _, challenge = h.pkce()
    request_id = h.start(client, challenge)

    response = auth_client.post(
        h.consent_path(request_id), {"decision": "deny"}, format="json"
    )

    query = h.query_of(response.json()["result"]["redirect_to"])
    assert query["error"] == "access_denied"
    assert query["iss"] == ISSUER
    assert "code" not in query
    assert not AgentccMCPOAuthConsent.objects.exists()


def test_first_party_client_pre_approved_only_on_its_web_callback(
    settings, client, auth_client, org_a
):
    web = "https://agents.example.test/integrations/oauth/callback"
    loopback = "http://127.0.0.1/oauth/callback"
    settings.AGENTCC_MCP_OAUTH_EE_REDIRECT_URIS = f"{web},{loopback}"
    _, challenge = h.pkce()

    on_web = h.start(client, challenge, client_id="futureagi-ee", redirect_uri=web)
    on_loopback = h.start(
        client,
        challenge,
        client_id="futureagi-ee",
        redirect_uri="http://127.0.0.1:41000/oauth/callback",
    )

    web_view = auth_client.get(h.consent_path(on_web)).json()["result"]
    loopback_view = auth_client.get(h.consent_path(on_loopback)).json()["result"]
    assert web_view["client"]["first_party"] is True
    assert web_view["client"]["name"] == "Future AGI Agents"
    assert web_view["pre_approved"] is True
    assert loopback_view["pre_approved"] is False


# --- Tokens and keys ---------------------------------------------------------------

TOKEN = "/agentcc/mcp-oauth/token"
CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "api_contracts" / "gateway"


@pytest.fixture(autouse=True)
def gateway_keys(monkeypatch):
    """The gateway's key endpoint, faked: nothing leaves the test."""
    from agentcc.services.gateway_admin import mcp_oauth as gateway_keys_module

    fake = MagicMock()
    monkeypatch.setattr(gateway_keys_module, "push_keys", fake)
    return fake


def _token(client, **form):
    return client.post(
        TOKEN, urlencode(form), content_type="application/x-www-form-urlencoded"
    )


def _exchange(client, code, verifier, **extra):
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": h.REDIRECT,
        "client_id": h.CLIENT_ID,
        "code_verifier": verifier,
    }
    form.update(extra)
    return _token(client, **{k: v for k, v in form.items() if v is not None})


def _refresh(client, refresh_token, **extra):
    return _token(
        client,
        grant_type="refresh_token",
        refresh_token=refresh_token,
        client_id=h.CLIENT_ID,
        **extra,
    )


def _decode(client, access_token, audience):
    jwks = client.get("/agentcc/mcp-oauth/jwks.json").json()
    kid = jwt.get_unverified_header(access_token)["kid"]
    jwk = next(k for k in jwks["keys"] if k["kid"] == kid)
    return jwt.decode(
        access_token,
        jwt.PyJWK(jwk).key,
        algorithms=["ES256"],
        audience=audience,
        issuer=ISSUER,
    )


@pytest.fixture
def signed_in(client, auth_client, client_row, org_a):
    """A finished sign-in: the token response of a first code exchange."""
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)
    response = _exchange(client, code, verifier)
    assert response.status_code == 200, response.content
    return response.json()


def test_token_claims_match_golden(client, user, org_a, signed_in):
    access = signed_in["access_token"]
    resource = f"{h.RESOURCE_BASE}/v/{h.VS_A}"
    claims = _decode(client, access, resource)
    header = jwt.get_unverified_header(access)
    schema = json.loads(
        (
            CONTRACTS_DIR / "examples" / "mcp-access-token-claims-v1.schema.json"
        ).read_text()
    )

    Draft202012Validator(
        {"$ref": "#/$defs/AccessClaims", "$defs": schema["$defs"]}
    ).validate(claims)
    Draft202012Validator({"$ref": "#/$defs/Header", "$defs": schema["$defs"]}).validate(
        {k: header[k] for k in ("alg", "kid", "typ") if k in header}
    )
    assert header["alg"] == "ES256"
    assert claims["aud"] == resource
    assert claims["sub"] == str(user.pk)
    assert claims["org_id"] == str(org_a.id)
    assert claims["client_id"] == h.CLIENT_ID
    assert claims["scope"] == "mcp:tools"
    assert signed_in["token_type"] == "Bearer"
    assert signed_in["scope"] == "mcp:tools"


def test_access_token_lifetime_15_min(client, signed_in):
    claims = jwt.decode(signed_in["access_token"], options={"verify_signature": False})

    assert claims["exp"] - claims["iat"] == 900
    assert claims["nbf"] == claims["iat"]
    assert signed_in["expires_in"] == 900


def test_token_answers_are_never_cached(client, auth_client, client_row, org_a):
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)

    ok = _exchange(client, code, verifier)
    refused = _exchange(client, "not-a-code", verifier)

    for response in (ok, refused):
        assert response["Cache-Control"] == "no-store"
        assert response["Pragma"] == "no-cache"


@pytest.mark.parametrize(
    "extra",
    [
        {"code_verifier": "x" * 64},
        {"redirect_uri": h.REDIRECT + "/other"},
        {"redirect_uri": None},
        {"resource": f"{h.RESOURCE_BASE}/v/{h.VS_B}"},
    ],
)
def test_code_exchange_needs_its_verifier_redirect_and_resource(
    client, auth_client, client_row, org_a, extra
):
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)

    response = _exchange(client, code, verifier, **extra)

    assert response.status_code == 400
    assert response.json()["error"] in ("invalid_grant", "invalid_target")
    assert "access_token" not in response.json()


def test_code_works_only_for_the_client_it_was_issued_to(
    client, auth_client, client_row, org_a
):
    h.preregistered(client_id="fake-other-client", redirect_uris=[h.REDIRECT])
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)

    response = _exchange(client, code, verifier, client_id="fake-other-client")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_unknown_client_cannot_use_the_token_endpoint(client, db):
    response = _token(
        client,
        grant_type="refresh_token",
        refresh_token="anything",
        client_id="nobody-registered-this",
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_code_is_single_use_and_reuse_revokes_what_it_issued(
    client, auth_client, client_row, org_a
):
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)

    first = _exchange(client, code, verifier)
    second = _exchange(client, code, verifier)
    after = _refresh(client, first.json()["refresh_token"])

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
    assert after.status_code == 400
    assert after.json()["error"] == "invalid_grant"


def test_refresh_tokens_are_stored_only_as_hashes(signed_in):
    raw = signed_in["refresh_token"]
    rows = list(AgentccMCPOAuthRefreshToken.objects.all())

    assert len(rows) == 1
    assert rows[0].token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in str(
        AgentccMCPOAuthRefreshToken.objects.values_list("token_hash", "resource")
    )


def test_refresh_rotation_reuse_revokes_chain(client, signed_in):
    first_refresh = signed_in["refresh_token"]

    rotated = _refresh(client, first_refresh)
    replayed = _refresh(client, first_refresh)
    successor = _refresh(client, rotated.json()["refresh_token"])

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != first_refresh
    assert rotated.json()["access_token"]
    assert replayed.status_code == 400
    assert replayed.json()["error"] == "invalid_grant"
    assert successor.status_code == 400
    assert successor.json()["error"] == "invalid_grant"


def test_refresh_rechecks_org_membership(client, user, org_a, signed_in):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=org_a
    ).update(is_active=False)

    response = _refresh(client, signed_in["refresh_token"])

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert not AgentccMCPOAuthRefreshToken.objects.filter(
        revoked_at__isnull=True
    ).exists()


def test_refresh_rechecks_consent(client, signed_in):
    AgentccMCPOAuthConsent.objects.update(revoked_at=timezone.now())

    response = _refresh(client, signed_in["refresh_token"])

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_refresh_stops_when_the_virtual_server_leaves_the_org(client, org_a, signed_in):
    h.org_with_virtual_server(org_a, h.VS_B, slug="other")

    response = _refresh(client, signed_in["refresh_token"])

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_refresh_cannot_widen_the_consented_scopes(client, signed_in):
    response = _refresh(
        client, signed_in["refresh_token"], scope="mcp:tools mcp:prompts"
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


def test_signing_key_private_half_stored_encrypted(signed_in):
    row = AgentccMCPOAuthSigningKey.objects.get()

    assert row.encrypted_private.startswith("enc::")
    assert "PRIVATE KEY" not in row.encrypted_private
    assert not set(row.public_jwk) & {"d", "p", "q", "k"}


def test_key_rotation_pushes_keys(db, gateway_keys):
    first = keys.ensure_active_key()
    gateway_keys.reset_mock()

    second = keys.rotate()

    gateway_keys.assert_called_once()
    payload = gateway_keys.call_args.args[0]
    schema = json.loads((CONTRACTS_DIR / "agentcc-admin.schema.json").read_text())
    Draft202012Validator(
        {"$ref": "#/$defs/MCPOAuthKeysPush", "$defs": schema["$defs"]}
    ).validate(payload)
    assert payload["issuer"] == ISSUER
    assert payload["version"] == second.pk
    assert {k["kid"] for k in payload["keys"]} == {first.kid, second.kid}
    for key in payload["keys"]:
        assert not set(key) & {"d", "p", "q", "k"}


def test_jwks_publishes_the_active_and_previous_keys_only(client, db):
    oldest = keys.ensure_active_key()
    previous = keys.rotate()
    active = keys.rotate()

    body = client.get("/agentcc/mcp-oauth/jwks.json").json()

    assert [k["kid"] for k in body["keys"]] == [active.kid, previous.kid]
    assert oldest.kid not in {k["kid"] for k in body["keys"]}
    assert all(k["alg"] == "ES256" and k["kty"] == "EC" for k in body["keys"])
    assert not any(set(k) & {"d", "p", "q", "k"} for k in body["keys"])


def test_a_token_signed_before_rotation_still_verifies(client, signed_in):
    keys.rotate()

    claims = _decode(client, signed_in["access_token"], f"{h.RESOURCE_BASE}/v/{h.VS_A}")

    assert claims["sub"]


def test_sign_in_survives_a_gateway_that_refuses_the_key_push(
    client, auth_client, client_row, org_a, gateway_keys
):
    gateway_keys.side_effect = GatewayClientError("PUT returned 404", status_code=404)
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)

    response = _exchange(client, code, verifier)

    assert response.status_code == 200
    assert response.json()["access_token"]
