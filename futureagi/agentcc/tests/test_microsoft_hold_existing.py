"""The Microsoft hold leaves an organization's own servers and the vendor's own
sign-ins alone, and says what to do with what it still holds.

Real org, member, config, vault and binding rows; only outbound transport is
synthetic. Each test says what the frozen cut does where it differs.
"""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.utils import timezone

from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamTokenAudit as Audit
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services.mcp_credentials import origin_of
from agentcc.services.mcp_vendor_http import VendorUnavailable
from agentcc.tests import test_microsoft_connect_gate as gate

pytestmark = pytest.mark.django_db
rig = gate.rig

OWN_CLIENT = "fixture-dcr-client-not-real"
OTHER_URL = "https://docs.vendor.example/mcp"
MICROSOFT_URL = "https://mcp.fixture.microsoft.com/mcp"
MICROSOFT_ISSUER = "https://login.microsoftonline.com/fixture-tenant/v2.0"
PARTNER = "mail-partner"
COMPOSIO_KEY = "fixture-composio-key-not-real"
COMPOSIO_ENDPOINT = "https://backend.composio.dev/tool_router/fixture/mcp"
COMPOSIO_LINK = "https://connect.composio.dev/link/fixture"
VENDOR_ORIGINS = {
    "pipedream": "https://api.pipedream.com",
    "composio": "https://backend.composio.dev",
}
STOPPED = (
    "This connection stopped working after an update and cannot be used until "
    "Microsoft connections are available; remove it or keep it for later."
)
GRANT_STOPPED = (
    "This connection stopped working after an update and cannot be connected "
    "again yet; Disconnect removes it."
)
TOKEN_PATH = "/agentcc/internal/mcp/upstream-token/"
VENDOR_PATH = "/agentcc/mcp/vendors/internal/credential/"


def own_server(config, server_id, url, mode="per_user", **credential):
    """A server the organization added by URL: no catalogue reference."""
    config.mcp["servers"][server_id] = {
        "url": url,
        "credential": {"mode": mode, **credential},
    }
    config.save(update_fields=["mcp"])
    return config.mcp["servers"][server_id]


def grant(org, user, server_id, url, *, shared=False, **fields):
    """A grant as production stored it, by default from a client of the
    organization's own sign-in (never a platform secret)."""
    values = {"oauth_client_id": OWN_CLIENT, "oauth_issuer": gate.ISSUER, **fields}
    return gate.Credential.objects.create(
        organization=org,
        user=None if shared else user,
        server_id=server_id,
        scope="shared" if shared else "user",
        kind="oauth",
        encrypted_secret=gate.credential_manager.encrypt_token("fixture-access"),
        encrypted_refresh=gate.credential_manager.encrypt_token("fixture-refresh"),
        origin=origin_of(url),
        token_expires_at=timezone.now() + timedelta(hours=1),
        **values,
    )


def fetch(org, user, server_id):
    return gate.tokens.upstream_token(str(org.pk), str(user.pk), server_id)


def fetch_over_http(org, user, server_id):
    """The gateway's token fetch, as the gateway sends it."""
    body = {"org_id": str(org.pk), "user_id": str(user.pk), "server_id": server_id}
    return Client().post(
        TOKEN_PATH,
        json.dumps(body),
        content_type="application/json",
        HTTP_X_AGENTCC_MCP_INTERNAL=gate.INTERNAL_SECRET,
    )


def listed(org, user, server_id):
    rows = gate.mcp_connections.own_connections(org, user)
    return next(row for row in rows if row["server_id"] == server_id)


def start(org, user, server_id):
    return gate.oauth.start(org, user, server_id, individual_authenticated=True)


def microsoft_discovery(url):
    return gate.oauth.Discovery(
        issuer=MICROSOFT_ISSUER,
        authorization_endpoint=MICROSOFT_ISSUER + "/authorize",
        token_endpoint=MICROSOFT_ISSUER + "/token",
        registration_endpoint="",
        revocation_endpoint="",
        cimd_supported=True,
        iss_required=False,
        resource=url,
        scopes=(),
    )


def partner_binding(org, config, *, vendor="composio", kind="api_key"):
    """A partner binding for a Microsoft app (slug and toolkit)."""
    own_server(
        config,
        PARTNER,
        VENDOR_ORIGINS[vendor] + "/mcp",
        mode="vendor_end_user",
        vendor=vendor,
    )
    credential = gate.Credential.objects.create(
        organization=org,
        server_id=PARTNER,
        scope="shared",
        kind=kind,
        origin=VENDOR_ORIGINS[vendor],
        encrypted_secret=gate.credential_manager.encrypt_token(COMPOSIO_KEY),
    )
    composio = vendor == "composio"
    return gate.Binding.objects.create(
        organization=org,
        server_id=PARTNER,
        vendor=vendor,
        credential=credential,
        project_id="" if composio else "proj_fixture",
        app_slug="" if composio else gate.OUTLOOK,
        toolkits=[gate.OUTLOOK] if composio else [],
        tools={gate.OUTLOOK: ["FIXTURE_TOOL"]} if composio else {},
        enabled=True,
    )


