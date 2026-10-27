from unittest.mock import MagicMock, patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def secondary_org_context(user):
    org_b = Organization.objects.create(name="Gateway Second Org")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace_b = Workspace.objects.create(
        name="Gateway Second Workspace",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace_b,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return org_b, workspace_b


@pytest.fixture
def secondary_org_client(user, secondary_org_context):
    _, workspace_b = secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace_b)
    yield client
    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestGatewayProvidersOrgIsolation:
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_list_uses_active_request_organization(
        self, mock_get_client, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
            models_list=["gpt-4o"],
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
            models_list=["claude-3-5-sonnet"],
        )

        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "ok"}
        mock_get_client.return_value = mock_client

        response = secondary_org_client.get("/agentcc/gateways/")

        assert response.status_code == 200, response.json()
        gateway = response.json()["result"][0]
        assert gateway["provider_count"] == 1
        assert gateway["model_count"] == 1

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_config_uses_active_request_organization(
        self, mock_get_client, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
            models_list=["gpt-4o"],
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
            models_list=["claude-3-5-sonnet"],
        )
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org_b,
            version=7,
            is_active=True,
            guardrails={"mode": "org-b"},
        )

        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "ok"}
        mock_get_client.return_value = mock_client

        response = secondary_org_client.get("/agentcc/gateways/default/config/")

        assert response.status_code == 200, response.json()
        providers = response.json()["result"]["providers"]
        assert set(providers.keys()) == {"anthropic"}

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_health_check_uses_active_request_organization(
        self, mock_get_client, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
            models_list=["gpt-4o"],
            is_active=True,
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
            models_list=["claude-3-5-sonnet"],
            is_active=True,
        )

        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "ok"}
        mock_get_client.return_value = mock_client

        response = secondary_org_client.post("/agentcc/gateways/default/health_check/")

        assert response.status_code == 200, response.json()
        providers = response.json()["result"]["providers"]["providers"]
        assert [provider["name"] for provider in providers] == ["anthropic"]

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_providers_endpoint_uses_active_request_organization(
        self, mock_get_client, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
            models_list=["gpt-4o"],
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
            models_list=["claude-3-5-sonnet"],
        )

        mock_client = MagicMock()
        mock_client.provider_health.return_value = {"providers": {}}
        mock_get_client.return_value = mock_client

        response = secondary_org_client.get("/agentcc/gateways/default/providers/")

        assert response.status_code == 200, response.json()
        providers = response.json()["result"]["providers"]
        names = {item["id"] for item in providers}
        assert names == {"anthropic"}
