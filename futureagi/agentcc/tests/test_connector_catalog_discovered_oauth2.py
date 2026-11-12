"""Sign-in for vendor MCP servers whose catalog record declares "oauth2" and
takes dynamic client registration (dcr) or a client metadata document (cimd).

Such a server needs no app of ours, so it signs in like "mcp_oauth": its card
asks for Configure OAuth first, then each person signs in with their own
account. A record that takes pre-registered clients only signs in with the
organization's own OAuth app instead (test_connector_org_app_oauth); a pilot
connector or pilot group, and the Microsoft group keep their refusal. Only
synthetic discovery and authorization server traffic (FakeVendor); no vendor
is called.
"""

import pytest

from agentcc.services import connector_catalog as catalog
from agentcc.services import mcp_upstream_oauth as oauth
from agentcc.services.connector_mcp_oauth_app import is_catalog_oauth
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.tests.mcp_upstream_oauth_helpers import (
    CALLBACK,
    ISSUER,
    SERVER_URL,
    FakeVendor,
)
from agentcc.tests.test_connector_catalog import (
    CATALOG,
    _active,
    _add,
    _app,
    _bundle,
    _header_key,
    _listed,
    _ok,
    _provider,
    _refused,
    _seed,
    _versions,
)

bundle = fixtures.bundle
drift_free = fixtures.drift_free
probe = fixtures.probe
probe_budget = fixtures.probe_budget
pushes = fixtures.pushes

# ClickUp-like: dcr only, its one provider.
APP = "clink-tasks"
# Notion-like: cimd and dcr, beside a key provider.
NOTES = "nimbus-notes"
# Slack-like: pre-registered clients only.
CHAT = "slab-chat"
# Gmail google-mcp-like, with dcr: a K2 pilot app.
K2_APP = "gmail"
# A K2 client group, with dcr.
K2_GROUP_APP = "glint-sheets"
# The Microsoft group, with dcr.
MICROSOFT_APP = "microsoft-loop"
# Discovered MCP OAuth as it is served today.
MCP_OAUTH_APP = "slate-docs"

ROOT = f"/agentcc/connectors/apps/{APP}/oauth/"
SECRET = "synthetic-client-secret-not-a-real-credential"
CONFIGURE_OAUTH = {"served": True, "configure_oauth": True}
# Every provider of the bundle that plans as a per-person sign-in: discovered,
# and Slab Chat's with the organization's own OAuth app.
SIGNS_IN = {(APP, "mcp"), (NOTES, "mcp"), (MCP_OAUTH_APP, "vendor-mcp"), (CHAT, "mcp")}
K2_AND_MICROSOFT = [
    (K2_APP, "google-mcp"),
    (K2_GROUP_APP, "mcp"),
    (MICROSOFT_APP, "mcp"),
]


def _oauth2(registration, **extra):
    spec = {
        "authorizeUrl": ISSUER + "/authorize",
        "tokenUrl": ISSUER + "/token",
        "scopes": [],
        "pkce": True,
        "tokenBody": "form",
        "clientAuth": "body",
        "registration": registration,
    }
    spec.update(extra)
    return {"kind": "oauth2", "oauth2": spec}


def _sign_in(app_id, provider_id, registration, *, url=None, **oauth2):
    return _provider(
        app_id,
        provider_id,
        auth=_oauth2(registration, **oauth2),
        tools=[],
        account_modes=["agent", "user"],
        transport={
            "kind": "http",
            "url": url or f"https://mcp.{app_id}.example.com/mcp",
        },
    )


def _synthetic_app(app_id, name, default):
    return _app(
        app_id,
        name,
        f"Synthetic {name} app.",
        category="productivity",
        categories=["docs"],
        default=default,
    )