def legacy_grant(org, user):
    """Kind 2: no recorded issuing client, signed in at a Microsoft issuer."""
    return grant(
        org,
        user,
        "mail",
        MICROSOFT_URL,
        oauth_client_id="",
        oauth_issuer=MICROSOFT_ISSUER,
    )


def member_connection(org, user, binding):
    return gate.VendorConnection.objects.create(
        binding=binding,
        end_user_key=gate.grants.subject_key(org.pk, "member", str(user.pk)),
        source="member",
        user=user,
        status="active",
        vendor_account_ref="ca_fixture",
        encrypted_session=gate.credential_manager.encrypt_token("trs_fixture"),
        encrypted_endpoint=gate.credential_manager.encrypt_token(COMPOSIO_ENDPOINT),
    )


def vendor_body(org, user, binding):
    return {
        "org_id": str(org.pk),
        "server_id": binding.server_id,
        "vendor": binding.vendor,
        "end_user": {"source": "member", "id": str(user.pk)},
    }


# --- Kind 1: an organization's own server, added by URL -----------------------


@pytest.mark.parametrize("shared", [False, True], ids=["per-person", "shared"])
def test_an_own_server_named_microsoft_on_another_host_is_served(rig, shared):
    """(a) Red at the frozen cut: held on its id alone (microsoft_coming_soon)."""
    org, user, config, _ = rig
    entry = own_server(config, "microsoft-docs", OTHER_URL)
    row = grant(org, user, "microsoft-docs", OTHER_URL, shared=shared)
    if shared:
        entry["credential"] = {
            "mode": "shared",
            "kind": "oauth",
            "state": "active",
            "credential_id": str(row.pk),
        }
        config.save(update_fields=["mcp"])
        answer = gate.tokens.shared_token(str(org.pk), "microsoft-docs")
    else:
        answer = fetch(org, user, "microsoft-docs")
    assert answer.outcome == "ok"
    assert answer.body()["access_token"] == "fixture-access"
    if not shared:
        own = listed(org, user, "microsoft-docs")
        assert own["start"]["reason"] != gate.CODE
        assert own["status"] == "active" and own["connected"] is True


@pytest.mark.parametrize("server_id", [gate.OUTLOOK, "microsoft-graph"])
def test_a_microsoft_server_with_our_held_sign_in_stays_held(
    rig, monkeypatch, server_id
):
    """(b) Guard, held at the frozen cut and after the fix: a catalogue
    Microsoft server, and a server added by URL on a Microsoft host, each
    holding a grant from our own (held) platform client. Nothing is read,
    written or fetched, and the account stays listed with Disconnect."""
    org, user, config, fake = rig
    if server_id == gate.OUTLOOK:
        url = config.mcp["servers"][gate.OUTLOOK]["url"]
    else:
        url = own_server(config, server_id, MICROSOFT_URL)["url"]
    grant(org, user, server_id, url, oauth_client_id=gate.PLATFORM_ID)
    with gate.no_effects(monkeypatch, fake):
        answer = fetch(org, user, server_id)
        assert answer.outcome == gate.CODE
        # A grant with a recorded client: the plain hold, with no other words.
        assert answer.body() == {"error": gate.CODE}
        response = fetch_over_http(org, user, server_id)
        assert response.status_code == 409
        assert response.json() == {"error": gate.CODE, "message": gate.MESSAGE}
        gate.refused(lambda: start(org, user, server_id))
    own = listed(org, user, server_id)
    assert own["start"] == {"state": "unavailable", "reason": gate.CODE}
    assert own["status"] == "active" and own["connected"] is False
    assert own["disconnect_url"] == f"/agentcc/mcp/connect/{server_id}/"


@pytest.mark.parametrize("server_id", ["graph", "microsoft-graph"])
def test_an_own_server_on_a_microsoft_host_is_held_under_any_name(
    rig, monkeypatch, settings, server_id
):
    """(g) Red at the frozen cut for "graph" (it held by id only, so this
    Microsoft-hosted server dispatched); guard for "microsoft-graph". With
    Microsoft connections on as well, it never gets the platform client: the
    platform's grant is refused unread and a sign-in needs the organization's
    own OAuth app."""
    org, user, config, fake = rig
    entry = own_server(config, server_id, MICROSOFT_URL)
    grant(org, user, server_id, MICROSOFT_URL, oauth_client_id=gate.PLATFORM_ID)
    with gate.no_effects(monkeypatch, fake):
        assert fetch(org, user, server_id).outcome == gate.CODE
        gate.refused(lambda: start(org, user, server_id))
    assert listed(org, user, server_id)["start"]["reason"] == gate.CODE
    settings.MICROSOFT_CONNECT_ENABLED = True
    assert microsoft.platform_allowed(entry, server_id=server_id) is False
    with gate.no_effects(monkeypatch, fake):
        assert fetch(org, user, server_id).outcome == "needs_reauth"
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            start(org, user, server_id)
        assert caught.value.code == gate.clients.OrgClientRequired.code


