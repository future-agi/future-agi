"""Server-side role checks on the agentcc endpoints that change spend or policy.

Only an org Owner or Admin may change the gateway config, its provider
credentials, its routing policies, its guardrail policies or its shadow
experiments through the API; every other member keeps read access. The role is the one the user holds in the organization the
request acts on, not in the user's home organization.
"""

import secrets
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.exceptions import PermissionDenied

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.permissions import (
    IsOrgGatewayAdmin,
    IsStaffOrAdminToken,
    is_gateway_admin,
    require_gateway_admin,
)
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
CREDENTIALS = "/agentcc/provider-credentials/"
ROUTING_POLICIES = "/agentcc/routing-policies/"
GUARDRAIL_POLICIES = "/agentcc/guardrail-policies/"
SHADOW_EXPERIMENTS = "/agentcc/shadow-experiments/"
# A random token: IsAdminToken refuses a short, placeholder or published one
# (agentcc/admin_token.py), as the gateway does at start-up.
ADMIN_TOKEN = secrets.token_hex(32)

# Test values, not real credentials.
STORED_KEY = "stored-provider-key-test-value"
ROTATED_KEY = "rotated-provider-key-test-value"

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
        email=f"role-{uuid.uuid4().hex[:8]}@futureagi.com",
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
def member_client(client_with_level):
    return client_with_level(Level.MEMBER)


@pytest.fixture
def admin_client(client_with_level):
    return client_with_level(Level.ADMIN)


@pytest.fixture
def active_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
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


@pytest.fixture
def guardrail_policy(organization):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="baseline",
        checks=[{"name": "injection-detector", "action": "block"}],
    )


@pytest.fixture
def shadow_experiment(organization, user):
    return AgentccShadowExperiment.no_workspace_objects.create(
        organization=organization,
        created_by=user,
        name="mirror",
        source_model="gpt-4o",
        shadow_model="claude-3-5-sonnet",
        shadow_provider="anthropic",
        sample_rate=0.1,
        status=AgentccShadowExperiment.STATUS_ACTIVE,
    )


@pytest.fixture
def gateway():
    """Every push and gateway call goes to a mock that records what it got."""
    client = MagicMock()
    client.get_catalog.return_value = {"models": []}
    client.get_org_credits.return_value = {"balance_usd": 0, "markup_percent": 5}
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
        patch("agentcc.views.managed_models.get_gateway_client", return_value=client),
    ):
        yield client


def _snapshot(organization):
    """Everything a refused write could have changed."""
    return {
        "configs": list(
            AgentccOrgConfig.all_objects.filter(organization=organization)
            .order_by("version")
            .values_list("version", "is_active", "deleted", "routing", "budgets")
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
            ).values_list("encrypted_credentials", flat=True)
        ],
        "routing_policies": list(
            AgentccRoutingPolicy.all_objects.filter(organization=organization)
            .order_by("version")
            .values_list("name", "version", "is_active", "deleted")
        ),
        "guardrail_policies": list(
            AgentccGuardrailPolicy.all_objects.filter(organization=organization)
            .order_by("name")
            .values_list(
                "name",
                "deleted",
                "is_active",
                "scope",
                "mode",
                "checks",
                "applied_keys",
            )
        ),
        "shadow_experiments": list(
            AgentccShadowExperiment.all_objects.filter(organization=organization)
            .order_by("name")
            .values_list(
                "name",
                "deleted",
                "status",
                "sample_rate",
                "shadow_model",
                "description",
            )
        ),
    }


