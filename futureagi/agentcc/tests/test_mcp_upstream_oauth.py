"""Per-user upstream OAuth connect (M25x): a member connects their own account
on a vendor MCP server, and nobody else can complete it for them.

Each test is one property. The vendor is ``FakeVendor``, answered in place of
the SSRF-guarded fetcher (the fetcher's own properties are in
test_mcp_safe_fetch.py), and the gateway's cache purge is recorded, so no test
reaches a network.
"""

import base64
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.utils import timezone
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import (
    AgentccMCPConnectState,
    AgentccMCPUpstreamClient,
)
from agentcc.services import gateway_client, mcp_safe_fetch
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CLIENT_METADATA,
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

Status = AgentccMCPCredential.Status


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


@pytest.fixture
def purges():
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    with patch.object(connect.gateway_credentials, "purge", purge):
        yield calls


@pytest.fixture
def config(organization, user):
    return seed_config(organization)


def _start(client, server_id=SERVER_ID, **body):
    return client.post(START.format(server_id), body, format="json")


def _started(client, **body):
    response = _start(client, **body)
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    return url, name, response.cookies[name]


def _callback(browser, **params):
    return browser.get(CALLBACK_PATH, params)


def _connect(client, vendor, browser=None, *, iss=ISSUER):
    """A whole connect: the start, the vendor's sign-in, the callback in the
    browser that started it."""
    url, name, cookie = _started(client)
    browser = browser or Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)
    params = {"code": code, "state": state}
    if iss is not None:
        params["iss"] = iss
    return _callback(browser, **params)


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _user_rows(org, user):
    return AgentccMCPCredential.objects.filter(
        organization=org, server_id=SERVER_ID, scope="user", user=user
    )


# --- Property: the start needs the member's own login --------------------------


def test_start_requires_login_and_bound_user(
    api_client, auth_client, organization, user, workspace, config, vendor
):
    assert _start(api_client).status_code in (401, 403)

    other, _ = member(organization, workspace)
    bound_to_other = connect.seal_binding(
        str(organization.pk), SERVER_ID, str(other.pk)
    )
    refused = _start(auth_client, binding=bound_to_other)

    assert refused.status_code == 403
    assert refused.json()["code"] == "binding_mismatch"
    assert AgentccMCPConnectState.objects.count() == 0
    assert vendor.requests == []

    own = connect.seal_binding(str(organization.pk), SERVER_ID, str(user.pk))
    assert _start(auth_client, binding=own).status_code == 200


def test_start_other_user_403_nothing_started(
    auth_client, organization, user, workspace, config, vendor
):
    other, other_client = member(organization, workspace)
    for binding in (
        connect.seal_binding(str(organization.pk), SERVER_ID, str(user.pk)),
        connect.seal_binding(str(organization.pk), "shared-one", str(other.pk)),
        connect.seal_binding(str(uuid.uuid4()), SERVER_ID, str(other.pk)),
        "not-a-binding",
    ):
        response = _start(other_client, binding=binding)
        assert response.status_code == 403, binding
        assert response.json()["code"] in ("binding_mismatch", "binding_invalid")

    assert AgentccMCPConnectState.objects.count() == 0
    assert vendor.requests == []
    assert not any(n.startswith(connect.COOKIE_PREFIX) for n in response.cookies)


def test_a_binding_opens_only_with_the_internal_secret_and_before_it_expires(
    organization, user, monkeypatch
):
    sealed = connect.seal_binding(str(organization.pk), SERVER_ID, str(user.pk))
    assert connect.open_binding(sealed) == {
        "org_id": str(organization.pk),
        "server_id": SERVER_ID,
        "user_id": str(user.pk),
    }
    expired = connect.seal_binding(
        str(organization.pk), SERVER_ID, str(user.pk), ttl=-timedelta(seconds=1)
    )
    assert connect.open_binding(expired) is None
    raw = bytearray(base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4)))
    raw[-1] ^= 1
    altered = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    assert connect.open_binding(altered) is None

    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, "x" * 40 + "-another-secret")
    assert connect.open_binding(sealed) is None
    # The admin token never keys a binding, even when set as the secret.
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, ADMIN_TOKEN)
    assert connect.open_binding(sealed) is None