def test_an_own_server_signed_in_at_a_microsoft_issuer_is_held(rig, monkeypatch):
    """(g) Red at the frozen cut: served, since neither its id nor its URL is
    Microsoft's. Its stored OAuth issuer is."""
    org, user, config, fake = rig
    own_server(config, "docs", OTHER_URL)
    grant(org, user, "docs", OTHER_URL, oauth_issuer=MICROSOFT_ISSUER)
    with gate.no_effects(monkeypatch, fake):
        assert fetch(org, user, "docs").outcome == gate.CODE
    assert listed(org, user, "docs")["start"]["reason"] == gate.CODE


def test_a_sign_in_at_a_microsoft_issuer_is_held_before_any_state(rig, monkeypatch):
    """(g) Red at the frozen cut: the sign-in starts, and the grant it stores
    would then be held at every fetch."""
    org, user, config, _ = rig
    own_server(config, "docs", OTHER_URL)
    monkeypatch.setattr(gate.oauth, "discover", microsoft_discovery)
    gate.refused(lambda: start(org, user, "docs"))
    assert not gate.State.objects.filter(organization=org, server_id="docs").exists()


def test_a_sign_in_in_flight_at_a_microsoft_issuer_is_held_at_the_callback(
    rig, monkeypatch, settings
):
    """(g) Red at the frozen cut: the callback completes a sign-in that
    started while Microsoft connections were on, after they were switched
    off. The state is kept, as for a catalogue Microsoft sign-in."""
    org, user, config, fake = rig
    own_server(config, "docs", OTHER_URL)
    monkeypatch.setattr(gate.oauth, "discover", microsoft_discovery)
    settings.MICROSOFT_CONNECT_ENABLED = True
    started = start(org, user, "docs")
    settings.MICROSOFT_CONNECT_ENABLED = False
    state = parse_qs(urlsplit(started.authorize_url).query)["state"][0]
    browser = Client()
    browser.cookies[started.cookie_name] = started.cookie_value
    with gate.no_effects(monkeypatch, fake):
        response = browser.get(
            "/agentcc/mcp/connect/callback/",
            {"state": state, "code": "fixture-code", "iss": MICROSOFT_ISSUER},
        )
        assert response.status_code == 409
        assert gate.MESSAGE in response.content.decode()
    assert gate.State.objects.filter(pk=started.connect_id).exists()


# --- Kind 2: a grant with no recorded client, from a Microsoft host -----------


def test_a_legacy_microsoft_grant_with_no_client_id_needs_a_new_sign_in(rig, settings):
    """(e)/(f) Kind 2 where the hold does not apply (Microsoft connections
    on). Red at the frozen cut: the row reads active and connected while the
    token fetch refuses it. The fetch keeps the contract's needs_reauth with a
    connect hint, which the gateway answers as "connect your account again"."""
    settings.MICROSOFT_CONNECT_ENABLED = True
    org, user, config, _ = rig
    own_server(config, "mail", MICROSOFT_URL)
    legacy_grant(org, user)
    response = fetch_over_http(org, user, "mail")
    assert response.status_code == 200
    assert response.json() == {
        "error": "needs_reauth",
        "connect_hint": "https://api.example.test/agentcc/mcp/connect/mail/start/",
    }
    own = listed(org, user, "mail")
    assert own["status"] == "needs_reauth" and own["connected"] is False
    assert own["disconnect_url"] == "/agentcc/mcp/connect/mail/"


