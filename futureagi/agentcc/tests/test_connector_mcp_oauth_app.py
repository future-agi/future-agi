"""OAuth app registration from the catalogue; only synthetic discovery/AS traffic."""

import copy
import json
from pathlib import Path

import pytest

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamClient
from agentcc.services import config_push
from agentcc.services import connector_catalog as catalog
from agentcc.services import mcp_upstream_oauth as oauth
from agentcc.tests.mcp_upstream_oauth_helpers import CALLBACK, SERVER_URL, FakeVendor
from agentcc.tests.test_connector_catalog import (  # noqa: F401 (fixtures)
    _active,
    _app,
    _bundle,
    _client_for,
    _ok,
    _provider,
    _seed,
    _tool,
)
from agentcc.tests.test_connector_catalog import (
    bundle as bundle,
)
from agentcc.tests.test_connector_catalog import (
    drift_free as drift_free,
)
from agentcc.tests.test_connector_catalog import (
    probe_budget as probe_budget,
)
from agentcc.tests.test_connector_catalog import (
    pushes as pushes,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

APP = "slate-docs"
ROOT = f"/agentcc/connectors/apps/{APP}/oauth/"
SECRET = "synthetic-client-secret-not-a-real-credential"


@pytest.fixture
def setup_oauth(bundle, user, monkeypatch, settings, pushes):
    data = _bundle()
    data["apps"] = [
        _app(
            APP,
            "Slate Docs",
            "Synthetic MCP app.",
            category="productivity",
            categories=["docs"],
            default="vendor-mcp",
        )
    ]
    data["providers"] = [
        _provider(
            APP,
            "vendor-mcp",
            auth={"kind": "mcp_oauth"},
            tools=[_tool("search", posture="auto"), _tool("delete", read_only=False)],
            account_modes=["user", "agent"],
            transport={"kind": "http", "url": SERVER_URL},
        )
    ]
    bundle(data)
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    monkeypatch.setenv(
        "AGENTCC_MCP_DJANGO_SECRET", "synthetic-internal-channel-secret-for-unit-tests"
    )
    vendor = FakeVendor(cimd=False, dcr=True)
    monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", vendor.fetch)
    _seed(user.organization, {"enabled": True})
    return vendor


def body(**changes):
    return {
        "provider": "vendor-mcp",
        "discovery_url": SERVER_URL,
        "mode": "per_user",
        "registration": "dcr",
        **changes,
    }


@pytest.mark.django_db
@pytest.mark.usefixtures("setup_oauth", "drift_free", "probe_budget")
class TestOAuthApp:
    def test_catalog_exposes_configurable_mcp_oauth(self, auth_client):
        app = _ok(auth_client.get("/agentcc/connectors/catalog/"))["results"][0]
        assert app["providers"][0]["connect"] == {
            "served": True,
            "configure_oauth": True,
        }
        assert app["providers"][0]["account_modes"] == ["user", "agent"]

    def test_discovery_is_read_only_and_does_not_register(
        self, auth_client, setup_oauth, user
    ):
        before = _active(user.organization).version
        result = _ok(
            auth_client.post(
                ROOT + "discovery/",
                {"provider": "vendor-mcp", "discovery_url": SERVER_URL},
                format="json",
            )
        )["result"]
        assert result["dcr_supported"] is True
        assert result["pkce_s256"] is True
        assert result["redirect_uri"] == CALLBACK
        assert setup_oauth.registrations == []
        assert _active(user.organization).version == before
        assert not AgentccMCPUpstreamClient.objects.exists()

    @pytest.mark.parametrize("mode", ["shared", "per_user"])
    def test_registers_dcr_and_adds_server_in_selected_mode(
        self, auth_client, user, setup_oauth, mode, pushes
    ):
        result = _ok(auth_client.post(ROOT, body(mode=mode), format="json"), 201)[
            "result"
        ]
        assert result["mode"] == mode
        assert result["registration"] == "dcr"
        assert result["configured"] is True
        assert result["has_secret"] is False
        assert len(setup_oauth.registrations) == 1
        active = _active(user.organization)
        expected = {"mode": mode}
        if mode == "shared":
            expected["kind"] = "oauth"
        # Only the gateway transport is faked: the org writer, enforcement and
        # payload validation all run, for both supported credential shapes.
        assert active.mcp["servers"][APP]["credential"] == expected
        assert len(pushes) == 1
        sent = pushes[0][1]["mcp"]["servers"][APP]["credential"]
        assert sent["mode"] == mode
        assert sent.get("kind") == expected.get("kind")
        assert active.mcp["virtual_servers"][APP]["tools"]["include"] == [
            f"{APP}/search"
        ]
        assert (
            f"{APP}/delete"
            in active.mcp["virtual_servers"][APP]["access"]["rules"][0]["deny"]
        )
        # Runtime recognizes exactly this catalog-backed MCP OAuth server.
        if mode == "per_user":
            assert oauth.per_user_server_url(user.organization.pk, APP) == SERVER_URL
        selected = oauth.choose_client(
            user.organization, APP, oauth.discover(SERVER_URL)
        )
        assert selected.kind == "dcr"
        reread = _ok(auth_client.get(ROOT))["result"]
        assert reread["configured"] is True
        assert reread["mode"] == mode

    @pytest.mark.parametrize("mode", ["shared", "per_user"])
    def test_manual_secret_is_encrypted_write_only_and_used_by_runtime(
        self, auth_client, user, setup_oauth, mode
    ):
        setup_oauth.confidential = {"synthetic-client": SECRET}
        response = auth_client.post(
            ROOT,
            body(
                mode=mode,
                registration="manual",
                client_id="synthetic-client",
                client_secret=SECRET,
            ),
            format="json",
        )
        result = _ok(response, 201)["result"]
        assert result["has_secret"] is True
        row = AgentccMCPCredential.objects.get(
            organization=user.organization, kind="oauth_client"
        )
        assert SECRET not in row.encrypted_secret
        assert SECRET not in response.content.decode()
        assert SECRET not in auth_client.get(ROOT).content.decode()
        assert (
            SECRET
            not in auth_client.get("/agentcc/connectors/catalog/").content.decode()
        )
        assert setup_oauth.registrations == []
        chosen = oauth.choose_client(user.organization, APP, oauth.discover(SERVER_URL))
        assert chosen.client_id == "synthetic-client"
        assert chosen.kind == "org"

    @pytest.mark.parametrize(
        "level,role",
        [
            (Level.MEMBER, OrganizationRoles.MEMBER),
            (Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY),
        ],
    )
    def test_members_and_viewers_can_read_but_not_configure_or_discover(
        self, user, workspace, level, role, setup_oauth
    ):
        client, _ = _client_for(user.organization, workspace, level=level, role=role)
        try:
            assert client.get(ROOT).status_code == 200
            for path in (ROOT, ROOT + "discovery/"):
                assert client.post(path, body(), format="json").status_code == 403
            assert setup_oauth.requests == []
        finally:
            client.stop_workspace_injection()

    def test_another_org_has_no_configuration_or_client(self, auth_client, user):
        _ok(
            auth_client.post(
                ROOT,
                body(
                    registration="manual",
                    client_id="synthetic-client",
                    client_secret=SECRET,
                ),
                format="json",
            ),
            201,
        )
        from agentcc.services.connector_mcp_oauth_app import configuration

        other = Organization.objects.create(name="Synthetic other organization")
        assert configuration(other, APP)["configured"] is False
        assert configuration(other, APP)["has_secret"] is False

    @pytest.mark.parametrize(
        "changes",
        [
            {"registration": "manual"},
            {"client_secret": SECRET},
            {"mode": "user"},
            {"organization_id": "foreign"},
            {"discovery_url": "https://other.invalid/mcp"},
        ],
    )
    def test_invalid_input_writes_nothing(self, auth_client, user, changes):
        before = _active(user.organization).pk
        response = auth_client.post(ROOT, body(**changes), format="json")
        assert response.status_code == 400
        assert SECRET not in response.content.decode()
        assert _active(user.organization).pk == before
        assert not AgentccMCPCredential.objects.filter(
            organization=user.organization
        ).exists()

    @pytest.mark.parametrize("failure", ["issuer", "pkce", "dcr"])
    def test_discovery_and_registration_fail_closed(
        self, auth_client, user, setup_oauth, failure
    ):
        if failure == "issuer":
            setup_oauth.issuer = "https://wrong.invalid"
        elif failure == "pkce":
            setup_oauth.s256 = False
        else:
            setup_oauth.dcr = False
        before = _active(user.organization).pk
        assert auth_client.post(ROOT, body(), format="json").status_code in (409, 502)
        assert _active(user.organization).pk == before

    def test_off_is_not_enabled_by_configuration(self, auth_client, user, setup_oauth):
        active = _active(user.organization)
        active.mcp = {"enabled": False}
        active.save(update_fields=["mcp"])
        assert auth_client.post(ROOT, body(), format="json").status_code == 409
        assert _active(user.organization).mcp == {"enabled": False}
        assert setup_oauth.requests == []

    def test_duplicate_does_not_overwrite_mode_or_client(
        self, auth_client, user, setup_oauth
    ):
        _ok(auth_client.post(ROOT, body(), format="json"), 201)
        before = copy.deepcopy(_active(user.organization).mcp)
        assert (
            auth_client.post(ROOT, body(mode="shared"), format="json").status_code
            == 409
        )
        assert _active(user.organization).mcp == before
        assert len(setup_oauth.registrations) == 1

    @pytest.mark.parametrize("mode", ["shared", "per_user"])
    def test_rejected_config_does_not_register_a_client(
        self, auth_client, user, setup_oauth, monkeypatch, pushes, mode
    ):
        def reject(config):
            raise config_push.OrgConfigRejected("Synthetic validation refusal")

        monkeypatch.setattr(config_push, "validate_org_config", reject)
        before = _active(user.organization).pk
        assert auth_client.post(ROOT, body(mode=mode), format="json").status_code == 400
        assert _active(user.organization).pk == before
        assert setup_oauth.registrations == []
        assert not AgentccMCPUpstreamClient.objects.exists()
        assert pushes == []

    @pytest.mark.parametrize("posture", ["ask", "auto", "deny"])
    def test_oauth_configuration_does_not_require_declared_allowed_tools(
        self, auth_client, user, bundle, setup_oauth, posture
    ):
        data = json.loads(catalog.BUNDLE_PATH.read_text())
        data["providers"][0]["tools"] = {
            "source": "upstream_mcp",
            "defaultPosture": posture,
        }
        bundle(data)
        app = _ok(auth_client.get(f"/agentcc/connectors/catalog/{APP}/"))["result"]
        assert app["providers"][0]["connect"]["configure_oauth"] is True
        assert app["providers"][0]["tools"] == []
        before_access = copy.deepcopy(_active(user.organization).mcp.get("access"))
        _ok(auth_client.post(ROOT, body(), format="json"), 201)
        active = _active(user.organization)
        assert active.mcp.get("access") == before_access
        assert active.mcp["virtual_servers"][APP]["tools"] == {"include": []}
        assert "access" not in active.mcp["virtual_servers"][APP]
        assert len(setup_oauth.registrations) == 1

    def test_real_calendly_catalogue_reaches_configuration(
        self, auth_client, user, bundle, setup_oauth, monkeypatch
    ):
        shipped = Path(__file__).resolve().parents[1] / "connector_catalog/catalog.json"
        # First prove the unmodified shipped catalogue exposes OAuth configuration.
        monkeypatch.setattr(catalog, "BUNDLE_PATH", shipped)
        app = _ok(auth_client.get("/agentcc/connectors/catalog/calendly/"))["result"]
        provider = next(p for p in app["providers"] if p["id"] == "mcp")
        assert provider["connect"]["configure_oauth"] is True
        assert provider["tools"] == []
        root = "/agentcc/connectors/apps/calendly/oauth/"
        assert _ok(auth_client.get(root))["result"]["configured"] is False
        assert setup_oauth.requests == []

        # Keep the actual catalogue records, changing only the network target to
        # the synthetic fixture. Never send discovery to the real vendor.
        data = json.loads(shipped.read_text())
        data["apps"] = [a for a in data["apps"] if a["id"] == "calendly"]
        data["providers"] = [
            p for p in data["providers"] if p["app"] == "calendly" and p["id"] == "mcp"
        ]
        data["providers"][0]["transport"]["url"] = SERVER_URL
        bundle(data)
        result = _ok(auth_client.post(root, body(provider="mcp"), format="json"), 201)[
            "result"
        ]
        assert result["configured"] is True
        server = _active(user.organization).mcp["servers"]["calendly"]
        assert server["catalog_ref"] == provider["catalog_ref"]
        assert server["credential"] == {"mode": "per_user"}
        assert len(setup_oauth.registrations) == 1
