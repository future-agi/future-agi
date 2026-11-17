"""The MCP authorization server's self-registration paths and revocation
(M24x): one property per test. Every outside host is faked; the client ID
metadata document fetch goes through the platform's SSRF guard with DNS and
the connection pool stubbed."""

import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.utils import timezone

from agentcc.mcp_oauth import cimd, clients
from agentcc.models.mcp_oauth import AgentccMCPOAuthClient
from agentcc.tests import mcp_oauth_helpers as h
from tfc.utils import ssrf_guard
from tfc.utils.ssrf_guard import SsrfResponse

REGISTER = "/agentcc/mcp-oauth/register"
TOKEN = "/agentcc/mcp-oauth/token"
DOC_URL = "https://client.example.test/oauth/client.json"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _as_settings(settings, monkeypatch):
    h.configure(settings)
    from agentcc.services.gateway_admin import mcp_oauth as gateway_keys_module

    monkeypatch.setattr(gateway_keys_module, "push_keys", MagicMock())
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def org_a(organization):
    h.org_with_virtual_server(organization, h.VS_A)
    return organization


def _register(client, body):
    return client.post(REGISTER, json.dumps(body), content_type="application/json")


def _token(client, headers=None, **form):
    return client.post(
        TOKEN,
        urlencode(form),
        content_type="application/x-www-form-urlencoded",
        **(headers or {}),
    )


def _claude_code_like(**overrides):
    """The shape native MCP clients send: no application_type, loopback
    redirect, public client."""
    body = {
        "client_name": "Fake Native Client",
        "redirect_uris": ["http://localhost:33418/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    body.update(overrides)
    return body


# --- Metadata ----------------------------------------------------------------------


@pytest.mark.django_db
def test_metadata_advertises_cimd_and_registration(client):
    body = client.get(
        "/.well-known/oauth-authorization-server/agentcc/mcp-oauth"
    ).json()

    assert body["registration_endpoint"] == f"{h.ISSUER}/register"
    assert body["client_id_metadata_document_supported"] is True
    assert set(body["token_endpoint_auth_methods_supported"]) == {
        "none",
        "client_secret_post",
        "client_secret_basic",
    }


# --- Dynamic client registration ------------------------------------------------------


@pytest.mark.django_db
def test_dcr_registers_the_payload_native_clients_send(client):
    response = _register(client, _claude_code_like())

    assert response.status_code == 201
    body = response.json()
    assert body["client_id"].startswith("mcpc_")
    assert body["application_type"] == "native"
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body
    row = AgentccMCPOAuthClient.objects.get(client_id=body["client_id"])
    assert row.kind == AgentccMCPOAuthClient.Kind.DCR
    assert row.redirect_uris == ["http://localhost:33418/callback"]


@pytest.mark.django_db
def test_dcr_derives_application_type_and_a_web_client_needs_https(client):
    web = _register(
        client, _claude_code_like(redirect_uris=["https://claude.example.test/cb"])
    )
    declared_web_loopback = _register(client, _claude_code_like(application_type="web"))
    unknown_type = _register(client, _claude_code_like(application_type="desktop"))

    assert web.json()["application_type"] == "web"
    assert declared_web_loopback.status_code == 400
    assert declared_web_loopback.json()["error"] == "invalid_redirect_uri"
    assert unknown_type.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    "redirect_uris",
    [
        [],
        ["https://client.example.test/cb#frag"],
        ["http://client.example.test/cb"],
        ["javascript:alert(1)"],
        ["https://user:pw@client.example.test/cb"],
        "https://client.example.test/cb",
    ],
)
def test_dcr_refuses_redirect_uris_that_could_leak_a_code(client, redirect_uris):
    response = _register(client, _claude_code_like(redirect_uris=redirect_uris))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert not AgentccMCPOAuthClient.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"token_endpoint_auth_method": "private_key_jwt"},
        {"grant_types": ["implicit"]},
        {"grant_types": ["refresh_token"]},
        {"response_types": ["token"]},
        {"scope": "mcp:tools admin"},
        {"client_id": "chosen-by-me"},
        {"client_name": "x" * 500},
    ],
)
def test_dcr_refuses_metadata_it_cannot_honour(client, overrides):
    response = _register(client, _claude_code_like(**overrides))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.django_db
def test_dcr_refuses_a_body_that_is_not_json_or_too_large(client):
    form = client.post(REGISTER, {"redirect_uris": "x"})
    large = _register(
        client, _claude_code_like(client_uri="https://x.test/" + "a" * 20000)
    )

    assert form.status_code == 400
    assert large.status_code == 400


