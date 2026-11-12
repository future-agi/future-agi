"""Vendor MCP servers whose OAuth takes only a pre-registered client sign in
each person with the organization's own OAuth app.

Such a record declares ``oauth2`` with ``registration: ["preregistered"]``
and no dcr or cimd (Slack, HubSpot, Asana, Zoom, Docusign and Salesforce in
the shipped catalog). Once an Owner or Admin registers the organization's own
app for it (Configure OAuth, manual client), each member signs in with their
own account through exactly the authorization server the record declares:
its authorize, token and revocation URLs, scopes, PKCE and client
authentication. Nothing is discovered and FutureAGI's platform client is never
read. Without the organization's app the card stays listed and the sign-in
waits for an Owner or Admin. The gateway gets the member's token through the
existing per-person token fetch.

The pilot connectors and groups and the Microsoft group keep their behaviour. Every
value here is a fake: the vendor is ``FakeVendor``, answered in place of the
guarded fetcher, and the gateway's cache purge is recorded, so no test reaches
a network.
"""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.utils import timezone
from structlog.testing import capture_logs

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_org_app_oauth as org_app
from agentcc.services import gateway_client
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as upstream_tokens
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_URL,
    START,
    FakeVendor,
    _response,
)
from agentcc.tests.test_connector_catalog import (
    CATALOG,
    _active,
    _add,
    _app,
    _bundle,
    _ok,
    _provider,
    _refused,
    _seed,
    _versions,
)
from agentcc.tests.test_mcp_connections import LIST

bundle = fixtures.bundle
drift_free = fixtures.drift_free
probe = fixtures.probe
probe_budget = fixtures.probe_budget
pushes = fixtures.pushes
purges = flow.purges

# Slack-like: the client secret in the form, a declared revocation endpoint.
CHAT = "slab-chat"
CHAT_URL = SERVER_URL
CHAT_SCOPES = ["chat:read", "chat:write"]
# Zoom-like: HTTP Basic client authentication, no revocation endpoint.
MEET = "zinc-meet"
MEET_URL = "https://mcp.zinc-meet.example.com/mcp"
# Pre-registered only, but in a pilot group, in the Microsoft group, or a pilot app.
K2_GROUP_APP = "glint-sheets"
MICROSOFT_APP = "microsoft-loop"
K2_APP = "google-bigquery"
UNCHANGED = [(K2_GROUP_APP, "mcp"), (MICROSOFT_APP, "mcp"), (K2_APP, "mcp")]

CONFIGURE_OAUTH = {"served": True, "configure_oauth": True}
CLIENT_ID = "synthetic-org-app-client-id"
CLIENT_SECRET = "synthetic-org-app-client-secret-not-real"
REPLACEMENT_ID = "synthetic-replacement-app-client-id"
REPLACEMENT_SECRET = "synthetic-replacement-app-client-secret-not-real"
OTHER_ID = "synthetic-other-org-app-client-id"
OTHER_SECRET = "synthetic-other-org-app-client-secret-not-real"
PLATFORM_ID = "synthetic-platform-client-id"
PLATFORM_SECRET = "synthetic-platform-client-secret-not-real"
SECRETS = (CLIENT_SECRET, REPLACEMENT_SECRET, OTHER_SECRET, PLATFORM_SECRET)
PLATFORM_ENV = (
    "AGENTCC_CONNECTOR_CLIENT_SLAB_CHAT_ID",
    "AGENTCC_CONNECTOR_CLIENT_SLAB_CHAT_SECRET",
)
TOKEN_URL = ISSUER + "/token"
REQUIRED = {"state": "needs_admin", "reason": "org_oauth_app_required"}

# The shipped catalog's vendor MCP providers that take only a pre-registered
# client, outside the pilot and Microsoft groups.
REAL_ORG_APP = {
    ("asana", "mcp"),
    ("docusign", "mcp"),
    ("github", "remote"),
    ("hubspot", "mcp"),
    ("linear", "oauth-app"),
    ("salesforce", "hosted-mcp"),
    ("salesforce", "hosted-mcp-sandbox"),
    ("slack", "mcp"),
    ("snowflake", "oauth"),
    ("zoom", "mcp"),
}
REAL_NO_LONGER_COMING_SOON = {
    "asana",
    "docusign",
    "hubspot",
    "salesforce",
    "slack",
    "zoom",
}
REAL_UNCHANGED = [
    ("gmail", "google-mcp"),
    ("google-calendar", "google-mcp"),
    ("google-bigquery", "mcp"),
    ("microsoft-outlook", "workiq"),
    ("microsoft-teams", "workiq"),
]