@pytest.fixture
def vendor(bundle, user, monkeypatch, settings, pushes):
    """The synthetic catalog, and the vendor every discovery reaches."""
    data = _bundle()
    data["apps"] = [
        _synthetic_app(APP, "Clink Tasks", "mcp"),
        _synthetic_app(NOTES, "Nimbus Notes", "mcp"),
        _synthetic_app(CHAT, "Slab Chat", "mcp"),
        _synthetic_app(K2_APP, "Gmail", "google-mcp"),
        _synthetic_app(K2_GROUP_APP, "Glint Sheets", "mcp"),
        _synthetic_app(MICROSOFT_APP, "Microsoft Loop", "mcp"),
        _synthetic_app(MCP_OAUTH_APP, "Slate Docs", "vendor-mcp"),
    ]
    data["providers"] = [
        _sign_in(APP, "mcp", ["dcr"], url=SERVER_URL),
        _sign_in(NOTES, "mcp", ["cimd", "dcr"]),
        _provider(
            NOTES,
            "mcp-token",
            auth=_header_key("Authorization", prefix="Bearer "),
            tools=[],
        ),
        _sign_in(CHAT, "mcp", ["preregistered"]),
        _sign_in(
            K2_APP, "google-mcp", ["cimd", "dcr"], clientGroup="google-workspace-mcp"
        ),
        _sign_in(K2_GROUP_APP, "mcp", ["dcr"], clientGroup="google"),
        _sign_in(MICROSOFT_APP, "mcp", ["dcr"]),
        _provider(
            MCP_OAUTH_APP,
            "vendor-mcp",
            auth={"kind": "mcp_oauth"},
            tools=[],
            account_modes=["agent", "user"],
        ),
    ]
    bundle(data)
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    monkeypatch.setenv(
        "AGENTCC_MCP_DJANGO_SECRET", "synthetic-internal-channel-secret-for-unit-tests"
    )
    fake = FakeVendor(cimd=False, dcr=True)
    monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", fake.fetch)
    _seed(user.organization, {"enabled": True})
    return fake


def _plans():
    return {
        (app_id, provider_id): plan
        for app_id, entry in catalog.load_bundle().apps.items()
        for provider_id, plan in entry.plans.items()
    }


def _signs_in(plan):
    return isinstance(plan, catalog.Plan) and plan.credential.kind == "oauth"


def _rows(client):
    body = _ok(client.get(CATALOG, {"limit": 200}))
    return {app["id"]: app for app in body["results"]}


def _providers(app):
    return {provider["id"]: provider for provider in app["providers"]}