def test_a_connect_link_starts_nothing_and_sends_the_browser_to_the_console(
    organization, user, config, vendor
):
    binding = connect.seal_binding(str(organization.pk), SERVER_ID, str(user.pk))

    response = Client().get(START.format(SERVER_ID), {"binding": binding})

    assert response.status_code == 302
    target = urlsplit(response["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == CONSOLE
    assert _query(response["Location"]) == {"connect": SERVER_ID, "binding": binding}
    assert not response.cookies
    assert AgentccMCPConnectState.objects.count() == 0
    assert vendor.requests == []


def test_start_refuses_a_server_that_does_not_connect_each_member(
    auth_client, config, vendor
):
    shared = _start(auth_client, server_id="shared-one")
    missing = _start(auth_client, server_id="no-such-server")

    assert (shared.status_code, shared.json()["code"]) == (400, "server_not_per_user")
    assert (missing.status_code, missing.json()["code"]) == (404, "server_not_found")
    assert vendor.requests == []


# --- Property: PKCE S256, the resource, and our client --------------------------


def test_start_uses_cimd_pkce_resource(
    auth_client, organization, user, config, vendor, purges
):
    url, name, cookie = _started(auth_client)
    query = _query(url)

    assert url.startswith(ISSUER + "/authorize?")
    assert query["client_id"] == CLIENT_METADATA
    assert query["redirect_uri"] == CALLBACK
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert query["resource"] == SERVER_URL
    assert query["scope"] == "mcp"
    assert query["state"]
    row = AgentccMCPConnectState.objects.get()
    assert row.client_kind == "cimd"
    assert row.encrypted_verifier.startswith("enc::")
    assert query["state"] not in row.state_hash
    # The vendor redeems the code only with the verifier for that challenge.
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)
    assert _callback(browser, code=code, state=state, iss=ISSUER).status_code == 200
    assert _user_rows(organization, user).filter(status=Status.ACTIVE).count() == 1


def test_start_registers_a_client_when_the_server_takes_no_metadata_document(
    auth_client, organization, user, config, purges
):
    vendor = FakeVendor(cimd=False, dcr=True)
    with patch.object(connect.mcp_safe_fetch, "fetch", vendor.fetch):
        first, _, _ = _started(auth_client)
        second, _, _ = _started(auth_client)

    assert _query(first)["client_id"] == _query(second)["client_id"] == "dcr-client-1"
    assert len(vendor.registrations) == 1
    registration = vendor.registrations[0]
    assert registration["redirect_uris"] == [CALLBACK]
    assert registration["token_endpoint_auth_method"] == "none"
    assert AgentccMCPUpstreamClient.objects.get().client_id == "dcr-client-1"


@pytest.mark.parametrize(
    ("vendor_kwargs", "status", "code"),
    [
        ({"cimd": False, "dcr": False}, 409, "oauth_client_unavailable"),
        ({"s256": False}, 502, "discovery_failed"),
        ({"issuer": "https://elsewhere.example"}, 502, "discovery_failed"),
        ({"resource": "https://other.example/mcp"}, 502, "discovery_failed"),
    ],
    ids=["no-client-method", "no-s256", "issuer-mismatch", "foreign-resource"],
)
def test_start_refuses_an_authorization_server_it_cannot_trust(
    auth_client, config, vendor_kwargs, status, code
):
    vendor = FakeVendor(**vendor_kwargs)
    with patch.object(connect.mcp_safe_fetch, "fetch", vendor.fetch):
        response = _start(auth_client)

    assert (response.status_code, response.json()["code"]) == (status, code)
    assert AgentccMCPConnectState.objects.count() == 0


def test_discovery_never_reaches_a_private_address(auth_client, config, monkeypatch):
    monkeypatch.delenv(mcp_safe_fetch.PRIVATE_ALLOW_ENV, raising=False)

    def private(host, *args, **kwargs):
        return [(None, None, None, "", ("10.0.0.5", 0))]

    def never(*args, **kwargs):
        raise AssertionError("a connection was opened")

    with (
        patch.object(mcp_safe_fetch.socket, "getaddrinfo", private),
        patch.object(mcp_safe_fetch, "_open_pool", never),
    ):
        response = _start(auth_client)

    assert (response.status_code, response.json()["code"]) == (502, "discovery_refused")
    assert AgentccMCPConnectState.objects.count() == 0


# --- Property: state is single use and bound to the starting browser -----------


def test_state_bound_to_host_cookie_single_use(
    auth_client, organization, user, config, vendor, purges
):
    url, name, cookie = _started(auth_client)

    assert name.startswith("__Host-")
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/"
    assert not cookie["domain"]
    assert int(cookie["max-age"]) == 600

    code, state = vendor.authorize(url)
    # Another browser, without the cookie: refused, and the state is spent.
    refused = _callback(Client(), code=code, state=state, iss=ISSUER)
    assert refused.status_code == 400
    assert b"another browser" in refused.content
    browser = Client()
    browser.cookies[name] = cookie.value
    replay = _callback(browser, code=code, state=state, iss=ISSUER)
    assert replay.status_code == 400
    assert b"already used" in replay.content
    assert not _user_rows(organization, user).exists()
    assert purges == []


def test_a_second_callback_with_the_same_state_is_refused(
    auth_client, organization, user, config, vendor, purges
):
    url, name, cookie = _started(auth_client)
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)

    assert _callback(browser, code=code, state=state, iss=ISSUER).status_code == 200
    browser.cookies[name] = cookie.value
    assert _callback(browser, code=code, state=state, iss=ISSUER).status_code == 400
    assert _user_rows(organization, user).count() == 1


def test_a_connect_left_over_ten_minutes_is_refused(
    auth_client, organization, user, config, vendor, purges
):
    url, name, cookie = _started(auth_client)
    AgentccMCPConnectState.objects.update(expires_at=timezone.now())
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)

    response = _callback(browser, code=code, state=state, iss=ISSUER)

    assert response.status_code == 400
    assert not _user_rows(organization, user).exists()


