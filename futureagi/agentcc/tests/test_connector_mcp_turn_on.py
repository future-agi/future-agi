"""Connecting an app, or saving a connector's OAuth app, while MCP is off for
the organization.

One server rule decides it for the three saves (the key add, the OAuth
sign-in add and the OAuth app save): someone who may turn MCP on (an Owner or
Admin, the right the console's own switch needs) turns it on with the save,
whether the organization never set MCP, set it off, or is off with servers
already saved; anyone else is refused before anything is written. Because the
three saves share the rule, the order they run in does not change the result.
Every value is synthetic.
"""

import copy
import json
from types import SimpleNamespace

import pytest

from agentcc import permissions
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamClient
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_mcp_oauth_app as apps
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.tests import test_connector_mcp_oauth_app as oauth_app
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

bundle = fixtures.bundle
drift_free = fixtures.drift_free
probe = fixtures.probe
probe_budget = fixtures.probe_budget
pushes = fixtures.pushes
setup_oauth = oauth_app.setup_oauth
k2_settings = flow.k2_settings
servers = flow.servers
k2_bundle = flow.k2_bundle
runtime = flow.runtime
purges = flow.purges

CODE = "connector_mcp_turn_on_not_allowed"
SENTENCE = "MCP is off for this organization. Ask an owner or admin to turn it on."
WIKI = {"url": "https://wiki.example.com/mcp"}
# The three states in which MCP is off for an organization; None is an
# organization that never saved an mcp section.
OFF = {
    "never set": None,
    "set off": {"enabled": False},
    "off with servers saved": {"enabled": False, "servers": {"wiki": WIKI}},
}
SIGN_IN_ADD = "/agentcc/connectors/apps/gmail/add/"
SIGN_IN_BODY = {"provider": "futureagi-rest", "mode": "user"}


def _set_mcp(org, mcp):
    """Save ``mcp`` as the organization's mcp section (None: none saved)."""
    active = AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()
    if active is None:
        if mcp is not None:
            fixtures._seed(org, copy.deepcopy(mcp))
        return
    active.mcp = {} if mcp is None else copy.deepcopy(mcp)
    active.save(update_fields=["mcp"])


def _kept_servers(org, state):
    """The servers saved before the save are still saved after it."""
    saved = (OFF[state] or {}).get("servers", {})
    current = fixtures._active(org).mcp["servers"]
    for server_id, entry in saved.items():
        assert current[server_id] == entry


def _person(org, workspace, level, role):
    """A member of ``org`` at ``level``, for a call made at the service."""
    client, person = fixtures._client_for(org, workspace, level=level, role=role)
    client.stop_workspace_injection()
    return person


def _may_not_turn_on(monkeypatch):
    """A role that may save connectors but not turn MCP on. No such role
    exists today (every save and the switch need an Owner or Admin), so the
    rule's own permission check is replaced by a double that says no, and the
    views' admin check lets the request through to the rule."""
    monkeypatch.setattr(
        permissions, "may_change_gateway", lambda user, organization: False
    )
    monkeypatch.setattr(
        permissions.IsOrgGatewayAdmin,
        "has_permission",
        lambda self, request, view: True,
    )


@pytest.fixture(params=["Owner", "Admin"])
def admin(request, auth_client, user, workspace):
    """An Owner (the signed-in test user) or an Admin of the same organization."""
    if request.param == "Owner":
        yield auth_client
        return
    client, _ = fixtures._client_for(
        user.organization,
        workspace,
        level=Level.ADMIN,
        role=OrganizationRoles.ADMIN,
    )
    try:
        yield client
    finally:
        client.stop_workspace_injection()


# --- The rule -------------------------------------------------------------------


@pytest.mark.django_db
class TestTheRule:
    def test_the_turn_on_right_matters_only_while_mcp_is_off(self, user, workspace):
        from agentcc.services.connector_catalog import ConnectorForbidden, mcp_turn_on

        org = user.organization
        member = _person(org, workspace, Level.MEMBER, OrganizationRoles.MEMBER)
        for actor in (user, member):
            assert mcp_turn_on({"enabled": True, "servers": {}}, org, actor) is False
        for mcp in (
            {},
            {"enabled": False},
            {"enabled": False, "servers": {"wiki": WIKI}},
            {"servers": {"wiki": WIKI}},
            {"managed_tools": {"enabled": True}},
        ):
            assert mcp_turn_on(mcp, org, user) is True
            with pytest.raises(ConnectorForbidden) as refused:
                mcp_turn_on(mcp, org, member)
            assert refused.value.code == CODE
            assert str(refused.value) == SENTENCE

    @pytest.mark.parametrize(
        "level, role",
        [
            (Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY),
            (Level.MEMBER, OrganizationRoles.MEMBER),
            (Level.ADMIN, OrganizationRoles.ADMIN),
            (Level.OWNER, OrganizationRoles.OWNER),
        ],
    )
    def test_the_switch_and_the_saves_share_one_check(
        self, user, workspace, level, role
    ):
        from agentcc.permissions import may_change_gateway

        org = user.organization
        person = _person(org, workspace, level, role)
        request = SimpleNamespace(user=person, organization=org)
        assert may_change_gateway(person, org) is (level >= Level.ADMIN)
        assert permissions.is_gateway_admin(request) is may_change_gateway(person, org)
        assert may_change_gateway(person, None) is False
        assert may_change_gateway(None, org) is False


