from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from agentcc.models.provider_credential import AgentccProviderCredential
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def secondary_org_context(user):
    org_b = Organization.objects.create(name="Second Organization")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace_b = Workspace.objects.create(
        name="Second Workspace",
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
class TestAgentccProviderCredentialOrganizationIsolation:
    def test_list_only_returns_active_request_organization_credentials(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
        )

        response = secondary_org_client.get("/agentcc/provider-credentials/")

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        if isinstance(result, dict) and "results" in result:
            result = result["results"]
        names = {item["provider_name"] for item in result}
        assert names == {"anthropic"}

    def test_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "display_name": "Org B OpenAI",
                    "credentials": {"api_key": "sk-org-b"},
                    "api_format": "openai",
                },
                format="json",
            )

        assert response.status_code == 201, response.json()

        credential = AgentccProviderCredential.no_workspace_objects.get(
            provider_name="openai", deleted=False
        )
        assert credential.organization_id == org_b.id
        assert credential.organization_id != user.organization_id

    def test_fetch_models_reads_credential_from_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=["gpt-4o"],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        args, _ = mock_fetch.call_args
        # Signature: (provider_name, base_url, api_key, api_format)
        assert args[0] == "openai"
        assert args[2] == "sk-org-b"