# (method, path, body). `{older}`, `{credential}`, `{policy}` and
# `{guardrail_policy}` are filled from the fixtures. Bodies are valid, so only
# the role check refuses them.
GATED = {
    "update-config": (
        "post",
        f"{GATEWAY}/update-config/",
        {"cache": {"enabled": True}},
    ),
    "managed-opt-in": (
        "post",
        f"{GATEWAY}/update-config/",
        {"managed_models": {"enabled": True}},
    ),
    "auto-routing-save": (
        "post",
        f"{GATEWAY}/update-config/",
        {"routing": {"auto": {"preference": "cost"}}},
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
        {"name": "pii-detector", "config": {"enabled": True, "action": "block"}},
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
        {"server_id": "docs", "config": {"url": "https://mcp.example.com"}},
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
    "org-config-create": ("post", ORG_CONFIGS, {"cache": {"enabled": True}}),
    "org-config-activate": ("post", f"{ORG_CONFIGS}{{older}}/activate/", {}),
    "org-config-delete": ("delete", f"{ORG_CONFIGS}{{older}}/", None),
    "org-config-update": ("put", f"{ORG_CONFIGS}{{older}}/", {}),
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
    # A guardrail policy's checks are merged into the org config and pushed to
    # the gateway, so writing one is Owner or Admin only.
    "guardrail-policy-create": (
        "post",
        GUARDRAIL_POLICIES,
        {
            "name": "webhook-policy",
            "checks": [
                {
                    "name": "callback-webhook",
                    "config": {"url": "https://hooks.example.test/in"},
                }
            ],
        },
    ),
    "guardrail-policy-update": (
        "put",
        f"{GUARDRAIL_POLICIES}{{guardrail_policy}}/",
        {"name": "baseline", "checks": [{"name": "injection-detector"}]},
    ),
    "guardrail-policy-partial-update": (
        "patch",
        f"{GUARDRAIL_POLICIES}{{guardrail_policy}}/",
        {"mode": "monitor"},
    ),
    "guardrail-policy-delete": (
        "delete",
        f"{GUARDRAIL_POLICIES}{{guardrail_policy}}/",
        None,
    ),
    "guardrail-policy-apply": (
        "post",
        f"{GUARDRAIL_POLICIES}{{guardrail_policy}}/apply/",
        {"key_ids": []},
    ),
    "guardrail-policy-sync": ("post", f"{GUARDRAIL_POLICIES}sync/", {}),
    # A shadow experiment mirrors a share of the org's traffic to another model
    # and provider, and pause, resume and complete push the org config, so
    # writing one is Owner or Admin only.
    "shadow-experiment-create": (
        "post",
        SHADOW_EXPERIMENTS,
        {
            "name": "mirror-all",
            "source_model": "gpt-4o",
            "shadow_model": "claude-3-5-sonnet",
            "shadow_provider": "anthropic",
            "sample_rate": 1.0,
        },
    ),
    "shadow-experiment-update": (
        "put",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/",
        {
            "name": "mirror",
            "source_model": "gpt-4o",
            "shadow_model": "gpt-4o-mini",
            "shadow_provider": "openai",
            "sample_rate": 1.0,
        },
    ),
    "shadow-experiment-partial-update": (
        "patch",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/",
        {"sample_rate": 1.0},
    ),
    "shadow-experiment-delete": (
        "delete",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/",
        None,
    ),
    "shadow-experiment-pause": (
        "patch",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/pause/",
        {},
    ),
    "shadow-experiment-resume": (
        "patch",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/resume/",
        {},
    ),
    "shadow-experiment-complete": (
        "patch",
        f"{SHADOW_EXPERIMENTS}{{shadow_experiment}}/complete/",
        {},
    ),
}


def _send(client, method, path, body, **ids):
    url = path.format(**ids)
    if body is None:
        return getattr(client, method)(url)
    return getattr(client, method)(url, body, format="json")


@pytest.fixture
def gated_ids(
    older_config, credential, routing_policy, guardrail_policy, shadow_experiment
):
    return {
        "older": older_config.pk,
        "credential": credential.pk,
        "policy": routing_policy.pk,
        "guardrail_policy": guardrail_policy.pk,
        "shadow_experiment": shadow_experiment.pk,
    }


@pytest.mark.integration
@pytest.mark.api
class TestMembersCannotChangeGatewayConfig:
    @pytest.mark.parametrize("endpoint", sorted(GATED))
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_non_admin_gets_403_and_nothing_changes(
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

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.json()
        assert response.json()["message"] == IsOrgGatewayAdmin.message
        assert _snapshot(organization) == before
        gateway.set_org_config.assert_not_called()
        gateway.create_key.assert_not_called()

    def test_member_gets_403_on_update_config_opt_in_post_and_rotate(
        self, member_client, organization, credential, active_config, gateway
    ):
        # Four writes, each read back from the store.
        responses = {
            "update-config": member_client.post(
                f"{GATEWAY}/update-config/",
                {"routing": {"strategy": "least_latency"}},
                format="json",
            ),
            "opt-in": member_client.post(
                f"{GATEWAY}/update-config/",
                {"managed_models": {"enabled": True}},
                format="json",
            ),
            "org-config-post": member_client.post(
                ORG_CONFIGS, {"managed_models": {"enabled": True}}, format="json"
            ),
            "rotate": member_client.post(
                f"{CREDENTIALS}{credential.pk}/rotate/",
                {"credentials": {"api_key": ROTATED_KEY}},
                format="json",
            ),
        }

        assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(
            responses, status.HTTP_403_FORBIDDEN
        )
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.version == 1
        assert active.managed_models == {}
        credential.refresh_from_db()
        assert CredentialManager.decrypt(bytes(credential.encrypted_credentials)) == {
            "api_key": STORED_KEY
        }
        assert credential.last_rotated_at is None

    def test_role_in_another_org_does_not_count(
        self, user, organization, workspace, make_client, active_config, gateway
    ):
        # The member here is the owner of their own organization; the request
        # acts on `organization`, where they are only a member.
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

        # And the reverse: the fixture owner is only a member of a peer org.
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


@pytest.mark.integration
@pytest.mark.api
class TestAdminsCanChangeGatewayConfig:
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_gets_200_on_update_config_opt_in_post_and_rotate(
        self, client_with_level, organization, credential, active_config, gateway, level
    ):
        client = client_with_level(level)

        update = client.post(
            f"{GATEWAY}/update-config/",
            {"routing": {"strategy": "least_latency"}},
            format="json",
        )
        opt_in = client.post(
            f"{GATEWAY}/update-config/",
            {"managed_models": {"enabled": True}},
            format="json",
        )
        created = client.post(ORG_CONFIGS, {"cache": {"enabled": True}}, format="json")
        rotated = client.post(
            f"{CREDENTIALS}{credential.pk}/rotate/",
            {"credentials": {"api_key": ROTATED_KEY}},
            format="json",
        )

        for response in (update, opt_in, created, rotated):
            assert response.status_code == status.HTTP_200_OK, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.version == 4
        assert active.routing["strategy"] == "least_latency"
        assert active.managed_models == {"enabled": True}
        assert active.cache["enabled"] is True
        credential.refresh_from_db()
        assert CredentialManager.decrypt(bytes(credential.encrypted_credentials)) == {
            "api_key": ROTATED_KEY
        }


@pytest.mark.integration
@pytest.mark.api
class TestAdminsCanChangeGuardrailPolicies:
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_gets_200_on_every_guardrail_policy_write(
        self,
        client_with_level,
        organization,
        active_config,
        guardrail_policy,
        gateway,
        level,
    ):
        client = client_with_level(level)
        detail = f"{GUARDRAIL_POLICIES}{guardrail_policy.pk}/"

        responses = {
            "create": client.post(
                GUARDRAIL_POLICIES,
                {"name": "strict", "checks": [{"name": "injection-detector"}]},
                format="json",
            ),
            "update": client.put(
                detail,
                {"name": "baseline", "checks": [{"name": "pii-detector"}]},
                format="json",
            ),
            "partial-update": client.patch(detail, {"mode": "monitor"}, format="json"),
            "apply": client.post(f"{detail}apply/", {"key_ids": []}, format="json"),
            "sync": client.post(f"{GUARDRAIL_POLICIES}sync/", {}, format="json"),
            "delete": client.delete(detail),
        }

        assert {
            name: response.status_code for name, response in responses.items()
        } == dict.fromkeys(responses, status.HTTP_200_OK)
        assert AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="strict"
        ).exists()
        stored = AgentccGuardrailPolicy.all_objects.get(pk=guardrail_policy.pk)
        assert stored.deleted is True
        assert stored.mode == "monitor"
        assert stored.checks == [{"name": "pii-detector"}]
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestAdminsCanChangeShadowExperiments:
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_gets_200_on_every_shadow_experiment_write(
        self,
        client_with_level,
        organization,
        active_config,
        shadow_experiment,
        gateway,
        level,
    ):
        client = client_with_level(level)
        detail = f"{SHADOW_EXPERIMENTS}{shadow_experiment.pk}/"

        # The create's plan-limit check is not what this test is about.
        with patch("tfc.ee_gating.check_ee_can_create"):
            created = client.post(
                SHADOW_EXPERIMENTS,
                {
                    "name": "second",
                    "source_model": "gpt-4o",
                    "shadow_model": "gpt-4o-mini",
                    "shadow_provider": "openai",
                    "sample_rate": 0.2,
                },
                format="json",
            )
        responses = {
            "create": created,
            "update": client.put(
                detail,
                {
                    "name": "mirror",
                    "source_model": "gpt-4o",
                    "shadow_model": "claude-3-5-sonnet",
                    "shadow_provider": "anthropic",
                    "sample_rate": 0.3,
                },
                format="json",
            ),
            "partial-update": client.patch(
                detail, {"description": "checked"}, format="json"
            ),
            "pause": client.patch(f"{detail}pause/", {}, format="json"),
            "resume": client.patch(f"{detail}resume/", {}, format="json"),
            "complete": client.patch(f"{detail}complete/", {}, format="json"),
            "delete": client.delete(detail),
        }

        assert {
            name: response.status_code for name, response in responses.items()
        } == dict.fromkeys(responses, status.HTTP_200_OK)
        assert AgentccShadowExperiment.no_workspace_objects.filter(
            organization=organization, name="second"
        ).exists()
        stored = AgentccShadowExperiment.all_objects.get(pk=shadow_experiment.pk)
        assert stored.deleted is True
        assert stored.status == AgentccShadowExperiment.STATUS_COMPLETED
        assert stored.sample_rate == 0.3
        assert stored.description == "checked"
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestMembersKeepReadAccess:
    @pytest.mark.parametrize(
        "path",
        [
            "/agentcc/managed-models/catalog/",
            "/agentcc/managed-models/",
            ORG_CONFIGS,
            f"{ORG_CONFIGS}active/",
            CREDENTIALS,
            ROUTING_POLICIES,
            GUARDRAIL_POLICIES,
            f"{GATEWAY}/config/",
        ],
    )
    def test_member_can_read(self, member_client, active_config, gateway, path):
        response = member_client.get(path)

        assert response.status_code == status.HTTP_200_OK, response.json()

    def test_member_reads_a_shadow_experiment(self, member_client, shadow_experiment):
        listed = member_client.get(SHADOW_EXPERIMENTS)
        assert listed.status_code == status.HTTP_200_OK, listed.json()
        assert [row["name"] for row in listed.json()["result"]] == ["mirror"]
        for suffix in ("", "stats/"):
            response = member_client.get(
                f"{SHADOW_EXPERIMENTS}{shadow_experiment.pk}/{suffix}"
            )
            assert response.status_code == status.HTTP_200_OK, response.json()

    def test_member_reads_a_guardrail_policy(self, member_client, guardrail_policy):
        response = member_client.get(f"{GUARDRAIL_POLICIES}{guardrail_policy.pk}/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["name"] == "baseline"

    def test_member_reads_the_catalog_and_the_org_configs(
        self, member_client, organization, active_config, gateway
    ):
        gateway.get_catalog.return_value = {"models": [{"id": "openai/gpt-4o"}]}
        with patch("agentcc.views.managed_models.cache") as cache:
            cache.get.return_value = None
            catalog = member_client.get("/agentcc/managed-models/catalog/")
        configs = member_client.get(ORG_CONFIGS)

        assert catalog.status_code == status.HTTP_200_OK
        assert catalog.json()["result"] == {"models": [{"id": "openai/gpt-4o"}]}
        assert configs.status_code == status.HTTP_200_OK
        assert [c["version"] for c in configs.json()["result"]] == [1]

    def test_member_can_still_fetch_models_with_the_saved_key(
        self, member_client, credential
    ):
        # Gateway usage, not config: listing models with the stored key is open
        # to members.
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet."
            "_fetch_models_from_provider",
            return_value=["gpt-4o"],
        ):
            response = member_client.post(
                f"{CREDENTIALS}fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"] == {"models": ["gpt-4o"]}


def _request(user=None, organization=None, authorization=""):
    return SimpleNamespace(
        user=user or SimpleNamespace(is_authenticated=False, is_staff=False),
        organization=organization,
        headers={"Authorization": authorization} if authorization else {},
    )


@pytest.mark.integration
class TestPermissionHelpers:
    def test_require_gateway_admin_raises_for_a_member(self, organization, workspace):
        member = _user_with_level(organization, Level.MEMBER)
        admin = _user_with_level(organization, Level.ADMIN)

        with pytest.raises(PermissionDenied) as denied:
            require_gateway_admin(_request(member, organization))

        assert str(denied.value.detail) == IsOrgGatewayAdmin.message
        assert require_gateway_admin(_request(admin, organization)) is None

    def test_no_organization_or_anonymous_is_not_an_admin(self, user, organization):
        assert is_gateway_admin(_request(user, None)) is False
        assert is_gateway_admin(_request(None, organization)) is False
        assert is_gateway_admin(_request(user, organization)) is True

    def test_inactive_membership_is_not_an_admin(self, organization, workspace):
        admin = _user_with_level(organization, Level.ADMIN)
        OrganizationMembership.no_workspace_objects.filter(user=admin).update(
            is_active=False
        )

        assert is_gateway_admin(_request(admin, organization)) is False

    @pytest.mark.parametrize(
        ("is_staff", "authorization", "configured", "allowed"),
        [
            (True, "", ADMIN_TOKEN, True),
            (False, f"Bearer {ADMIN_TOKEN}", ADMIN_TOKEN, True),
            (False, "Bearer wrong-token", ADMIN_TOKEN, False),
            (False, "", ADMIN_TOKEN, False),
            (False, f"Bearer {ADMIN_TOKEN}", "", False),
            (True, "", "", True),
        ],
        ids=[
            "staff",
            "admin-token",
            "wrong-token",
            "neither",
            "token-unconfigured",
            "staff-token-unconfigured",
        ],
    )
    def test_is_staff_or_admin_token(
        self, user, is_staff, authorization, configured, allowed
    ):
        user.is_staff = is_staff
        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", configured):
            granted = IsStaffOrAdminToken().has_permission(
                _request(user, authorization=authorization), view=None
            )

        assert granted is allowed

    def test_an_org_owner_is_not_staff(self, user):
        assert user.is_staff is False
        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            assert IsStaffOrAdminToken().has_permission(_request(user), None) is False