@pytest.mark.django_db
@pytest.mark.usefixtures("vendor", "drift_free", "probe_budget")
class TestDiscoveredOAuth2SignIn:
    def test_a_dcr_only_record_is_listed_with_a_sign_in_card(self, auth_client):
        plan = _plans()[(APP, "mcp")]
        assert isinstance(plan, catalog.Plan), plan
        assert (plan.credential.mode, plan.credential.kind) == ("per_user", "oauth")
        assert plan.modes == ("agent", "user")
        assert APP in _listed(auth_client)
        app = _ok(auth_client.get(f"{CATALOG}{APP}/"))["result"]
        assert app["default_provider"] == "mcp"
        (provider,) = app["providers"]
        assert provider["id"] == "mcp"
        assert provider["connect"] == CONFIGURE_OAUTH
        assert provider["credential"]["mode"] == "per_user"
        assert provider["credential"]["kind"] == "oauth"
        assert provider["account_modes"] == ["agent", "user"]
        assert provider["tools"] == []
        # The sign-in needs Configure OAuth first.
        assert _ok(auth_client.get(ROOT))["result"]["configured"] is False

    @pytest.mark.parametrize("registration", ["dcr", "manual"])
    def test_after_configure_oauth_each_person_signs_in(
        self, auth_client, user, vendor, registration
    ):
        org = user.organization
        body = {
            "provider": "mcp",
            "discovery_url": SERVER_URL,
            "mode": "per_user",
            "registration": registration,
        }
        if registration == "manual":
            vendor.confidential = {"synthetic-client": SECRET}
            body.update(client_id="synthetic-client", client_secret=SECRET)
        result = _ok(auth_client.post(ROOT, body, format="json"), 201)["result"]
        assert result["configured"] is True
        assert result["registration"] == registration
        server = _active(org).mcp["servers"][APP]
        assert server["catalog_ref"] == _plans()[(APP, "mcp")].catalog_ref
        assert server["credential"] == {"mode": "per_user"}
        assert is_catalog_oauth(server)
        # Not held back: each person's sign-in starts from the server's URL.
        assert oauth.per_user_server_url(org.pk, APP) == SERVER_URL
        chosen = oauth.choose_client(org, APP, oauth.discover(SERVER_URL))
        assert chosen.kind == ("dcr" if registration == "dcr" else "org")

    def test_a_cimd_and_dcr_record_offers_sign_in_beside_its_key_provider(
        self, auth_client
    ):
        app = _ok(auth_client.get(f"{CATALOG}{NOTES}/"))["result"]
        providers = _providers(app)
        assert set(providers) == {"mcp", "mcp-token"}
        assert providers["mcp"]["connect"] == CONFIGURE_OAUTH
        assert providers["mcp"]["credential"]["kind"] == "oauth"
        key = providers["mcp-token"]["credential"]
        assert (key["mode"], key["kind"], key["in"], key["name"]) == (
            "shared",
            "bearer",
            "header",
            "Authorization",
        )
        assert providers["mcp-token"]["account_modes"] == ["agent"]
        assert app["default_provider"] == "mcp"

    def test_a_preregistered_only_record_needs_the_organizations_own_app(
        self, auth_client, user, probe, pushes
    ):
        plans = _plans()
        assert {ref for ref, plan in plans.items() if _signs_in(plan)} == SIGNS_IN
        plan = plans[(CHAT, "mcp")]
        assert isinstance(plan, catalog.Plan), plan
        assert plan.modes == ("user",)
        # Listed with its sign-in card, never Coming soon; never discovered.
        chat = _rows(auth_client)[CHAT]
        assert chat.get("status") != "coming_soon"
        assert _providers(chat)["mcp"]["connect"] == CONFIGURE_OAUTH
        before = _versions(user.organization)
        _refused(
            auth_client.post(
                _add(CHAT), {"provider": "mcp", "mode": "user"}, format="json"
            ),
            409,
            "oauth_configuration_required",
        )
        _refused(
            auth_client.post(
                f"/agentcc/connectors/apps/{CHAT}/oauth/",
                {
                    "provider": "mcp",
                    "discovery_url": f"https://mcp.{CHAT}.example.com/mcp",
                    "mode": "per_user",
                    "registration": "dcr",
                },
                format="json",
            ),
            409,
            "dcr_unavailable",
        )
        assert _versions(user.organization) == before
        assert probe.calls == [] and pushes == []

    @pytest.mark.parametrize("ref", K2_AND_MICROSOFT)
    def test_k2_and_microsoft_records_are_unchanged(self, auth_client, ref):
        plans = _plans()
        assert {r for r, plan in plans.items() if _signs_in(plan)} == SIGNS_IN
        plan = plans[ref]
        assert isinstance(plan, catalog.Unavailable), plan
        assert plan.code == catalog.OAUTH_NOT_BUILT
        assert not catalog._mcp_oauth(catalog.load_bundle().providers[ref])
        app_id, provider_id = ref
        listed = _rows(auth_client).get(app_id, {"providers": []})
        assert provider_id not in _providers(listed)

    def test_upstream_oauth_off_hides_the_admitted_record_like_mcp_oauth(
        self, auth_client, settings
    ):
        on = _rows(auth_client)
        assert {APP, NOTES, MCP_OAUTH_APP} <= set(on)
        assert set(_providers(on[NOTES])) == {"mcp", "mcp-token"}
        settings.AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED = False
        off = _rows(auth_client)
        # With the flag off their only plan is hidden, so each is a Coming soon
        # card with nothing to connect (336ec9f5ef), and its detail and OAuth
        # routes still answer 404 (MCPSubfeatureMiddleware).
        for app_id in (APP, MCP_OAUTH_APP):
            card = off[app_id]
            assert card["status"] == "coming_soon", app_id
            assert card["providers"] == [] and card["default_provider"] is None, app_id
        assert set(_providers(off[NOTES])) == {"mcp-token"}
        for app_id in (APP, MCP_OAUTH_APP):
            for path in (
                f"{CATALOG}{app_id}/",
                f"/agentcc/connectors/apps/{app_id}/oauth/",
            ):
                _refused(auth_client.get(path), 404, "not_found")

    @pytest.mark.parametrize("app_id", [APP, NOTES])
    def test_add_before_configure_oauth_is_refused(
        self, auth_client, user, probe, pushes, app_id
    ):
        before = _versions(user.organization)
        _refused(
            auth_client.post(
                _add(app_id), {"provider": "mcp", "mode": "user"}, format="json"
            ),
            409,
            "oauth_configuration_required",
        )
        assert _versions(user.organization) == before
        assert app_id not in _active(user.organization).mcp.get("servers", {})
        assert probe.calls == [] and pushes == []