def _oauth2(*, client_auth="body", revocation=True, **extra):
    spec = {
        "authorizeUrl": ISSUER + "/authorize",
        "tokenUrl": TOKEN_URL,
        "scopes": [],
        "pkce": True,
        "tokenBody": "form",
        "clientAuth": client_auth,
        "registration": ["preregistered"],
    }
    if revocation:
        spec["revocation"] = {
            "url": ISSUER + "/revoke",
            "method": "POST",
            "tokenBody": "form",
            "tokenField": "token",
            "clientAuth": client_auth,
        }
    spec.update(extra)
    return {"kind": "oauth2", "oauth2": spec}


def _org_app_provider(app_id, url, **oauth2):
    return _provider(
        app_id,
        "mcp",
        auth=_oauth2(**oauth2),
        tools=[],
        account_modes=["agent", "user"],
        transport={"kind": "http", "url": url},
    )


def _synthetic_app(app_id, name):
    return _app(
        app_id,
        name,
        f"Synthetic {name} app.",
        category="communication",
        categories=["chat"],
        default="mcp",
    )


@pytest.fixture
def vendor(bundle, user, monkeypatch, settings, pushes, purges):
    """The synthetic catalog, and the vendor every request reaches."""
    data = _bundle()
    data["apps"] = [
        _synthetic_app(CHAT, "Slab Chat"),
        _synthetic_app(MEET, "Zinc Meet"),
        _synthetic_app(K2_GROUP_APP, "Glint Sheets"),
        _synthetic_app(MICROSOFT_APP, "Microsoft Loop"),
        _synthetic_app(K2_APP, "Google BigQuery"),
    ]
    data["providers"] = [
        _org_app_provider(CHAT, CHAT_URL, scopes=CHAT_SCOPES),
        _org_app_provider(MEET, MEET_URL, client_auth="basic", revocation=False),
        _org_app_provider(
            K2_GROUP_APP,
            "https://mcp.glint-sheets.example.com/mcp",
            clientGroup="google",
        ),
        _org_app_provider(MICROSOFT_APP, "https://mcp.microsoft-loop.example.com/mcp"),
        _org_app_provider(K2_APP, "https://mcp.google-bigquery.example.com/mcp"),
    ]
    bundle(data)
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    for name in PLATFORM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENTS_FILE", raising=False)
    fake = FakeVendor(
        cimd=False,
        dcr=False,
        confidential={
            CLIENT_ID: CLIENT_SECRET,
            REPLACEMENT_ID: REPLACEMENT_SECRET,
            OTHER_ID: OTHER_SECRET,
            PLATFORM_ID: PLATFORM_SECRET,
        },
    )
    monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", fake.fetch)
    _seed(user.organization, {"enabled": True})
    return fake


# --- Helpers -------------------------------------------------------------------


def _oauth_root(app_id):
    return f"/agentcc/connectors/apps/{app_id}/oauth/"


