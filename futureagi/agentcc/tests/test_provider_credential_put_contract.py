from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.provider_credential import AgentccProviderCredential
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def put_secondary_org_context(user):
    organization = Organization.objects.create(name="Provider PUT Organization")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.objects.create(
        name="Provider PUT Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return organization, workspace


@pytest.fixture
def put_secondary_org_client(user, put_secondary_org_context):
    _, workspace = put_secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestAgentccProviderCredentialPutContract:
    def test_cross_tenant_put_returns_404_without_gateway_push(
        self, user, put_secondary_org_context, put_secondary_org_client
    ):
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "sk-org-a"}
            ),
            api_format="openai",
        )
        encrypted_before = bytes(credential.encrypted_credentials)

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = put_secondary_org_client.put(
                f"/agentcc/provider-credentials/{credential.id}/",
                {"display_name": "Must Not Apply"},
                format="json",
            )

        assert response.status_code == 404
        mock_push.assert_not_called()
        credential.refresh_from_db()
        assert credential.display_name == "Org A OpenAI"
        assert bytes(credential.encrypted_credentials) == encrypted_before

    def test_put_preserves_encrypted_credentials_and_pushes_once(
        self, put_secondary_org_context, put_secondary_org_client
    ):
        organization, _ = put_secondary_org_context
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="openai",
            display_name="Old Display",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "sk-unchanged"}
            ),
            api_format="openai",
            models_list=["gpt-4o-mini"],
        )
        encrypted_before = bytes(credential.encrypted_credentials)

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = put_secondary_org_client.put(
                f"/agentcc/provider-credentials/{credential.id}/",
                {
                    "display_name": "New Display",
                    "models_list": ["gpt-4o"],
                },
                format="json",
            )

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert result["gateway_synced"] is True
        mock_push.assert_called_once_with(organization)

        credential.refresh_from_db()
        assert credential.display_name == "New Display"
        assert credential.models_list == ["gpt-4o"]
        assert bytes(credential.encrypted_credentials) == encrypted_before
        assert CredentialManager.decrypt(credential.encrypted_credentials) == {
            "api_key": "sk-unchanged"
        }

    def test_put_reports_gateway_sync_failure_without_hiding_saved_change(
        self, put_secondary_org_context, put_secondary_org_client
    ):
        organization, _ = put_secondary_org_context
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="anthropic",
            display_name="Old Display",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "sk-anthropic"}
            ),
            api_format="anthropic",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=False,
        ) as mock_push:
            response = put_secondary_org_client.put(
                f"/agentcc/provider-credentials/{credential.id}/",
                {"display_name": "Saved But Unsynced"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert result["gateway_synced"] is False
        assert result["gateway_warning"]
        mock_push.assert_called_once_with(organization)

        credential.refresh_from_db()
        assert credential.display_name == "Saved But Unsynced"
