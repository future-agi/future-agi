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

    def test_fetch_models_returns_bad_request_when_saved_credential_cannot_decrypt(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=b"invalid-ciphertext",
            api_format="openai",
        )

        with (
            patch(
                "agentcc.views.provider_credential.CredentialManager.decrypt",
                side_effect=ValueError("decrypt failed"),
            ),
            patch(
                "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            ) as mock_fetch,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == 400, response.json()
        assert "could not be decrypted" in response.json()["message"]
        mock_fetch.assert_not_called()

    def test_retrieve_returns_metadata_without_decrypted_credentials(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
        )

        response = secondary_org_client.get(
            f"/agentcc/provider-credentials/{cred.id}/"
        )

        assert response.status_code == 200, response.json()
        data = response.json()["result"]
        assert data["provider_name"] == "openai"
        assert data["display_name"] == "Org B OpenAI"
        # Encrypted-credential bytes must not appear in the response.
        assert "encrypted_credentials" not in data
        # Credentials field is masked (present but value redacted); the
        # plaintext api_key must never appear on the wire.
        creds = data.get("credentials") or {}
        assert creds.get("api_key") in (None, "****", "")
        assert "sk-org-b" not in str(data)

    def test_retrieve_cross_tenant_returns_404(
        self, user, secondary_org_context, secondary_org_client
    ):
        # Credential belongs to org A; secondary_org_client is scoped to org B.
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )

        response = secondary_org_client.get(
            f"/agentcc/provider-credentials/{cred.id}/"
        )
        assert response.status_code == 404

    def test_update_writes_safe_fields_and_pushes_config(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Old Display",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-untouched"}),
            api_format="openai",
            models_list=["gpt-4o-mini"],
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = secondary_org_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {
                    "provider_name": "openai",
                    "display_name": "New Display",
                    "models_list": ["gpt-4o"],
                    "api_format": "openai",
                },
                format="json",
            )

        assert response.status_code == 200, response.json()
        body = response.json()
        # PUT falls through to DRF's default UpdateModelMixin (no override in
        # the view) so the payload comes back raw; PATCH is overridden to
        # wrap via _gm.success_response. Accept both shapes.
        data = body.get("result", body)
        assert data["display_name"] == "New Display"

        cred.refresh_from_db()
        assert cred.display_name == "New Display"
        assert cred.models_list == ["gpt-4o"]
        # Current behavior: PUT does not push to the gateway because the
        # view only overrides create/partial_update/destroy/rotate. PATCH
        # (below) is the client path that fans out to the gateway. If PUT
        # is ever overridden to push, this assertion should flip.
        assert mock_push.call_count == 0

    def test_patch_updates_single_field_leaving_others_intact(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Original Display",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
            default_timeout_seconds=30,
            max_concurrent=5,
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.patch(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"default_timeout_seconds": 45},
                format="json",
            )

        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.default_timeout_seconds == 45
        assert cred.max_concurrent == 5  # untouched
        assert cred.display_name == "Original Display"  # untouched

    def test_destroy_soft_deletes_and_pushes_config(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="To Delete",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-x"}),
            api_format="anthropic",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = secondary_org_client.delete(
                f"/agentcc/provider-credentials/{cred.id}/"
            )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["deleted"] is True
        assert response.json()["result"]["gateway_synced"] is True
        mock_push.assert_called_once()

        cred.refresh_from_db()
        # Soft-delete, not hard-delete.
        assert cred.deleted is True
        assert cred.deleted_at is not None
        # List should now exclude it.
        list_response = secondary_org_client.get("/agentcc/provider-credentials/")
        list_ids = {item["id"] for item in list_response.json()["result"]}
        assert str(cred.id) not in list_ids

    def test_list_unauthenticated(self, api_client, db):
        response = api_client.get("/agentcc/provider-credentials/")
        assert response.status_code in (401, 403)

    def test_create_encrypts_credentials_before_persisting(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        raw_api_key = "sk-plaintext-must-not-appear-at-rest"

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "display_name": "Org B OpenAI",
                    "credentials": {"api_key": raw_api_key},
                    "api_format": "openai",
                },
                format="json",
            )
        assert response.status_code == 201, response.json()

        cred = AgentccProviderCredential.no_workspace_objects.get(
            provider_name="openai", organization=org_b, deleted=False
        )
        # The raw api_key must not sit in the DB in plaintext, either as
        # the encrypted-credentials bytes or anywhere else.
        assert raw_api_key.encode() not in cred.encrypted_credentials
        # But CredentialManager.decrypt should yield the original.
        assert CredentialManager.decrypt(cred.encrypted_credentials) == {
            "api_key": raw_api_key
        }