@pytest.mark.parametrize("held_by", ["host", "issuer"])
def test_a_legacy_microsoft_grant_is_held_while_microsoft_is_switched_off(
    rig, monkeypatch, held_by
):
    """(e)/(f) Kind 2 on an own server, switch off (production): the hold
    answers first, by the server's Microsoft host or by the Microsoft issuer
    its grant came from. The refusal and the row both say it stopped working
    after an update, that it cannot be connected again yet (the hold also
    stops a new sign-in) and that Disconnect removes it; the row needs a new
    sign-in, never "Connected". Still read-only and unaudited. Red at the
    frozen cut: "mail" was not held (200 needs_reauth). Red at the first fix:
    the refusal had only "Microsoft connections are coming soon." and the row
    read active."""
    org, user, config, fake = rig
    if held_by == "host":
        own_server(config, "mail", MICROSOFT_URL)
        legacy_grant(org, user)
    else:
        own_server(config, "mail", OTHER_URL)
        grant(
            org,
            user,
            "mail",
            OTHER_URL,
            oauth_client_id="",
            oauth_issuer=MICROSOFT_ISSUER,
        )
    with gate.no_effects(monkeypatch, fake):
        answer = fetch(org, user, "mail")
        assert answer.outcome == gate.CODE
        assert answer.body() == {"error": gate.CODE, "message": GRANT_STOPPED}
        response = fetch_over_http(org, user, "mail")
        assert response.status_code == 409
        assert response.json() == {"error": gate.CODE, "message": GRANT_STOPPED}
    assert not Audit.objects.filter(server_id="mail").exists()
    own = listed(org, user, "mail")
    assert own["start"] == {"state": "unavailable", "reason": gate.CODE}
    assert own["status"] == "needs_reauth" and own["connected"] is False
    assert own["start_url"] is None
    assert own["disconnect_url"] == "/agentcc/mcp/connect/mail/"


# --- Kind 3: partner vendor bindings -------------------------------------------


def test_a_composio_microsoft_binding_on_the_vendors_own_app_dispatches(
    rig, monkeypatch
):
    """(c) Red at the frozen cut: refused microsoft_coming_soon, though the
    vendor's key (not an OAuth client of ours) signs people in with the
    vendor's own Microsoft app. Arcade has no binding row in this service."""
    org, user, config, _ = rig
    binding = partner_binding(org, config)
    member_connection(org, user, binding)
    monkeypatch.setattr(gate.grants.Composio, "pin_account", lambda *args: None)
    monkeypatch.setattr(gate.grants.Composio, "account", lambda *args: "ca_fixture")
    assert isinstance(gate.grants.vendor_client(binding), gate.grants.Composio)
    answer = gate.grants.answer_request(vendor_body(org, user, binding))
    assert answer["vendor"] == "composio"
    assert answer["api_key"] == COMPOSIO_KEY
    assert answer["endpoint"] == COMPOSIO_ENDPOINT


def test_a_composio_microsoft_binding_on_the_vendors_own_app_still_connects(
    rig, monkeypatch
):
    """(c) Red at the frozen cut: a member's connect start is refused
    microsoft_coming_soon for the same binding."""
    org, user, config, _ = rig
    binding = partner_binding(org, config)
    monkeypatch.setattr(
        gate.grants.Composio, "create", lambda *args: ("trs_fixture", COMPOSIO_ENDPOINT)
    )
    monkeypatch.setattr(gate.grants.Composio, "connect", lambda *args: COMPOSIO_LINK)
    identity = {"source": "member", "id": str(user.pk)}
    assert gate.vendors.start(binding.pk, identity) == COMPOSIO_LINK


@pytest.mark.parametrize(
    "vendor,kind",
    [
        ("pipedream", "oauth_client"),
        ("pipedream", "api_key"),
        ("composio", "oauth_client"),
    ],
)
@pytest.mark.parametrize("enabled", [False, True], ids=["switch-off", "switch-on"])
def test_a_partner_binding_with_its_own_client_json_stays_refused(
    rig, monkeypatch, settings, vendor, kind, enabled
):
    """(d) Guard at the frozen cut and after the fix: a Pipedream binding, or
    one whose credential is an OAuth client, carries a client id no stored
    record proves. It stays refused before any secret is read."""
    org, _, config, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = enabled
    binding = partner_binding(org, config, vendor=vendor, kind=kind)
    expected = VendorUnavailable if enabled else gate.oauth.ConnectRefused
    with gate.no_effects(monkeypatch, fake):
        with pytest.raises(expected):
            gate.grants.vendor_client(binding)


@pytest.mark.parametrize(
    "vendor,kind", [("pipedream", "oauth_client"), ("composio", "oauth_client")]
)
def test_a_held_partner_binding_says_it_stopped_working_after_an_update(
    rig, monkeypatch, vendor, kind
):
    """(f) Red at the frozen cut: the refusal says only "Microsoft connections
    are coming soon.". Still a 409 policy refusal with no write or secret
    read."""
    org, user, config, fake = rig
    binding = partner_binding(org, config, vendor=vendor, kind=kind)
    member_connection(org, user, binding)
    body = vendor_body(org, user, binding)
    with gate.no_effects(monkeypatch, fake):
        assert gate.grants.answer_request(body) == {
            "error": gate.CODE,
            "message": STOPPED,
        }
        response = Client().post(
            VENDOR_PATH,
            json.dumps(body),
            content_type="application/json",
            HTTP_X_AGENTCC_MCP_INTERNAL=gate.INTERNAL_SECRET,
        )
        assert response.status_code == 409
        assert response.json() == {"error": gate.CODE, "message": STOPPED}