# --- The key add ----------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestKeyAdd:
    @pytest.mark.parametrize("state", OFF)
    def test_an_owner_or_admin_turns_mcp_on_in_every_off_state(
        self, admin, user, bundle, pushes, probe, state
    ):
        org = user.organization
        _set_mcp(org, OFF[state])
        before = fixtures._versions(org)
        result = fixtures._ok(
            admin.post(
                fixtures._add("acme-docs"),
                {"provider": "acme-mcp", **fixtures._key()},
                format="json",
            ),
            201,
        )["result"]
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is True
        mcp = fixtures._active(org).mcp
        assert mcp["enabled"] is True and "acme-docs" in mcp["servers"]
        _kept_servers(org, state)
        assert fixtures._versions(org) == before + 1
        assert pushes[-1][1]["mcp"]["enabled"] is True
        assert len(probe.calls) == 1

    def test_with_mcp_on_nothing_is_turned_on(self, admin, user, bundle, pushes, probe):
        org = user.organization
        fixtures._seed(org, {"enabled": True, "servers": {"wiki": WIKI}})
        result = fixtures._ok(
            admin.post(fixtures._add("cobalt-search"), {}, format="json"), 201
        )["result"]
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is False
        assert fixtures._active(org).mcp["servers"]["wiki"] == WIKI

    @pytest.mark.parametrize("state", OFF)
    def test_a_role_that_may_not_turn_mcp_on_is_refused_and_nothing_is_saved(
        self, auth_client, user, bundle, pushes, probe, monkeypatch, state
    ):
        org = user.organization
        _set_mcp(org, OFF[state])
        before = fixtures._versions(org)
        saved = copy.deepcopy(OFF[state])
        _may_not_turn_on(monkeypatch)
        response = auth_client.post(
            fixtures._add("acme-docs"),
            {"provider": "acme-mcp", **fixtures._key()},
            format="json",
        )
        body = fixtures._refused(response, 403, CODE)
        assert body["message"] == SENTENCE
        assert fixtures.SECRET not in response.content.decode()
        # Refused before the provider is called: nothing listed, stored,
        # saved or pushed.
        assert probe.calls == [] and pushes == []
        assert fixtures._versions(org) == before
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        if saved is not None:
            assert fixtures._active(org).mcp == saved

    def test_with_mcp_on_the_turn_on_right_is_not_needed(
        self, auth_client, user, bundle, pushes, probe, monkeypatch
    ):
        org = user.organization
        fixtures._seed(org, {"enabled": True})
        _may_not_turn_on(monkeypatch)
        result = fixtures._ok(
            auth_client.post(fixtures._add("cobalt-search"), {}, format="json"), 201
        )["result"]
        assert result["mcp_turned_on"] is False

    @pytest.mark.parametrize("state", OFF)
    def test_a_member_is_refused_at_the_service_before_the_provider_is_called(
        self, user, workspace, bundle, pushes, probe, state
    ):
        from agentcc.services.connector_catalog import ConnectorForbidden

        org = user.organization
        _set_mcp(org, OFF[state])
        member = _person(org, workspace, Level.MEMBER, OrganizationRoles.MEMBER)
        before = fixtures._versions(org)
        with pytest.raises(ConnectorForbidden) as refused:
            catalog.add_connector(
                org,
                "acme-docs",
                provider_id="acme-mcp",
                mode="agent",
                url_variables={},
                actor=member,
                credential=fixtures.SECRET,
            )
        assert refused.value.code == CODE and str(refused.value) == SENTENCE
        assert probe.calls == [] and pushes == []
        assert fixtures._versions(org) == before
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()


# --- The OAuth sign-in add ------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures(
    "k2_settings", "k2_bundle", "runtime", "purges", "probe_budget", "drift_free"
)
class TestSignInAdd:
    @pytest.mark.parametrize("state", OFF)
    def test_an_owner_turns_mcp_on_in_every_off_state(
        self, auth_client, organization, user, pushes, state
    ):
        _set_mcp(organization, OFF[state])
        flow.register(organization, user)
        added = auth_client.post(SIGN_IN_ADD, SIGN_IN_BODY, format="json")
        assert added.status_code == 201, added.json()
        result = added.json()["result"]
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is True
        mcp = fixtures._active(organization).mcp
        assert mcp["enabled"] is True and "gmail" in mcp["servers"]
        _kept_servers(organization, state)
        assert pushes[-1][1]["mcp"]["enabled"] is True

    @pytest.mark.parametrize("state", OFF)
    def test_a_member_is_refused_at_the_service_before_any_version(
        self, organization, user, workspace, pushes, state
    ):
        from agentcc.services.connector_catalog import ConnectorForbidden

        _set_mcp(organization, OFF[state])
        flow.register(organization, user)
        member = _person(
            organization, workspace, Level.MEMBER, OrganizationRoles.MEMBER
        )
        before = fixtures._versions(organization)
        with pytest.raises(ConnectorForbidden) as refused:
            catalog.add_connector(
                organization,
                "gmail",
                provider_id="futureagi-rest",
                mode="user",
                url_variables={},
                actor=member,
            )
        assert refused.value.code == CODE and str(refused.value) == SENTENCE
        assert fixtures._versions(organization) == before
        assert pushes == []


