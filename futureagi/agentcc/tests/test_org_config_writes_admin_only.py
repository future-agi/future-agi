"""Only an org Owner or Admin can change the org's gateway config.

Each agentcc write that changes or pushes the org config (guardrail
checks, routing, budgets and MCP, the org's provider keys, config versions,
shadow experiments) needs an Owner or Admin of the organization the request
acts on (``request.organization``), not of the user's home organization;
every member keeps read access, the console's Reload and Health check (they
push what an Owner or Admin already saved), and fetching a provider's models
with the saved key.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
CREDENTIALS = "/agentcc/provider-credentials/"
ROUTING_POLICIES = "/agentcc/routing-policies/"
SHADOW = "/agentcc/shadow-experiments/"

# Test values, not real credentials or hosts.
STORED_KEY = "stored-provider-key-test-value"
ROTATED_KEY = "rotated-provider-key-test-value"
COLLECTOR = "https://collector.example.test/in"

# A webhook check in both shapes the console writes.
WEBHOOK_RULES = {
    "rules": [
        {
            "name": "outbound-webhook",
            "enabled": True,
            "action": "log",
            "threshold": 0.5,
            "config": {"url": COLLECTOR},
        }
    ]
}
WEBHOOK_CHECKS = {
    "checks": {
        "outbound-webhook": {
            "enabled": True,
            "action": "log",
            "config": {"url": COLLECTOR},
        }
    }
}

LEVEL_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}


def _member_of(user, organization, level, workspace=None):
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    workspace = workspace or Workspace.objects.create(
        name=f"Workspace {uuid.uuid4().hex[:6]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return workspace


def _user_with_level(organization, level):
    user = User.objects.create_user(
        email=f"t-role-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Role User",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True
    )
    _member_of(user, organization, level, workspace=workspace)
    return user


@pytest.fixture
def make_client():
    clients = []

    def build(user, workspace):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def client_with_level(organization, workspace, make_client):
    def build(level):
        return make_client(_user_with_level(organization, level), workspace)

    return build


@pytest.fixture
def active_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        mcp={"servers": {"docs": {"url": "https://mcp.example.test/mcp"}}},
    )


@pytest.fixture
def older_config(organization, active_config):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=2, is_active=False
    )


@pytest.fixture
def credential(organization):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name="openai",
        display_name="OpenAI",
        encrypted_credentials=CredentialManager.encrypt({"api_key": STORED_KEY}),
        api_format="openai",
    )


@pytest.fixture
def routing_policy(organization, user):
    return AgentccRoutingPolicy.no_workspace_objects.create(
        organization=organization,
        name="primary",
        version=1,
        is_active=True,
        config={"strategy": "round_robin"},
        created_by=user,
    )


def _experiment(organization, user, name, experiment_status):
    return AgentccShadowExperiment.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        created_by=user,
        name=name,
        source_model="gpt-4o",
        shadow_model="claude-3-5-sonnet",
        shadow_provider="anthropic",
        sample_rate=0.1,
        status=experiment_status,
    )


@pytest.fixture
def active_experiment(organization, user):
    return _experiment(
        organization, user, "active-exp", AgentccShadowExperiment.STATUS_ACTIVE
    )


@pytest.fixture
def paused_experiment(organization, user):
    return _experiment(
        organization, user, "paused-exp", AgentccShadowExperiment.STATUS_PAUSED
    )


@pytest.fixture
def gateway():
    """Every push and gateway call goes to a mock that records what it got."""
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
    ):
        yield client


def _snapshot(organization):
    """Everything a refused write would change."""
    return {
        "configs": list(
            AgentccOrgConfig.all_objects.filter(organization=organization)
            .order_by("version")
            .values_list(
                "version",
                "is_active",
                "deleted",
                "guardrails",
                "routing",
                "budgets",
                "cache",
                "mcp",
            )
        ),
        "credentials": list(
            AgentccProviderCredential.all_objects.filter(organization=organization)
            .order_by("provider_name")
            .values_list("provider_name", "deleted", "is_active", "display_name")
        ),
        "credential_blobs": [
            CredentialManager.decrypt(bytes(blob))
            for blob in AgentccProviderCredential.all_objects.filter(
                organization=organization
            )
            .order_by("provider_name")
            .values_list("encrypted_credentials", flat=True)
        ],
        "routing_policies": list(
            AgentccRoutingPolicy.all_objects.filter(organization=organization)
            .order_by("name", "version")
            .values_list("name", "version", "is_active", "deleted", "config")
        ),
        "shadow_experiments": list(
            AgentccShadowExperiment.all_objects.filter(organization=organization)
            .order_by("name")
            .values_list("name", "status", "deleted", "sample_rate", "shadow_model")
        ),
    }


SHADOW_BODY = {
    "name": "member-exp",
    "source_model": "gpt-4o",
    "shadow_model": "gpt-4o-mini",
    "shadow_provider": "openai",
    "sample_rate": 1.0,
}

# (method, path, body). `{older}`, `{credential}`, `{policy}`, `{active_exp}`
# and `{paused_exp}` are filled from the fixtures. Bodies are valid, so only
# the role check refuses them.
GATED = {
    # The gateway view's org-config writes.
    "update-config": (
        "post",
        f"{GATEWAY}/update-config/",
        {"cache": {"enabled": True}},
    ),
    "update-config-webhook-check": (
        "post",
        f"{GATEWAY}/update-config/",
        {"guardrails": WEBHOOK_RULES},
    ),
    "update-provider": (
        "post",
        f"{GATEWAY}/update-provider/",
        {"name": "anthropic", "config": {"api_key": ROTATED_KEY}},
    ),
    "remove-provider": ("post", f"{GATEWAY}/remove-provider/", {"name": "openai"}),
    "toggle-guardrail": (
        "post",
        f"{GATEWAY}/toggle-guardrail/",
        {"name": "pii-detector", "enabled": True},
    ),
    "update-guardrail": (
        "post",
        f"{GATEWAY}/update-guardrail/",
        {"name": "outbound-webhook", "config": {"enabled": True, "url": COLLECTOR}},
    ),
    "test-playground": ("post", f"{GATEWAY}/test-playground/", {"prompt": "hello"}),
    "set-budget": (
        "post",
        f"{GATEWAY}/set-budget/",
        {"level": "org", "config": {"limit": 10, "period": "monthly"}},
    ),
    "remove-budget": ("post", f"{GATEWAY}/remove-budget/", {"level": "org"}),
    "update-mcp-server": (
        "post",
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": "search", "config": {"url": "https://mcp.example.test/s"}},
    ),
    "remove-mcp-server": (
        "post",
        f"{GATEWAY}/remove-mcp-server/",
        {"server_id": "docs"},
    ),
    "update-mcp-guardrails": (
        "post",
        f"{GATEWAY}/update-mcp-guardrails/",
        {"config": {"enabled": True}},
    ),
    # Org-config versions.
    "org-config-create": ("post", ORG_CONFIGS, {"cache": {"enabled": True}}),
    "org-config-create-webhook-check": (
        "post",
        ORG_CONFIGS,
        {"guardrails": WEBHOOK_CHECKS},
    ),
    "org-config-activate": ("post", f"{ORG_CONFIGS}{{older}}/activate/", {}),
    "org-config-delete": ("delete", f"{ORG_CONFIGS}{{older}}/", None),
    "org-config-update": ("put", f"{ORG_CONFIGS}{{older}}/", {}),
    "org-config-partial-update": ("patch", f"{ORG_CONFIGS}{{older}}/", {}),
    # Provider credentials (each write pushes the org config).
    "credential-create": (
        "post",
        CREDENTIALS,
        {
            "provider_name": "anthropic",
            "display_name": "Anthropic",
            "credentials": {"api_key": ROTATED_KEY},
            "api_format": "anthropic",
        },
    ),
    "credential-update": (
        "put",
        f"{CREDENTIALS}{{credential}}/",
        {"display_name": "Renamed"},
    ),
    "credential-partial-update": (
        "patch",
        f"{CREDENTIALS}{{credential}}/",
        {"display_name": "Renamed"},
    ),
    "credential-rotate": (
        "post",
        f"{CREDENTIALS}{{credential}}/rotate/",
        {"credentials": {"api_key": ROTATED_KEY}},
    ),
    "credential-delete": ("delete", f"{CREDENTIALS}{{credential}}/", None),
    # Routing policies (each write makes a new config version and pushes it).
    "routing-policy-create": (
        "post",
        ROUTING_POLICIES,
        {"name": "secondary", "config": {"strategy": "round_robin"}},
    ),
    "routing-policy-update": (
        "put",
        f"{ROUTING_POLICIES}{{policy}}/",
        {"name": "primary", "config": {}},
    ),
    "routing-policy-partial-update": (
        "patch",
        f"{ROUTING_POLICIES}{{policy}}/",
        {"config": {}},
    ),
    "routing-policy-delete": ("delete", f"{ROUTING_POLICIES}{{policy}}/", None),
    "routing-policy-activate": ("post", f"{ROUTING_POLICIES}{{policy}}/activate/", {}),
    "routing-policy-sync": ("post", f"{ROUTING_POLICIES}sync/", {}),
    # Shadow experiments (pause, resume and complete push the org config).
    "shadow-create": ("post", SHADOW, SHADOW_BODY),
    "shadow-update": ("put", f"{SHADOW}{{active_exp}}/", SHADOW_BODY),
    "shadow-partial-update": (
        "patch",
        f"{SHADOW}{{active_exp}}/",
        {"sample_rate": 1.0},
    ),
    "shadow-delete": ("delete", f"{SHADOW}{{active_exp}}/", None),
    "shadow-pause": ("patch", f"{SHADOW}{{active_exp}}/pause/", {}),
    "shadow-resume": ("patch", f"{SHADOW}{{paused_exp}}/resume/", {}),
    "shadow-complete": ("patch", f"{SHADOW}{{active_exp}}/complete/", {}),
}


def _send(client, method, path, body, **ids):
    url = path.format(**ids)
    if body is None:
        return getattr(client, method)(url)
    return getattr(client, method)(url, body, format="json")


@pytest.fixture
def gated_ids(
    active_config,
    older_config,
    credential,
    routing_policy,
    active_experiment,
    paused_experiment,
):
    return {
        "older": older_config.pk,
        "credential": credential.pk,
        "policy": routing_policy.pk,
        "active_exp": active_experiment.pk,
        "paused_exp": paused_experiment.pk,
    }


@pytest.mark.integration
@pytest.mark.api
class TestMembersCannotChangeTheOrgConfig:
    @pytest.mark.parametrize("endpoint", sorted(GATED))
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_non_admin_gets_403_and_nothing_changes_or_is_pushed(
        self,
        client_with_level,
        organization,
        gated_ids,
        gateway,
        endpoint,
        level,
    ):
        client = client_with_level(level)
        before = _snapshot(organization)
        method, path, body = GATED[endpoint]

        response = _send(client, method, path, body, **gated_ids)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert GATEWAY_ADMIN_MESSAGE in response.content.decode()
        assert _snapshot(organization) == before
        assert COLLECTOR not in repr(_snapshot(organization))
        gateway.set_org_config.assert_not_called()
        gateway.create_key.assert_not_called()

    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    @pytest.mark.parametrize(
        "path,body",
        [
            (f"{GATEWAY}/update-config/", {"guardrails": WEBHOOK_RULES}),
            (ORG_CONFIGS, {"guardrails": WEBHOOK_CHECKS}),
        ],
        ids=["update-config", "POST-org-configs"],
    )
    def test_member_cannot_add_a_webhook_check(
        self, client_with_level, organization, active_config, gateway, level, path, body
    ):
        # Neither path stores the URL or pushes it.
        response = client_with_level(level).post(path, body, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.version == 1
        assert COLLECTOR not in repr(active.guardrails)
        assert COLLECTOR not in repr(gateway.set_org_config.call_args_list)

    def test_role_in_another_org_does_not_count(
        self, user, organization, workspace, make_client, active_config, gateway
    ):
        # The member here is the Owner of their own organization; the request
        # acts on `organization`, where they are only a Member.
        member = _user_with_level(organization, Level.MEMBER)
        member_client = make_client(member, workspace)
        home = Organization.objects.create(name="Member's own org")
        _member_of(member, home, Level.OWNER)
        member.organization = home
        member.save(update_fields=["organization"])

        response = member_client.post(
            f"{GATEWAY}/update-config/", {"cache": {"enabled": True}}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

        # And the reverse: the fixture Owner is only a Member of a peer org.
        peer = Organization.objects.create(name="Peer org")
        peer_workspace = _member_of(user, peer, Level.MEMBER)
        AgentccOrgConfig.no_workspace_objects.create(
            organization=peer, version=1, is_active=True
        )
        peer_client = make_client(user, peer_workspace)

        response = peer_client.post(
            f"{GATEWAY}/update-config/", {"cache": {"enabled": True}}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert (
            AgentccOrgConfig.no_workspace_objects.get(
                organization=peer, is_active=True
            ).version
            == 1
        )
        gateway.set_org_config.assert_not_called()

    def test_inactive_admin_membership_is_not_an_admin(
        self, organization, workspace, make_client, active_config, gateway
    ):
        admin = _user_with_level(organization, Level.ADMIN)
        OrganizationMembership.no_workspace_objects.filter(user=admin).update(
            is_active=False
        )

        response = make_client(admin, workspace).post(
            f"{GATEWAY}/set-budget/",
            {"level": "org", "config": {"limit": 1, "period": "monthly"}},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestAdminsCanChangeTheOrgConfig:
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_changes_and_pushes_the_org_config(
        self,
        client_with_level,
        organization,
        active_config,
        credential,
        gateway,
        level,
    ):
        client = client_with_level(level)

        # A create is a whole new version (the sections it
        # leaves out are empty), so it goes first.
        responses = {
            "org-config-create": client.post(
                ORG_CONFIGS, {"cache": {"enabled": True}}, format="json"
            ),
            "update-config": client.post(
                f"{GATEWAY}/update-config/",
                {"guardrails": WEBHOOK_RULES},
                format="json",
            ),
            "toggle-guardrail": client.post(
                f"{GATEWAY}/toggle-guardrail/",
                {"name": "outbound-webhook", "enabled": False},
                format="json",
            ),
            "set-budget": client.post(
                f"{GATEWAY}/set-budget/",
                {"level": "org_limit", "config": {"limit": 10, "on_exceed": "warn"}},
                format="json",
            ),
            "rotate": client.post(
                f"{CREDENTIALS}{credential.pk}/rotate/",
                {"credentials": {"api_key": ROTATED_KEY}},
                format="json",
            ),
        }

        assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(
            responses, status.HTTP_200_OK
        ), {name: r.content[:300] for name, r in responses.items()}
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.cache["enabled"] is True
        assert active.budgets["org_limit"]["limit"] == 10
        assert active.guardrails["rules"][0]["config"]["url"] == COLLECTOR
        assert active.guardrails["rules"][0]["enabled"] is False
        assert COLLECTOR in repr(gateway.set_org_config.call_args_list)
        credential.refresh_from_db()
        assert CredentialManager.decrypt(bytes(credential.encrypted_credentials)) == {
            "api_key": ROTATED_KEY
        }

    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_activates_and_deletes_config_versions(
        self, client_with_level, organization, older_config, gateway, level
    ):
        client = client_with_level(level)

        activated = client.post(f"{ORG_CONFIGS}{older_config.pk}/activate/", {})
        v1 = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, version=1
        )
        deleted = client.delete(f"{ORG_CONFIGS}{v1.pk}/")

        assert activated.status_code == status.HTTP_200_OK, activated.content
        assert deleted.status_code == status.HTTP_200_OK, deleted.content
        assert (
            AgentccOrgConfig.no_workspace_objects.get(
                organization=organization, is_active=True, deleted=False
            ).version
            == 2
        )
        assert AgentccOrgConfig.all_objects.get(pk=v1.pk).deleted is True
        gateway.set_org_config.assert_called()

    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_runs_the_shadow_experiment_lifecycle(
        self,
        client_with_level,
        organization,
        active_config,
        active_experiment,
        gateway,
        level,
    ):
        client = client_with_level(level)
        detail = f"{SHADOW}{active_experiment.pk}/"

        responses = {
            "partial-update": client.patch(detail, {"sample_rate": 0.5}, format="json"),
            "pause": client.patch(f"{detail}pause/", {}, format="json"),
            "resume": client.patch(f"{detail}resume/", {}, format="json"),
            "complete": client.patch(f"{detail}complete/", {}, format="json"),
            "delete": client.delete(detail),
        }

        assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(
            responses, status.HTTP_200_OK
        ), {name: r.content[:300] for name, r in responses.items()}
        stored = AgentccShadowExperiment.all_objects.get(pk=active_experiment.pk)
        assert stored.sample_rate == 0.5
        assert stored.status == AgentccShadowExperiment.STATUS_COMPLETED
        assert stored.deleted is True
        assert gateway.set_org_config.call_count == 3

    @pytest.mark.requires_ee
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_creates_a_shadow_experiment(
        self, client_with_level, organization, level
    ):
        response = client_with_level(level).post(SHADOW, SHADOW_BODY, format="json")

        assert response.status_code == status.HTTP_200_OK, response.content
        assert AgentccShadowExperiment.no_workspace_objects.filter(
            organization=organization, name="member-exp"
        ).exists()


@pytest.mark.integration
@pytest.mark.api
class TestMembersKeepReadAccess:
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    @pytest.mark.parametrize(
        "path",
        [
            ORG_CONFIGS,
            f"{ORG_CONFIGS}active/",
            "{older_diff}",
            f"{GATEWAY}/",
            f"{GATEWAY}/config/",
            CREDENTIALS,
            ROUTING_POLICIES,
            SHADOW,
            "{exp_detail}",
            "{exp_stats}",
            "/agentcc/shadow-results/",
        ],
    )
    def test_member_reads(
        self,
        client_with_level,
        active_config,
        older_config,
        active_experiment,
        gateway,
        level,
        path,
    ):
        url = path.format(
            older_diff=(
                f"{ORG_CONFIGS}{older_config.pk}/diff/?compare_to={active_config.pk}"
            ),
            exp_detail=f"{SHADOW}{active_experiment.pk}/",
            exp_stats=f"{SHADOW}{active_experiment.pk}/stats/",
        )

        response = client_with_level(level).get(url)

        assert response.status_code == status.HTTP_200_OK, response.content

    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_member_keeps_reload_and_health_check(
        self, client_with_level, organization, active_config, gateway, level
    ):
        # Reload and Health check re-push the config an Owner or Admin saved
        # (health check: at most once in 5 minutes) and change nothing, so a
        # member keeps the console's buttons.
        client = client_with_level(level)
        before = _snapshot(organization)

        reloaded = client.post(f"{GATEWAY}/reload/", {}, format="json")
        checked = client.post(f"{GATEWAY}/health_check/", {}, format="json")

        assert reloaded.status_code == status.HTTP_200_OK, reloaded.content
        assert checked.status_code == status.HTTP_200_OK, checked.content
        assert _snapshot(organization) == before

    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_member_can_still_fetch_models_with_the_saved_key(
        self, client_with_level, credential, level
    ):
        # Gateway usage, not config: listing models with the stored key stays
        # open to members.
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet."
            "_fetch_models_from_provider",
            return_value=["gpt-4o"],
        ):
            response = client_with_level(level).post(
                f"{CREDENTIALS}fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["result"] == {"models": ["gpt-4o"]}