def test_dcr_client_still_needs_consent(client, auth_client, org_a):
    registered = _register(client, _claude_code_like()).json()
    _, challenge = h.pkce()

    request_id = h.start(
        client,
        challenge,
        client_id=registered["client_id"],
        redirect_uri="http://localhost:50123/callback",
    )
    shown = auth_client.get(h.consent_path(request_id)).json()["result"]

    assert shown["client"]["verified"] is False
    assert shown["client"]["kind"] == "dcr"
    assert shown["client"]["name"] == "Fake Native Client"
    assert shown["pre_approved"] is False


def test_dcr_public_client_signs_in_with_pkce_alone(client, auth_client, org_a):
    registered = _register(client, _claude_code_like()).json()
    verifier, challenge = h.pkce()
    redirect = "http://localhost:50123/callback"

    code = h.sign_in(
        client,
        auth_client,
        challenge,
        client_id=registered["client_id"],
        redirect_uri=redirect,
    )
    response = _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=redirect,
        client_id=registered["client_id"],
        code_verifier=verifier,
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_dcr_confidential_client_must_present_its_secret(client, auth_client, org_a):
    registered = _register(
        client,
        _claude_code_like(
            redirect_uris=["https://claude.example.test/api/mcp/auth_callback"],
            token_endpoint_auth_method="client_secret_post",
        ),
    ).json()
    secret = registered["client_secret"]
    row = AgentccMCPOAuthClient.objects.get(client_id=registered["client_id"])
    verifier, challenge = h.pkce()
    redirect = "https://claude.example.test/api/mcp/auth_callback"
    code = h.sign_in(
        client,
        auth_client,
        challenge,
        client_id=registered["client_id"],
        redirect_uri=redirect,
    )
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": registered["client_id"],
        "code_verifier": verifier,
    }

    without = _token(client, **form)
    wrong = _token(client, **form, client_secret="not-the-secret")
    right = _token(client, **form, client_secret=secret)

    assert registered["client_secret_expires_at"] == 0
    assert secret not in json.dumps(row.metadata)
    assert row.metadata["client_secret_hash"] == clients.secret_hash(secret)
    assert without.status_code == 401
    assert wrong.status_code == 401
    assert without.json()["error"] == "invalid_client"
    assert right.status_code == 200