# --- The OAuth app save ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("setup_oauth", "drift_free", "probe_budget")
class TestOAuthAppSave:
    @pytest.mark.parametrize("state", OFF)
    def test_an_owner_or_admin_turns_mcp_on_in_every_off_state(
        self, admin, user, setup_oauth, pushes, state
    ):
        org = user.organization
        _set_mcp(org, OFF[state])
        result = fixtures._ok(
            admin.post(oauth_app.ROOT, oauth_app.body(), format="json"), 201
        )["result"]
        assert result["configured"] is True
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is True
        assert len(setup_oauth.registrations) == 1
        mcp = fixtures._active(org).mcp
        assert mcp["enabled"] is True and oauth_app.APP in mcp["servers"]
        _kept_servers(org, state)
        assert pushes[-1][1]["mcp"]["enabled"] is True

    def test_an_organization_with_no_saved_configuration(self, setup_oauth, pushes):
        other, owner = fixtures._other_org_owner("Synthetic OAuth app organization")
        result = apps.configure(other, oauth_app.APP, oauth_app.body(), owner)
        assert result["configured"] is True
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is True
        assert fixtures._active(other).mcp["enabled"] is True

    def test_with_mcp_on_nothing_is_turned_on(self, admin, user, setup_oauth):
        result = fixtures._ok(
            admin.post(oauth_app.ROOT, oauth_app.body(), format="json"), 201
        )["result"]
        assert result["mcp_enabled"] is True
        assert result["mcp_turned_on"] is False

    @pytest.mark.parametrize("state", OFF)
    def test_a_role_that_may_not_turn_mcp_on_is_refused_and_nothing_is_saved(
        self, auth_client, user, setup_oauth, pushes, monkeypatch, state
    ):
        org = user.organization
        _set_mcp(org, OFF[state])
        before = fixtures._versions(org)
        _may_not_turn_on(monkeypatch)
        response = auth_client.post(
            oauth_app.ROOT,
            oauth_app.body(
                registration="manual",
                client_id="synthetic-client",
                client_secret=oauth_app.SECRET,
            ),
            format="json",
        )
        body = fixtures._refused(response, 403, CODE)
        assert body["message"] == SENTENCE
        assert oauth_app.SECRET not in response.content.decode()
        # No authorization server call, no client, no version, no push.
        assert setup_oauth.requests == []
        assert not AgentccMCPUpstreamClient.objects.exists()
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        assert fixtures._versions(org) == before and pushes == []

    @pytest.mark.parametrize("state", OFF)
    def test_a_member_is_refused_at_the_service(
        self, user, workspace, setup_oauth, pushes, state
    ):
        from agentcc.services.connector_catalog import ConnectorForbidden

        org = user.organization
        _set_mcp(org, OFF[state])
        member = _person(org, workspace, Level.MEMBER, OrganizationRoles.MEMBER)
        before = fixtures._versions(org)
        with pytest.raises(ConnectorForbidden) as refused:
            apps.configure(org, oauth_app.APP, oauth_app.body(), member)
        assert refused.value.code == CODE and str(refused.value) == SENTENCE
        assert setup_oauth.requests == []
        assert not AgentccMCPUpstreamClient.objects.exists()
        assert fixtures._versions(org) == before and pushes == []


# --- The order of saves ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("setup_oauth", "drift_free", "probe_budget")
class TestOrderOfSaves:
    @pytest.mark.parametrize("first", ["the OAuth app", "the key add"])
    def test_the_order_of_saves_does_not_change_the_result(
        self, auth_client, user, bundle, setup_oauth, probe, first
    ):
        org = user.organization
        # The OAuth app's bundle, with the key apps of the default one.
        slate = json.loads(catalog.BUNDLE_PATH.read_text())
        data = fixtures._bundle()
        data["apps"] += slate["apps"]
        data["providers"] += slate["providers"]
        bundle(data)
        _set_mcp(org, OFF["never set"])

        def save_oauth_app():
            return fixtures._ok(
                auth_client.post(oauth_app.ROOT, oauth_app.body(), format="json"),
                201,
            )["result"]

        def add_key_app():
            return fixtures._ok(
                auth_client.post(fixtures._add("cobalt-search"), {}, format="json"),
                201,
            )["result"]

        saves = (
            [save_oauth_app, add_key_app]
            if first == "the OAuth app"
            else [add_key_app, save_oauth_app]
        )
        results = [save() for save in saves]
        assert [result["mcp_turned_on"] for result in results] == [True, False]
        mcp = fixtures._active(org).mcp
        assert mcp["enabled"] is True
        assert {oauth_app.APP, "cobalt-search"} <= set(mcp["servers"])