# --- Property: the issuer and the member are the ones the start bound -----------


@pytest.mark.parametrize(
    "iss", [None, "https://elsewhere.example"], ids=["missing", "other"]
)
def test_callback_checks_iss_and_state_user_binding(
    auth_client, organization, user, workspace, config, vendor, purges, iss
):
    response = _connect(auth_client, vendor, iss=iss)

    assert response.status_code == 400
    assert b"different server" in response.content
    assert not AgentccMCPCredential.objects.exists()


def test_the_token_is_stored_for_the_member_who_started(
    auth_client, organization, user, workspace, config, vendor, purges
):
    other, other_client = member(organization, workspace)

    # The callback carries no login: whoever's browser it is, the token is
    # the starting member's, and only that browser completes it.
    assert _connect(other_client, vendor).status_code == 200

    assert not _user_rows(organization, user).exists()
    assert _user_rows(organization, other).filter(status=Status.ACTIVE).count() == 1
    assert purges == [(str(organization.pk), SERVER_ID, str(other.pk))]


def test_a_member_removed_during_the_connect_gets_nothing(
    auth_client, organization, user, workspace, config, vendor, purges
):
    other, other_client = member(organization, workspace)
    url, name, cookie = _started(other_client)
    other.organization_memberships.update(is_active=False)
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)

    response = _callback(browser, code=code, state=state, iss=ISSUER)

    assert response.status_code == 400
    assert not _user_rows(organization, other).exists()


def test_a_server_moved_during_the_connect_gets_no_token(
    auth_client, organization, user, config, vendor, purges
):
    url, name, cookie = _started(auth_client)
    move_server(organization, "https://mcp.elsewhere.example/mcp")
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)

    response = _callback(browser, code=code, state=state, iss=ISSUER)

    assert response.status_code == 400
    assert not _user_rows(organization, user).exists()


# --- Property: tokens are encrypted, and the refresh token never leaves --------


def test_tokens_encrypted_refresh_never_returned(
    auth_client, organization, user, config, vendor, purges
):
    with capture_logs() as logs:
        page = _connect(auth_client, vendor)

    assert page.status_code == 200
    assert page["Cache-Control"] == "no-store"
    assert page["Referrer-Policy"] == "no-referrer"
    tokens = vendor.issued[0]
    row = _user_rows(organization, user).get()
    assert (row.kind, row.status, row.version) == ("oauth", "active", 1)
    assert row.encrypted_secret.startswith("enc::")
    assert row.encrypted_refresh.startswith("enc::")
    assert decrypt_token(row.encrypted_secret) == tokens["access_token"]
    assert decrypt_token(row.encrypted_refresh) == tokens["refresh_token"]
    assert row.oauth_issuer == ISSUER
    assert row.oauth_client_id == CLIENT_METADATA
    assert row.origin == "https://mcp.vendor.example:443"
    assert row.token_expires_at > timezone.now()
    listing = auth_client.get("/agentcc/mcp/credentials/").content.decode()
    for secret in (tokens["access_token"], tokens["refresh_token"]):
        assert secret not in page.content.decode()
        assert secret not in listing
        assert secret not in repr(logs)
        assert secret not in repr(row)


def test_reconnecting_replaces_the_token_in_place(
    auth_client, organization, user, config, vendor, purges
):
    _connect(auth_client, vendor)
    _connect(auth_client, vendor)

    row = _user_rows(organization, user).get()
    assert row.version == 2
    assert decrypt_token(row.encrypted_secret) == vendor.issued[1]["access_token"]


# --- Disconnect ---------------------------------------------------------------


def test_disconnect_revokes_and_purges(
    auth_client, organization, user, config, vendor, purges
):
    _connect(auth_client, vendor)
    purges.clear()

    response = auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/")

    assert response.status_code == 200
    assert response.json()["result"] == {
        "server_id": SERVER_ID,
        "disconnected": True,
        "gateway_synced": True,
        "cleanup_pending": False,
        "upstream_revocation": "confirmed",
    }
    assert vendor.revoked == [
        {
            "token": vendor.issued[0]["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": CLIENT_METADATA,
        }
    ]
    row = _user_rows(organization, user).get()
    assert row.status == Status.REVOKED
    assert row.encrypted_refresh is None
    assert purges == [(str(organization.pk), SERVER_ID, str(user.pk))]
    assert auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/").status_code == 404


def test_disconnect_touches_only_the_members_own_connection(
    auth_client, organization, user, workspace, config, vendor, purges
):
    other, other_client = member(organization, workspace)
    _connect(auth_client, vendor)

    assert other_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/").status_code == 404
    assert _user_rows(organization, user).get().status == Status.ACTIVE
    assert vendor.revoked == []


# --- Our client metadata document --------------------------------------------


def test_client_metadata_document_names_its_own_url_and_one_redirect():
    response = Client().get("/agentcc/mcp/connect/client.json")

    assert response.status_code == 200
    assert response.json() == {
        "client_id": CLIENT_METADATA,
        "client_name": "Future AGI",
        "redirect_uris": [CALLBACK],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