def test_dcr_basic_client_authenticates_with_http_basic(client, auth_client, org_a):
    body = _claude_code_like(redirect_uris=["https://claude.example.test/cb"])
    del body["token_endpoint_auth_method"]  # RFC 7591's default applies

    registered = _register(client, body).json()
    assert registered["token_endpoint_auth_method"] == "client_secret_basic"
    verifier, challenge = h.pkce()
    code = h.sign_in(
        client,
        auth_client,
        challenge,
        client_id=registered["client_id"],
        redirect_uri="https://claude.example.test/cb",
    )
    basic = base64.b64encode(
        f"{registered['client_id']}:{registered['client_secret']}".encode()
    ).decode()

    response = _token(
        client,
        headers={"HTTP_AUTHORIZATION": f"Basic {basic}"},
        grant_type="authorization_code",
        code=code,
        redirect_uri="https://claude.example.test/cb",
        code_verifier=verifier,
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_unused_registrations_are_removed_after_a_day(client):
    old = _register(client, _claude_code_like()).json()["client_id"]
    AgentccMCPOAuthClient.objects.filter(client_id=old).update(
        created_at=timezone.now() - timedelta(days=2)
    )

    fresh = _register(client, _claude_code_like()).json()["client_id"]

    assert not AgentccMCPOAuthClient.objects.filter(client_id=old).exists()
    assert AgentccMCPOAuthClient.objects.filter(client_id=fresh).exists()


# --- Client ID metadata documents -------------------------------------------------------


def _doc(**overrides):
    doc = {
        "client_id": DOC_URL,
        "client_name": "Fake Document Client",
        "redirect_uris": ["http://127.0.0.1/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def document(monkeypatch):
    """The document host, faked at the guard's fetch: ``document.doc`` is what
    it serves, ``document.calls`` what was asked."""
    state = MagicMock()
    state.doc = _doc()
    state.final_url = DOC_URL
    state.status = 200

    def fake_safe_fetch(url, **kwargs):
        state.calls.append((url, kwargs))
        return SsrfResponse(
            state.status, {}, json.dumps(state.doc).encode(), state.final_url
        )

    state.calls = []
    monkeypatch.setattr(cimd, "safe_fetch", fake_safe_fetch)
    return state


@pytest.mark.django_db
def test_cimd_client_fetch_uses_safe_fetcher(client, document):
    _, challenge = h.pkce()

    response = client.get(
        "/agentcc/mcp-oauth/authorize",
        h.authorize_params(
            challenge,
            client_id=DOC_URL,
            redirect_uri="http://127.0.0.1:61000/callback",
        ),
    )

    assert response.status_code == 302
    assert response["Location"].startswith(h.CONSENT_URL)
    assert len(document.calls) == 1
    url, kwargs = document.calls[0]
    assert url == DOC_URL
    assert kwargs["method"] == "GET"
    assert kwargs["max_bytes"] == 5 * 1024
    assert kwargs["timeout"] <= 5
    row = AgentccMCPOAuthClient.objects.get(client_id=DOC_URL)
    assert row.kind == AgentccMCPOAuthClient.Kind.CIMD
    assert row.last_fetched_at is not None


class _FakePool:
    """A pinned connection pool that answers one canned response."""

    opened = []

    def __init__(self, status, headers):
        self._status = status
        self._headers = headers

    def request(self, method, path, **kwargs):
        response = MagicMock()
        response.status = self._status
        response.headers = self._headers
        response.stream.return_value = iter([b"{}"])
        return response

    def close(self):
        pass


@pytest.fixture
def guarded_network(monkeypatch):
    """DNS and the pinned pool of tfc.utils.ssrf_guard, stubbed: the real guard
    runs; ``answers`` maps a host to its addresses, ``pools`` records each
    pool the guard opened as (pinned ip, host)."""
    answers = {}
    pools = []

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(2, 1, 6, "", (ip, 0)) for ip in answers[host]]

    def fake_pool(scheme, pinned_ip, host, port, timeout):
        pools.append((pinned_ip, host))
        return _FakePool(302, {"Location": "https://internal.example.test/doc.json"})

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(ssrf_guard, "_open_pinned_pool", fake_pool)
    return answers, pools


@pytest.mark.django_db
def test_cimd_redirect_to_private_refused(guarded_network):
    answers, pools = guarded_network
    answers["client.example.test"] = [PUBLIC_IP]
    answers["internal.example.test"] = ["10.0.0.5"]

    resolved = clients.resolve(DOC_URL)

    assert resolved is None
    assert pools == [(PUBLIC_IP, "client.example.test")]
    assert not AgentccMCPOAuthClient.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("address", ["169.254.169.254", "127.0.0.1", "10.1.2.3"])
def test_cimd_document_on_an_internal_address_is_never_fetched(
    guarded_network, address
):
    answers, pools = guarded_network
    answers["client.example.test"] = [address]

    assert clients.resolve(DOC_URL) is None
    assert pools == []


@pytest.mark.django_db
def test_cimd_any_redirect_refused(document):
    document.final_url = "https://elsewhere.example.test/client.json"

    assert clients.resolve(DOC_URL) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "doc_overrides",
    [
        {"client_id": "https://client.example.test/other.json"},
        {"token_endpoint_auth_method": "client_secret_basic"},
        {"client_secret": "shared"},
        {"redirect_uris": ["http://client.example.test/cb"]},
        {"response_types": ["token"]},
    ],
)
def test_cimd_document_must_name_itself_and_use_no_secret(document, doc_overrides):
    document.doc = _doc(**doc_overrides)

    assert clients.resolve(DOC_URL) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "client_id",
    [
        "http://client.example.test/client.json",
        "https://client.example.test/",
        "https://client.example.test/client.json?x=1",
    ],
)
def test_only_an_https_url_with_a_path_names_a_document(document, client_id):
    assert clients.resolve(client_id) is None
    assert document.calls == []


@pytest.mark.django_db
def test_cimd_cache_and_domain_allowlist(settings, document):
    first = clients.resolve(DOC_URL)
    second = clients.resolve(DOC_URL)
    AgentccMCPOAuthClient.objects.filter(client_id=DOC_URL).update(
        last_fetched_at=timezone.now() - timedelta(hours=2)
    )
    third = clients.resolve(DOC_URL)
    settings.AGENTCC_MCP_OAUTH_CIMD_ALLOWED_HOSTS = "trusted.example.test"
    outside = clients.resolve(DOC_URL)

    assert first is not None and second is not None and third is not None
    assert len(document.calls) == 2
    assert outside is None
    assert len(document.calls) == 2


@pytest.mark.django_db
def test_cimd_stale_copy_serves_for_a_day_when_the_host_fails(document):
    assert clients.resolve(DOC_URL) is not None
    document.status = 503
    AgentccMCPOAuthClient.objects.filter(client_id=DOC_URL).update(
        last_fetched_at=timezone.now() - timedelta(hours=3)
    )
    within_a_day = clients.resolve(DOC_URL)
    AgentccMCPOAuthClient.objects.filter(client_id=DOC_URL).update(
        last_fetched_at=timezone.now() - timedelta(days=2)
    )
    after_a_day = clients.resolve(DOC_URL)

    assert within_a_day is not None
    assert after_a_day is None