def _configure(client, app_id=CHAT, url=CHAT_URL, **changes):
    body = {
        "provider": "mcp",
        "discovery_url": url,
        "mode": "per_user",
        "registration": "manual",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    body.update(changes)
    return client.post(_oauth_root(app_id), body, format="json")


def _configured(client, app_id=CHAT, url=CHAT_URL):
    return _ok(_configure(client, app_id, url), 201)["result"]


def _org_app_row(org, server_id=CHAT):
    return AgentccMCPCredential.objects.get(
        organization=org,
        server_id=f"server:{server_id}",
        kind="oauth_client",
        status="active",
    )


def _register(org, actor, client_id, secret, server_id=CHAT):
    return vault.create_oauth_client(
        org,
        server_id=server_id,
        client_id=client_id,
        client_secret=vault.SealedSecret(secret),
        actor=actor,
    )


def _replace(org, actor, server_id=CHAT):
    vault.revoke_oauth_client(_org_app_row(org, server_id), actor)
    _register(org, actor, REPLACEMENT_ID, REPLACEMENT_SECRET, server_id)


def _start(client, server_id=CHAT):
    return client.post(START.format(server_id), {}, format="json")


def _started(client, server_id=CHAT):
    response = _start(client, server_id)
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    return url, name, response.cookies[name].value


def _callback(vendor, url, name, cookie, browser=None):
    """The member signs in at the vendor and comes back to the callback in
    the browser holding ``cookie`` (none for another browser)."""
    code, state = vendor.authorize(url)
    browser = browser or Client()
    if cookie is not None:
        browser.cookies[name] = cookie
    return browser.get(CALLBACK_PATH, {"code": code, "state": state}), state


def _sign_in(client, vendor, server_id=CHAT):
    url, name, cookie = _started(client, server_id)
    page, _ = _callback(vendor, url, name, cookie)
    return url, page


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _gets(vendor):
    return [r for r in vendor.requests if r[0] == "GET"]


def _auths(vendor, endpoint):
    return [a for a in vendor.client_auths if a[0] == endpoint]


def _expire_soon(org, user, server_id=CHAT):
    row = flow.account(org, user, server_id)
    row.token_expires_at = timezone.now() + timedelta(seconds=30)
    row.save(update_fields=["token_expires_at"])
    return row


def _plan(app_id, provider_id="mcp"):
    return catalog.load_bundle().apps[app_id].plans[provider_id]


def _disconnect(client, server_id=CHAT):
    return client.delete(f"/agentcc/mcp/connect/{server_id}/")


def _names_its_own_token_type(vendor, monkeypatch, token_type="user"):
    """The vendor's token endpoint answers ``token_type`` in place of Bearer,
    as Slack's user token method answers ``"user"``. Returns the token types
    answered so far."""
    answered = []

    def fetch(url, **kwargs):
        response = vendor.fetch(url, **kwargs)
        if url != TOKEN_URL or response.status != 200:
            return response
        answered.append(token_type)
        return _response(200, dict(response.json(), token_type=token_type))

    monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", fetch)
    return answered


# --- The plan and the card -----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestThePlanAndTheCard:
    @pytest.mark.parametrize("app_id", [CHAT, MEET])
    def test_a_preregistered_record_plans_a_per_person_sign_in(
        self, auth_client, app_id
    ):
        plan = _plan(app_id)
        assert isinstance(plan, catalog.Plan), plan
        assert (plan.credential.mode, plan.credential.kind) == ("per_user", "oauth")
        assert plan.credential.label == "OAuth account"
        assert plan.modes == ("user",)
        app = _ok(auth_client.get(f"{CATALOG}{app_id}/"))["result"]
        assert app.get("status") != "coming_soon"
        assert app["default_provider"] == "mcp"
        (provider,) = app["providers"]
        assert provider["connect"] == CONFIGURE_OAUTH
        assert provider["account_modes"] == ["user"]
        assert provider["credential"]["kind"] == "oauth"
        configuration = _ok(auth_client.get(_oauth_root(app_id)))["result"]
        assert configuration["configured"] is False

    def test_add_asks_for_the_organizations_app_first(
        self, auth_client, user, probe, pushes
    ):
        before = _versions(user.organization)
        _refused(
            auth_client.post(
                _add(CHAT), {"provider": "mcp", "mode": "user"}, format="json"
            ),
            409,
            "oauth_configuration_required",
        )
        assert _versions(user.organization) == before
        assert probe.calls == [] and pushes == []

    @pytest.mark.parametrize("ref", UNCHANGED)
    def test_k2_and_microsoft_records_keep_their_refusal(self, auth_client, ref):
        """A guard: these pass before and after the change."""
        plan = _plan(*ref)
        assert isinstance(plan, catalog.Unavailable), plan
        assert plan.code == catalog.OAUTH_NOT_BUILT
        app_id, provider_id = ref
        body = _ok(auth_client.get(CATALOG, {"limit": 200}))
        row = next((a for a in body["results"] if a["id"] == app_id), None)
        assert row is None or provider_id not in {p["id"] for p in row["providers"]}


# --- Configure OAuth: the organization's own app --------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestConfigureTheOrganizationsApp:
    def test_discovery_answers_the_declared_server_and_fetches_nothing(
        self, auth_client, vendor
    ):
        result = _ok(
            auth_client.post(
                _oauth_root(CHAT) + "discovery/",
                {"provider": "mcp", "discovery_url": CHAT_URL},
                format="json",
            )
        )["result"]
        assert result == {
            "issuer": ISSUER,
            "dcr_supported": False,
            "pkce_s256": True,
            "redirect_uri": CALLBACK,
        }
        assert vendor.requests == []

    def test_dynamic_registration_is_refused_before_anything_is_written(
        self, auth_client, user, vendor, pushes
    ):
        before = _versions(user.organization)
        dcr = {
            "provider": "mcp",
            "discovery_url": CHAT_URL,
            "mode": "per_user",
            "registration": "dcr",
        }
        refused = _refused(
            auth_client.post(_oauth_root(CHAT), dcr, format="json"),
            409,
            "dcr_unavailable",
        )
        assert "own OAuth app" in refused["message"]
        assert _versions(user.organization) == before
        assert not AgentccMCPCredential.objects.filter(kind="oauth_client").exists()
        assert pushes == [] and vendor.requests == []

    def test_a_shared_account_is_not_offered(self, auth_client, user, pushes):
        before = _versions(user.organization)
        _refused(
            _configure(auth_client, mode="shared"),
            400,
            "connector_account_mode_not_declared",
        )
        assert _versions(user.organization) == before
        assert pushes == []

    def test_the_app_is_stored_in_the_vault_and_the_server_per_person(
        self, auth_client, user, vendor, pushes
    ):
        org = user.organization
        result = _configured(auth_client)
        assert (result["configured"], result["mode"]) == (True, "per_user")
        assert (result["registration"], result["has_secret"]) == ("manual", True)
        server = _active(org).mcp["servers"][CHAT]
        assert server["catalog_ref"] == _plan(CHAT).catalog_ref
        assert server["credential"] == {"mode": "per_user"}
        assert server["url"] == CHAT_URL
        row = _org_app_row(org)
        assert row.oauth_client_id == CLIENT_ID
        assert row.encrypted_secret != CLIENT_SECRET
        assert decrypt_token(row.encrypted_secret) == CLIENT_SECRET
        assert len(pushes) == 1
        # Registering contacts nobody: there is nothing to discover.
        assert vendor.requests == []


# --- The sign-in -----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestTheSignIn:
    def test_start_asks_the_declared_authorization_server(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)

        url, _, _ = _started(auth_client)

        assert url.startswith(ISSUER + "/authorize?")
        asked = _query(url)
        assert asked["response_type"] == "code"
        assert asked["client_id"] == CLIENT_ID
        assert asked["redirect_uri"] == CALLBACK
        assert asked["code_challenge_method"] == "S256"
        assert asked["code_challenge"] and asked["state"]
        assert asked["scope"] == "chat:read chat:write"
        assert "resource" not in asked
        (row,) = AgentccMCPConnectState.objects.all()
        assert (row.organization_id, row.user_id, row.server_id) == (
            organization.pk,
            user.pk,
            CHAT,
        )
        assert row.state_hash != asked["state"]
        assert (row.client_id, row.client_kind) == (CLIENT_ID, "org")
        assert (row.token_endpoint, row.redirect_uri) == (TOKEN_URL, CALLBACK)
        assert (row.resource, row.org_only) == ("", True)
        assert vendor.requests == []

    def test_the_code_is_exchanged_at_the_declared_token_url_as_the_app(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)

        _, page = _sign_in(auth_client, vendor)

        assert page.status_code == 200, page.content
        assert _auths(vendor, "token") == [("token", "post", CLIENT_ID, True)]
        (posted,) = [r for r in vendor.requests if r[0] == "POST"]
        assert posted[1] == TOKEN_URL
        assert posted[2]["redirect_uri"] == CALLBACK
        assert posted[2]["code_verifier"]
        assert "resource" not in posted[2]
        row = flow.account(organization, user, CHAT)
        assert row.status == "active"
        assert (row.oauth_client_id, row.oauth_resource) == (CLIENT_ID, "")
        assert row.oauth_org_only is True
        # The origin every connection stores: the default port is filled in.
        assert row.origin == "https://mcp.vendor.example:443"
        assert decrypt_token(row.encrypted_secret) == vendor.issued[0]["access_token"]
        assert _gets(vendor) == []

    def test_basic_client_authentication_as_declared(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client, MEET, MEET_URL)

        url, page = _sign_in(auth_client, vendor, MEET)

        assert "scope" not in _query(url)
        assert page.status_code == 200, page.content
        assert _auths(vendor, "token") == [("token", "basic", CLIENT_ID, True)]
        assert flow.account(organization, user, MEET).oauth_client_id == CLIENT_ID

    def test_the_state_works_once_and_only_in_the_browser_that_started(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        url, name, cookie = _started(auth_client)
        code, state = vendor.authorize(url)

        elsewhere = Client().get(CALLBACK_PATH, {"code": code, "state": state})
        browser = Client()
        browser.cookies[name] = cookie
        again = browser.get(CALLBACK_PATH, {"code": code, "state": state})

        assert elsewhere.status_code == 400
        assert again.status_code == 400
        assert vendor.client_auths == []
        assert not AgentccMCPCredential.objects.filter(kind="oauth").exists()
        assert not AgentccMCPConnectState.objects.exists()

    def test_the_gateway_gets_the_members_token_and_refresh_uses_the_same_app(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        _sign_in(auth_client, vendor)

        fetched = flow.fetch(organization, user, CHAT)
        _expire_soon(organization, user)
        refreshed = flow.fetch(organization, user, CHAT)

        assert fetched["access_token"] == vendor.issued[0]["access_token"]
        assert refreshed["access_token"] == vendor.issued[1]["access_token"]
        (asked,) = vendor.refreshes
        assert asked["grant_type"] == "refresh_token"
        assert asked["refresh_token"] == vendor.issued[0]["refresh_token"]
        assert "resource" not in asked
        assert _auths(vendor, "token")[-1] == ("token", "post", CLIENT_ID, True)
        assert _gets(vendor) == []
        assert flow.listed(auth_client, CHAT)["connected"] is True

    def test_a_token_type_the_vendor_names_itself_signs_in_and_refreshes(
        self, auth_client, organization, user, vendor, monkeypatch
    ):
        """Slack's user token method answers ``"token_type": "user"``. The
        gateway presents the member's token as a bearer token either way."""
        answered = _names_its_own_token_type(vendor, monkeypatch)
        _configured(auth_client)

        _, page = _sign_in(auth_client, vendor)
        assert page.status_code == 200, page.content
        fetched = flow.fetch(organization, user, CHAT)
        _expire_soon(organization, user)
        refreshed = flow.fetch(organization, user, CHAT)

        assert answered == ["user", "user"]
        assert flow.account(organization, user, CHAT).status == "active"
        assert fetched["access_token"] == vendor.issued[0]["access_token"]
        assert refreshed["access_token"] == vendor.issued[1]["access_token"]
        assert fetched["token_type"] == refreshed["token_type"] == "Bearer"
        assert _auths(vendor, "token")[-1] == ("token", "post", CLIENT_ID, True)

    def test_a_refresh_reads_no_metadata_while_the_declared_server_cannot_be_read(
        self, auth_client, organization, user, vendor, monkeypatch
    ):
        """The organization's secret and the member's refresh token go only to
        the token URL the provider declares, never to one read from the
        issuer's metadata. The member's connection is left as it was."""
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        row = _expire_soon(organization, user)
        assert row.oauth_issuer == ISSUER
        monkeypatch.setattr(org_app, "declared", lambda entry: None)
        real_metadata = connect.authorization_server_metadata
        metadata_reads = []

        def metadata(issuer):
            metadata_reads.append(issuer)
            return real_metadata(issuer)

        monkeypatch.setattr(connect, "authorization_server_metadata", metadata)
        requests_before = len(vendor.requests)
        auths_before = list(vendor.client_auths)

        fetched = flow.fetch(organization, user, CHAT)

        assert metadata_reads == []
        assert vendor.requests[requests_before:] == []
        assert vendor.client_auths == auths_before
        assert vendor.refreshes == []
        # Held back before any refresh: this sign-in is not served now.
        assert fetched.get("error") == "no_credential"
        assert "connect_hint" not in fetched
        assert "access_token" not in fetched
        kept = flow.account(organization, user, CHAT)
        assert (kept.status, kept.version) == ("active", row.version)
        stored = decrypt_token(kept.encrypted_refresh)
        assert stored == vendor.issued[0]["refresh_token"]

    def test_the_refresh_itself_refuses_while_the_declared_server_cannot_be_read(
        self, auth_client, organization, user, vendor, monkeypatch
    ):
        """The refresh step refuses on its own when an organization app's
        declared sign-in cannot be read: it answers None (the fetch's
        needs_reauth), reads no issuer metadata, sends no request and leaves
        the member's connection as it was."""
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        row = _expire_soon(organization, user)
        entry = connect.server_entry(organization.pk, CHAT)
        assert org_app.is_org_app_server(entry) is True
        monkeypatch.setattr(org_app, "declared", lambda entry: None)
        real_metadata = connect.authorization_server_metadata
        metadata_reads = []

        def metadata(issuer):
            metadata_reads.append(issuer)
            return real_metadata(issuer)

        monkeypatch.setattr(connect, "authorization_server_metadata", metadata)
        requests_before = len(vendor.requests)
        auths_before = list(vendor.client_auths)

        refreshed = upstream_tokens._refresh(row, CHAT_URL)

        assert metadata_reads == []
        assert vendor.requests[requests_before:] == []
        assert vendor.client_auths == auths_before
        assert vendor.refreshes == []
        assert refreshed is None
        kept = flow.account(organization, user, CHAT)
        assert (kept.status, kept.version) == ("active", row.version)
        stored = decrypt_token(kept.encrypted_refresh)
        assert stored == vendor.issued[0]["refresh_token"]
        access = decrypt_token(kept.encrypted_secret)
        assert access == vendor.issued[0]["access_token"]


@pytest.mark.parametrize(
    ("token_type", "any_token_type", "parsed"),
    [
        ("Bearer", False, True),
        ("bearer", True, True),
        ("user", False, False),
        ("user", True, True),
        ("", True, False),
        (None, True, False),
        (7, True, False),
    ],
)
def test_only_a_server_with_the_organizations_app_may_name_its_own_token_type(
    token_type, any_token_type, parsed
):
    """Without the flag only Bearer is taken, as for discovered and pilot servers;
    a server that signs in with the organization's own app may name its own
    type, never an empty one."""
    body = {"access_token": "test-upstream-at-not-real", "expires_in": 60}
    if token_type is not None:
        body["token_type"] = token_type

    tokens = connect.parse_token_response(
        _response(200, body), any_token_type=any_token_type
    )

    assert (tokens is not None) is parsed
    if parsed:
        assert tokens.access.reveal() == "test-upstream-at-not-real"


# --- The organization's app is required, and is the only client --------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestOnlyTheOrganizationsApp:
    def test_without_the_app_the_sign_in_waits_for_an_owner_or_admin(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        vault.revoke_oauth_client(_org_app_row(organization), user)

        row = flow.listed(auth_client, CHAT)
        started = _start(auth_client)

        assert row["start"] == REQUIRED
        assert row["start_url"] is None
        body = _refused(started, 409, "connector_oauth_org_client_required")
        assert "Owner or Admin" in body["message"]
        assert not AgentccMCPConnectState.objects.exists()
        assert vendor.requests == []

    def test_the_platform_client_is_never_read(
        self, auth_client, organization, user, vendor, monkeypatch
    ):
        monkeypatch.setenv(PLATFORM_ENV[0], PLATFORM_ID)
        monkeypatch.setenv(PLATFORM_ENV[1], PLATFORM_SECRET)
        reads = flow.platform_reads(monkeypatch)
        _configured(auth_client)
        registration = _org_app_row(organization)
        vault.revoke_oauth_client(registration, user)

        refused = _start(auth_client)
        _register(organization, user, CLIENT_ID, CLIENT_SECRET)
        _sign_in(auth_client, vendor)
        _expire_soon(organization, user)
        flow.fetch(organization, user, CHAT)
        gone = _disconnect(auth_client)

        _refused(refused, 409, "connector_oauth_org_client_required")
        assert gone.status_code == 200
        assert reads == []
        assert {a[2] for a in vendor.client_auths} == {CLIENT_ID}

    def test_a_replaced_app_makes_members_sign_in_again(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        _replace(organization, user)
        _expire_soon(organization, user)
        before = list(vendor.client_auths)

        fetched = flow.fetch(organization, user, CHAT)
        sent = list(vendor.client_auths)
        row = flow.listed(auth_client, CHAT)
        url, page = _sign_in(auth_client, vendor)

        assert fetched["error"] == "needs_reauth"
        assert "access_token" not in fetched
        assert vendor.refreshes == []
        assert sent == before
        assert row["connected"] is False
        assert _query(url)["client_id"] == REPLACEMENT_ID
        assert page.status_code == 200, page.content
        assert _auths(vendor, "token")[-1] == ("token", "post", REPLACEMENT_ID, True)

    def test_a_removed_app_makes_members_sign_in_again(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        vault.revoke_oauth_client(_org_app_row(organization), user)
        _expire_soon(organization, user)

        fetched = flow.fetch(organization, user, CHAT)
        row = flow.listed(auth_client, CHAT)

        assert fetched["error"] == "needs_reauth"
        assert vendor.refreshes == []
        assert row["connected"] is False
        assert row["start"] == REQUIRED

    def test_an_app_replaced_during_a_sign_in_never_exchanges_its_code(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        url, name, cookie = _started(auth_client)
        _replace(organization, user)

        page, _ = _callback(vendor, url, name, cookie)

        assert page.status_code == 400
        assert b"OAuth app changed" in page.content
        assert vendor.client_auths == []
        assert not AgentccMCPCredential.objects.filter(kind="oauth").exists()


# --- Disconnect ----------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestDisconnect:
    def test_the_grant_is_revoked_at_the_declared_endpoint_as_the_app(
        self, auth_client, organization, user, vendor, purges
    ):
        _configured(auth_client)
        _sign_in(auth_client, vendor)

        gone = _disconnect(auth_client)

        assert gone.status_code == 200, gone.json()
        assert gone.json()["result"]["upstream_revocation"] == "confirmed"
        (revoked,) = vendor.revoked
        assert revoked["token"] == vendor.issued[0]["refresh_token"]
        assert _auths(vendor, "revoke") == [("revoke", "post", CLIENT_ID, True)]
        assert flow.account(organization, user, CHAT).status == "revoked"
        assert (str(organization.pk), CHAT, str(user.pk)) in purges
        assert _gets(vendor) == []

    def test_a_provider_without_a_revocation_endpoint_is_unconfirmed(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client, MEET, MEET_URL)
        _sign_in(auth_client, vendor, MEET)

        gone = _disconnect(auth_client, MEET)

        assert gone.status_code == 200, gone.json()
        row = flow.account(organization, user, MEET)
        assert (row.status, row.oauth_revoke_outcome) == ("revoked", "unconfirmed")
        assert _auths(vendor, "revoke") == []
        assert _gets(vendor) == []

    def test_nothing_is_sent_once_the_app_is_replaced(
        self, auth_client, organization, user, vendor
    ):
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        _replace(organization, user)

        gone = _disconnect(auth_client)

        assert gone.status_code == 200, gone.json()
        assert gone.json()["result"]["upstream_revocation"] == "pending"
        assert _auths(vendor, "revoke") == []
        assert vendor.revoked == []

    def test_nothing_is_discovered_while_the_declared_server_cannot_be_read(
        self, auth_client, organization, user, vendor, monkeypatch
    ):
        """The organization's secret goes only to the endpoint the provider
        declares, never to one read from the issuer's metadata."""
        _configured(auth_client)
        _sign_in(auth_client, vendor)
        monkeypatch.setattr(org_app, "declared", lambda entry: None)

        gone = _disconnect(auth_client)

        assert gone.status_code == 200, gone.json()
        assert gone.json()["result"]["upstream_revocation"] == "pending"
        assert _gets(vendor) == []
        assert _auths(vendor, "revoke") == []
        assert vendor.revoked == []
        row = flow.account(organization, user, CHAT)
        assert (row.status, row.oauth_revoke_pending) == ("revoked", True)


# --- One organization -----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestOneOrganization:
    def test_another_organizations_app_never_signs_this_one_in(
        self, auth_client, organization, user, vendor
    ):
        other = Organization.objects.create(name="Another Organization")
        vault.create_oauth_client(
            other,
            client_group=CHAT,
            client_id=OTHER_ID,
            client_secret=vault.SealedSecret(OTHER_SECRET),
            actor=None,
        )
        _configured(auth_client)
        vault.revoke_oauth_client(_org_app_row(organization), user)

        started = _start(auth_client)

        _refused(started, 409, "connector_oauth_org_client_required")
        assert vendor.requests == [] and vendor.client_auths == []

    def test_a_token_is_served_only_in_its_own_organization(
        self, auth_client, organization, user, vendor
    ):
        other = Organization.objects.create(name="Another Organization")
        _seed(other, {"enabled": True})
        _configured(auth_client)
        _sign_in(auth_client, vendor)

        mine = flow.fetch(organization, user, CHAT)
        theirs = flow.fetch(other, user, CHAT)

        assert mine["access_token"] == vendor.issued[0]["access_token"]
        assert "access_token" not in theirs


# --- No secret leaves the vault -------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
def test_no_client_secret_or_token_reaches_a_log_a_response_or_the_config(
    auth_client, organization, user, vendor, pushes
):
    with capture_logs() as logs:
        configured = _configure(auth_client)
        read = auth_client.get(_oauth_root(CHAT))
        card = auth_client.get(f"{CATALOG}{CHAT}/")
        url, page = _sign_in(auth_client, vendor)
        rows = auth_client.get(LIST)
        fetched = flow.fetch(organization, user, CHAT)
        gone = _disconnect(auth_client)

    assert configured.status_code == 201, configured.json()
    assert fetched["access_token"] == vendor.issued[0]["access_token"]
    issued = [v for t in vendor.issued for k, v in t.items() if k.endswith("_token")]
    responses = "".join(
        r.content.decode() for r in (configured, read, card, page, rows, gone)
    )
    stored = json.dumps(_active(organization).mcp) + json.dumps(pushes)
    text = json.dumps(logs, default=str) + url + responses + stored
    assert issued
    assert [s for s in SECRETS + tuple(issued) if s in text] == []
    assert [s for s in SECRETS if s in json.dumps(fetched)] == []


# --- The shipped catalog --------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("drift_free")
def test_the_shipped_preregistered_vendor_apps_sign_in_with_the_organizations_app(
    auth_client,
):
    """The real Django copy: Slack, HubSpot, Asana, Zoom, Docusign and
    Salesforce are no longer Coming soon, and every vendor MCP provider that
    takes only a pre-registered client (outside the pilot and Microsoft groups)
    is a per-person sign-in after Configure OAuth."""
    loaded = catalog.load_bundle()
    for app_id, provider_id in sorted(REAL_ORG_APP):
        plan = loaded.apps[app_id].plans[provider_id]
        assert isinstance(plan, catalog.Plan), (app_id, provider_id, plan)
        assert plan.credential.kind == "oauth", (app_id, provider_id)
        assert plan.modes == ("user",), (app_id, provider_id)
    for app_id, provider_id in REAL_UNCHANGED:
        plan = loaded.apps[app_id].plans[provider_id]
        assert isinstance(plan, catalog.Unavailable), (app_id, provider_id)
    body = _ok(auth_client.get(CATALOG, {"limit": 200}))
    rows = {app["id"]: app for app in body["results"]}
    for app_id in sorted(REAL_NO_LONGER_COMING_SOON):
        assert rows[app_id].get("status") != "coming_soon", app_id
        assert rows[app_id]["default_provider"] is not None, app_id
    for app_id, provider_id in sorted(REAL_ORG_APP):
        providers = {p["id"]: p for p in rows[app_id]["providers"]}
        assert providers[provider_id]["connect"] == CONFIGURE_OAUTH, app_id
        assert providers[provider_id]["account_modes"] == ["user"], app_id