def test_cimd_client_still_needs_consent(client, auth_client, org_a, document):
    _, challenge = h.pkce()

    request_id = h.start(
        client,
        challenge,
        client_id=DOC_URL,
        redirect_uri="http://127.0.0.1:61000/callback",
    )
    shown = auth_client.get(h.consent_path(request_id)).json()["result"]

    assert shown["client"]["verified"] is False
    assert shown["client"]["kind"] == "cimd"
    assert shown["redirect_host"] == "127.0.0.1"


# --- Revocation ------------------------------------------------------------------------

REVOKE = "/agentcc/mcp-oauth/revoke"


def _revoke(client, **form):
    return client.post(
        REVOKE, urlencode(form), content_type="application/x-www-form-urlencoded"
    )


@pytest.fixture
def signed_in(client, auth_client, org_a):
    h.preregistered()
    verifier, challenge = h.pkce()
    code = h.sign_in(client, auth_client, challenge)
    response = _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=h.REDIRECT,
        client_id=h.CLIENT_ID,
        code_verifier=verifier,
    )
    assert response.status_code == 200, response.content
    return response.json()


def _refresh(client, refresh_token, client_id=h.CLIENT_ID):
    return _token(
        client,
        grant_type="refresh_token",
        refresh_token=refresh_token,
        client_id=client_id,
    )


def test_metadata_advertises_revocation(client, db):
    body = client.get(
        "/.well-known/oauth-authorization-server/agentcc/mcp-oauth"
    ).json()

    assert body["revocation_endpoint"] == f"{h.ISSUER}/revoke"


def test_revoke_refresh_chain(client, signed_in):
    rotated = _refresh(client, signed_in["refresh_token"]).json()

    revoked = _revoke(client, token=rotated["refresh_token"], client_id=h.CLIENT_ID)
    after = _refresh(client, rotated["refresh_token"])

    assert revoked.status_code == 200
    assert after.status_code == 400
    assert after.json()["error"] == "invalid_grant"


def test_revoking_an_older_token_of_the_chain_revokes_the_live_one(client, signed_in):
    rotated = _refresh(client, signed_in["refresh_token"]).json()

    _revoke(client, token=signed_in["refresh_token"], client_id=h.CLIENT_ID)

    assert _refresh(client, rotated["refresh_token"]).status_code == 400


def test_another_client_cannot_revoke_a_token(client, signed_in):
    h.preregistered(client_id="fake-other-client")

    answer = _revoke(
        client, token=signed_in["refresh_token"], client_id="fake-other-client"
    )
    still = _refresh(client, signed_in["refresh_token"])

    assert answer.status_code == 200
    assert still.status_code == 200


@pytest.mark.parametrize("token", ["not-a-token", "access"])
def test_revoke_answers_200_for_tokens_it_does_not_revoke(client, signed_in, token):
    value = signed_in["access_token"] if token == "access" else token

    response = _revoke(client, token=value, client_id=h.CLIENT_ID)

    assert response.status_code == 200
    assert _refresh(client, signed_in["refresh_token"]).status_code == 200


def test_revoke_requires_client_authentication(client, signed_in):
    unknown = _revoke(client, token=signed_in["refresh_token"], client_id="nobody")
    missing = _revoke(client, client_id=h.CLIENT_ID)

    assert unknown.status_code == 401
    assert unknown.json()["error"] == "invalid_client"
    assert missing.status_code == 400
    assert _refresh(client, signed_in["refresh_token"]).status_code == 200


def test_revoke_consent_stops_refresh(client, auth_client, signed_in):
    listed = auth_client.get("/agentcc/mcp-oauth/consents/").json()["result"]
    consent_id = listed[0]["id"]

    revoked = auth_client.delete(f"/agentcc/mcp-oauth/consents/{consent_id}/")
    after = _refresh(client, signed_in["refresh_token"])
    relisted = auth_client.get("/agentcc/mcp-oauth/consents/").json()["result"]

    assert listed[0]["client_id"] == h.CLIENT_ID
    assert listed[0]["scopes"] == ["mcp:tools"]
    assert listed[0]["organization"]["name"]
    assert revoked.status_code == 200
    assert after.status_code == 400
    assert after.json()["error"] == "invalid_grant"
    assert relisted == []


def test_person_sees_and_revokes_only_their_own_consents(
    client, auth_client, org_a, signed_in
):
    consent_id = auth_client.get("/agentcc/mcp-oauth/consents/").json()["result"][0][
        "id"
    ]
    _, other_client = h.member(org_a, name="Other Member")

    seen = other_client.get("/agentcc/mcp-oauth/consents/").json()["result"]
    refused = other_client.delete(f"/agentcc/mcp-oauth/consents/{consent_id}/")

    assert seen == []
    assert refused.status_code == 404
    assert _refresh(client, signed_in["refresh_token"]).status_code == 200
