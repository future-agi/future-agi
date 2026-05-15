"""
API Tests for Custom AI Models endpoints.

Endpoints covered:
- GET  custom-models/                    - List all custom models
- GET  custom-models/list/               - List models (simplified)
- POST custom_models/create/             - Create a custom model
- GET  custom-models/<uuid:id>/          - Get model details
- POST custom-models/<uuid:id>/          - Update model details
- POST custom_models/update-baseline/<uuid:id>/ - Update baseline
- GET  custom_models/edit/?id=<uuid>     - Get model for editing
- PATCH custom_models/edit/              - Edit model
- DELETE custom_models/delete/           - Delete models
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.custom_models import CustomAIModel
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# Base URL prefix for model_hub endpoints
BASE_URL = "/model-hub"


@pytest.mark.django_db
class CustomModelsAPITestCase(APITestCase):
    """Base test case for Custom Models API tests."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for the entire test class."""
        cls.organization = Organization.objects.create(name="Test Organization")
        cls.other_organization = Organization.objects.create(name="Other Organization")

        cls.user = User.objects.create_user(
            email="test@example.com",
            password="testpassword123",
            name="Test User",
            organization=cls.organization,
            organization_role=OrganizationRoles.OWNER,
        )

        cls.other_user = User.objects.create_user(
            email="other@example.com",
            password="testpassword123",
            name="Other User",
            organization=cls.other_organization,
            organization_role=OrganizationRoles.OWNER,
        )

        # Create workspaces for organizations (required by ensure_workspace_after_save signal)
        cls.workspace = Workspace.objects.create(
            name="Default Workspace",
            organization=cls.organization,
            is_default=True,
            created_by=cls.user,
        )
        cls.other_workspace = Workspace.objects.create(
            name="Default Workspace",
            organization=cls.other_organization,
            is_default=True,
            created_by=cls.other_user,
        )
        cls.organization_membership = OrganizationMembership.no_workspace_objects.create(
            user=cls.user,
            organization=cls.organization,
            role=OrganizationRoles.OWNER,
            is_active=True,
        )
        cls.other_organization_membership = (
            OrganizationMembership.no_workspace_objects.create(
                user=cls.other_user,
                organization=cls.other_organization,
                role=OrganizationRoles.OWNER,
                is_active=True,
            )
        )
        WorkspaceMembership.no_workspace_objects.create(
            workspace=cls.workspace,
            user=cls.user,
            role=OrganizationRoles.WORKSPACE_ADMIN,
            organization_membership=cls.organization_membership,
            is_active=True,
        )
        WorkspaceMembership.no_workspace_objects.create(
            workspace=cls.other_workspace,
            user=cls.other_user,
            role=OrganizationRoles.WORKSPACE_ADMIN,
            organization_membership=cls.other_organization_membership,
            is_active=True,
        )

    def setUp(self):
        """Set up for each test method."""
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self._model_volume_patcher = patch(
            "model_hub.views.custom_model.get_model_volume", return_value=(0, 0)
        )
        self._model_volume_patcher.start()
        # Set workspace context for API requests (used by signals)
        set_workspace_context(
            workspace=self.workspace, organization=self.organization, user=self.user
        )

    def tearDown(self):
        """Clean up workspace context after each test."""
        self._model_volume_patcher.stop()
        clear_workspace_context()

    def create_custom_model(
        self,
        user_model_id="test-model",
        provider="openai",
        input_token_cost=0.001,
        output_token_cost=0.002,
        organization=None,
        user=None,
        workspace=None,
        deleted=False,
    ):
        """Helper method to create a custom model."""
        org = organization or self.organization
        ws = workspace or (
            self.workspace if org == self.organization else self.other_workspace
        )
        return CustomAIModel.objects.create(
            user_model_id=user_model_id,
            provider=provider,
            input_token_cost=input_token_cost,
            output_token_cost=output_token_cost,
            organization=org,
            workspace=ws,
            user=user or self.user,
            key_config={"key": "test-api-key"},
            deleted=deleted,
        )


# =============================================================================
# List Endpoints Tests
# =============================================================================


@patch("model_hub.views.custom_model.get_model_volume", return_value=(0, 0))
class TestCustomModelsListView(CustomModelsAPITestCase):
    """Tests for GET custom-models/ endpoint."""

    def test_list_custom_models_empty(self, mock_volume):
        """Test listing models when none exist."""
        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_list_custom_models_returns_user_org_models_only(self, mock_volume):
        """Test that only models from user's organization are returned."""
        # Create model for user's org
        model1 = self.create_custom_model(user_model_id="my-model")

        # Create model for other org
        self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(model1.id))

    def test_list_custom_models_excludes_deleted(self, mock_volume):
        """Test that deleted models are not returned."""
        self.create_custom_model(user_model_id="active-model")
        self.create_custom_model(user_model_id="deleted-model", deleted=True)

        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["user_model_id"], "active-model")

    def test_list_custom_models_sorted_by_created_at_desc(self, mock_volume):
        """Test default sorting is by created_at descending."""
        model1 = self.create_custom_model(user_model_id="model-a")
        model2 = self.create_custom_model(user_model_id="model-b")

        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # model2 created last, should be first
        self.assertEqual(response.data["results"][0]["id"], str(model2.id))
        self.assertEqual(response.data["results"][1]["id"], str(model1.id))

    def test_list_custom_models_sort_order_asc(self, mock_volume):
        """Test sorting by user_model_id ascending."""
        self.create_custom_model(user_model_id="zebra-model")
        self.create_custom_model(user_model_id="alpha-model")

        response = self.client.get(f"{BASE_URL}/custom-models/?sort_order=asc")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["user_model_id"], "alpha-model")
        self.assertEqual(response.data["results"][1]["user_model_id"], "zebra-model")

    def test_list_custom_models_sort_order_desc(self, mock_volume):
        """Test sorting by user_model_id descending."""
        self.create_custom_model(user_model_id="alpha-model")
        self.create_custom_model(user_model_id="zebra-model")

        response = self.client.get(f"{BASE_URL}/custom-models/?sort_order=desc")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["user_model_id"], "zebra-model")
        self.assertEqual(response.data["results"][1]["user_model_id"], "alpha-model")

    def test_list_custom_models_search_query(self, mock_volume):
        """Test filtering by search query."""
        self.create_custom_model(user_model_id="gpt-4-turbo")
        self.create_custom_model(user_model_id="claude-3-opus")
        self.create_custom_model(user_model_id="gpt-3.5-turbo")

        response = self.client.get(f"{BASE_URL}/custom-models/?search_query=gpt")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        model_ids = [r["user_model_id"] for r in response.data["results"]]
        self.assertIn("gpt-4-turbo", model_ids)
        self.assertIn("gpt-3.5-turbo", model_ids)

    def test_list_custom_models_search_case_insensitive(self, mock_volume):
        """Test search is case-insensitive."""
        self.create_custom_model(user_model_id="GPT-4-Turbo")

        response = self.client.get(f"{BASE_URL}/custom-models/?search_query=gpt")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_list_custom_models_unauthenticated(self, mock_volume):
        """Test unauthenticated request is rejected."""
        self.client.force_authenticate(user=None)

        response = self.client.get(f"{BASE_URL}/custom-models/")

        # API returns 403 Forbidden for unauthenticated requests
        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )


class TestCustomModelsSimplifiedListView(CustomModelsAPITestCase):
    """Tests for GET custom-models/list/ endpoint."""

    def test_simplified_list_returns_minimal_fields(self):
        """Test simplified list returns only id, user_model_id."""
        self.create_custom_model(user_model_id="test-model")

        response = self.client.get(f"{BASE_URL}/custom-models/list/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.data["results"][0]
        self.assertIn("id", result)
        self.assertIn("user_model_id", result)
        # Should not have detailed fields like input_token_cost
        self.assertNotIn("input_token_cost", result)
        self.assertNotIn("output_token_cost", result)

    def test_simplified_list_with_limit(self):
        """Test limit parameter works."""
        for i in range(5):
            self.create_custom_model(user_model_id=f"model-{i}")

        response = self.client.get(f"{BASE_URL}/custom-models/list/?limit=2")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_simplified_list_with_search(self):
        """Test search_query parameter works."""
        self.create_custom_model(user_model_id="special-model")
        self.create_custom_model(user_model_id="regular-model")

        response = self.client.get(
            f"{BASE_URL}/custom-models/list/?search_query=special"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)


# =============================================================================
# Create Endpoint Tests
# =============================================================================


class TestCustomModelsCreateView(CustomModelsAPITestCase):
    """Tests for POST custom_models/create/ endpoint."""

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_openai_model_success(self, mock_validate):
        """Test creating an OpenAI model successfully."""
        mock_validate.return_value = True

        data = {
            "model_provider": "openai",
            "model_name": "gpt-4-turbo",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": {"key": "sk-test-key"},
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Response is wrapped: {"status": True, "result": {"status": "success", "data": {...}}}
        self.assertEqual(response.data["status"], True)
        self.assertEqual(response.data["result"]["status"], "success")
        self.assertIn("id", response.data["result"]["data"])

        # Verify model was created
        model = CustomAIModel.objects.get(id=response.data["result"]["data"]["id"])
        self.assertEqual(model.user_model_id, "gpt-4-turbo")
        self.assertEqual(model.provider, "openai")

    def test_create_model_missing_provider(self):
        """Test creating model without provider returns error."""
        data = {
            "model_name": "test-model",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_azure_model_missing_config(self, mock_validate):
        """Test Azure model requires proper config."""
        data = {
            "model_provider": "azure",
            "model_name": "gpt-4",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": {},  # Missing required Azure fields
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_azure_model_with_valid_config(self, mock_validate):
        """Test Azure model with valid config."""
        mock_validate.return_value = True

        data = {
            "model_provider": "azure",
            "model_name": "gpt-4",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": {
                "api_base": "https://my-resource.openai.azure.com",
                "api_version": "2024-02-15-preview",
                "api_key": "azure-api-key",
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_bedrock_model_missing_aws_config(self, mock_validate):
        """Test Bedrock model requires AWS credentials."""
        data = {
            "model_provider": "bedrock",
            "model_name": "anthropic.claude-v2",
            "input_token_cost": 0.008,
            "output_token_cost": 0.024,
            "config_json": {},  # Missing AWS credentials
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_bedrock_model_with_valid_config(self, mock_validate):
        """Test Bedrock model with valid AWS config."""
        mock_validate.return_value = True

        data = {
            "model_provider": "bedrock",
            "model_name": "anthropic.claude-v2",
            "input_token_cost": 0.008,
            "output_token_cost": 0.024,
            "config_json": {
                "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
                "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "aws_region_name": "us-east-1",
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_duplicate_model_name(self, mock_validate):
        """Test creating model with duplicate name returns error."""
        mock_validate.return_value = True

        # Create first model
        self.create_custom_model(user_model_id="gpt-4-turbo", provider="openai")

        # Try to create duplicate
        data = {
            "model_provider": "openai",
            "model_name": "gpt-4-turbo",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": {"key": "sk-test-key"},
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_model_validation_failure(self, mock_validate):
        """Test model creation fails when validation fails."""
        mock_validate.return_value = Exception("Invalid API key")

        data = {
            "model_provider": "openai",
            "model_name": "gpt-4-turbo",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": {"key": "invalid-key"},
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_vertex_ai_model_success(self, mock_validate):
        """Test creating a Vertex AI model successfully."""
        mock_validate.return_value = True

        data = {
            "model_provider": "vertex_ai",
            "model_name": "gemini-pro",
            "input_token_cost": 0.00025,
            "output_token_cost": 0.0005,
            "config_json": {
                "project_id": "my-gcp-project",
                "location": "us-central1",
                "credentials": {"type": "service_account"},
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify model name has vertex_ai prefix
        model_id = response.data["result"]["data"]["id"]
        model = CustomAIModel.objects.get(id=model_id)
        self.assertTrue(model.user_model_id.startswith("vertex_ai/"))

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_vertex_ai_model_missing_config(self, mock_validate):
        """Test Vertex AI model requires config_json."""
        data = {
            "model_provider": "vertex_ai",
            "model_name": "gemini-pro",
            "input_token_cost": 0.00025,
            "output_token_cost": 0.0005,
            "config_json": {},  # Empty config
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_custom_provider_model_success(self, mock_validate):
        """Test creating a custom provider model."""
        mock_validate.return_value = True

        data = {
            "model_provider": "custom",
            "model_name": "my-custom-model",
            "input_token_cost": 0.001,
            "output_token_cost": 0.002,
            "config_json": {
                "api_base": "https://my-custom-api.com/v1",
                "api_key": "custom-key",
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_sagemaker_model_success(self, mock_validate):
        """Test creating a SageMaker model."""
        mock_validate.return_value = True

        data = {
            "model_provider": "sagemaker",
            "model_name": "my-sagemaker-endpoint",
            "input_token_cost": 0.001,
            "output_token_cost": 0.002,
            "config_json": {
                "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
                "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "aws_region_name": "us-west-2",
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_sagemaker_model_missing_aws_config(self, mock_validate):
        """Test SageMaker model requires AWS credentials."""
        data = {
            "model_provider": "sagemaker",
            "model_name": "my-sagemaker-endpoint",
            "input_token_cost": 0.001,
            "output_token_cost": 0.002,
            "config_json": {},  # Missing AWS credentials
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_create_openai_model_with_custom_base_url(self, mock_validate):
        """Test creating OpenAI-compatible model with custom base URL."""
        mock_validate.return_value = True

        data = {
            "model_provider": "openai",
            "model_name": "local-llama",
            "input_token_cost": 0.0,
            "output_token_cost": 0.0,
            "config_json": {
                "key": "not-needed",
                "api_base": "http://localhost:8000/v1",
            },
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/create/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)


# =============================================================================
# Details Endpoint Tests
# =============================================================================


class TestCustomModelsDetailsView(CustomModelsAPITestCase):
    """Tests for GET/POST custom-models/<uuid:id>/ endpoint."""

    def test_get_model_details_success(self):
        """Test getting model details successfully."""
        model = self.create_custom_model(
            user_model_id="test-model",
            input_token_cost=0.01,
            output_token_cost=0.03,
        )

        response = self.client.get(f"{BASE_URL}/custom-models/{model.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user_model_id"], "test-model")
        self.assertEqual(response.data["input_token_cost"], 0.01)
        self.assertEqual(response.data["output_token_cost"], 0.03)

    def test_get_model_details_not_found(self):
        """Test getting non-existent model returns 404."""
        fake_id = uuid.uuid4()

        response = self.client.get(f"{BASE_URL}/custom-models/{fake_id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_model_details_other_org(self):
        """Test cannot get model from another organization."""
        model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(f"{BASE_URL}/custom-models/{model.id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_model_details_success(self):
        """Test updating model details via POST."""
        model = self.create_custom_model(
            user_model_id="old-name",
            input_token_cost=0.01,
            output_token_cost=0.02,
        )

        data = {
            "model_name": "new-name",
            "input_token_cost": 0.02,
            "output_token_cost": 0.04,
        }

        response = self.client.post(
            f"{BASE_URL}/custom-models/{model.id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify data was actually saved
        model.refresh_from_db()
        self.assertEqual(model.user_model_id, "new-name")
        self.assertEqual(model.input_token_cost, 0.02)
        self.assertEqual(model.output_token_cost, 0.04)

    def test_update_model_details_not_found(self):
        """Test updating non-existent model returns 404."""
        fake_id = uuid.uuid4()

        data = {
            "model_name": "new-name",
            "input_token_cost": 0.02,
            "output_token_cost": 0.04,
        }

        response = self.client.post(
            f"{BASE_URL}/custom-models/{fake_id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_model_details_other_org(self):
        """Test cannot update model from another organization."""
        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        data = {
            "model_name": "hacked-name",
            "input_token_cost": 0.02,
            "output_token_cost": 0.04,
        }

        response = self.client.post(
            f"{BASE_URL}/custom-models/{other_model.id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Verify model was not modified
        other_model.refresh_from_db()
        self.assertEqual(other_model.user_model_id, "other-model")

    def test_update_model_details_partial_update(self):
        """Test partial update only changes specified fields."""
        model = self.create_custom_model(
            user_model_id="original-name",
            input_token_cost=0.01,
            output_token_cost=0.02,
        )

        # Only update input_token_cost
        data = {
            "input_token_cost": 0.05,
        }

        response = self.client.post(
            f"{BASE_URL}/custom-models/{model.id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify only input_token_cost changed
        model.refresh_from_db()
        self.assertEqual(model.input_token_cost, 0.05)
        self.assertEqual(model.user_model_id, "original-name")  # unchanged
        self.assertEqual(model.output_token_cost, 0.02)  # unchanged

    def test_update_model_details_duplicate_name(self):
        """Test cannot update to a name that already exists."""
        self.create_custom_model(user_model_id="existing-name")
        model = self.create_custom_model(user_model_id="my-model")

        data = {
            "model_name": "existing-name",
        }

        response = self.client.post(
            f"{BASE_URL}/custom-models/{model.id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# =============================================================================
# Update Baseline Endpoint Tests
# =============================================================================


class TestUpdateBaselineView(CustomModelsAPITestCase):
    """Tests for POST custom_models/update-baseline/<uuid:id>/ endpoint."""

    def test_update_baseline_success(self):
        """Test updating baseline successfully."""
        model = self.create_custom_model()

        data = {
            "environment": "production",
            "model_version": "v2.0",
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/update-baseline/{model.id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "success")

    def test_update_baseline_not_found(self):
        """Test updating baseline for non-existent model."""
        fake_id = uuid.uuid4()

        data = {
            "environment": "production",
            "model_version": "v2.0",
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/update-baseline/{fake_id}/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_baseline_other_org(self):
        """Test cannot update baseline for another organization's model."""
        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        data = {
            "environment": "hacked",
            "model_version": "v666",
        }

        response = self.client.post(
            f"{BASE_URL}/custom_models/update-baseline/{other_model.id}/",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# =============================================================================
# Edit Endpoint Tests
# =============================================================================


class TestEditCustomModelView(CustomModelsAPITestCase):
    """Tests for GET/PATCH custom_models/edit/ endpoint."""

    def test_get_edit_model_success(self):
        """Test getting model for editing."""
        model = self.create_custom_model(
            user_model_id="test-model",
            provider="openai",
            input_token_cost=0.01,
            output_token_cost=0.03,
        )

        response = self.client.get(f"{BASE_URL}/custom_models/edit/?id={model.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Response is wrapped: {"status": True, "result": {"model_name": ..., ...}}
        self.assertEqual(response.data["result"]["model_name"], "test-model")
        self.assertEqual(response.data["result"]["model_provider"], "openai")

    def test_get_edit_model_missing_id(self):
        """Test getting model without ID returns error."""
        response = self.client.get(f"{BASE_URL}/custom_models/edit/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_edit_model_not_found(self):
        """Test getting non-existent model for editing."""
        fake_id = uuid.uuid4()

        response = self.client.get(f"{BASE_URL}/custom_models/edit/?id={fake_id}")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_edit_model_other_org(self):
        """Test cannot get another organization's model for editing."""
        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(
            f"{BASE_URL}/custom_models/edit/?id={other_model.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_patch_edit_model_success(self, mock_validate):
        """Test patching model successfully."""
        mock_validate.return_value = True

        model = self.create_custom_model(
            user_model_id="old-name",
            input_token_cost=0.01,
            output_token_cost=0.02,
        )

        data = {
            "id": str(model.id),
            "model_name": "new-name",
            "input_token_cost": 0.02,
            "output_token_cost": 0.04,
            "config_json": {"key": "new-api-key"},
        }

        response = self.client.patch(
            f"{BASE_URL}/custom_models/edit/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify update
        model.refresh_from_db()
        self.assertEqual(model.user_model_id, "new-name")
        self.assertEqual(model.input_token_cost, 0.02)

    def test_patch_edit_model_missing_id(self):
        """Test patching model without ID returns error."""
        data = {
            "model_name": "new-name",
        }

        response = self.client.patch(
            f"{BASE_URL}/custom_models/edit/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_patch_edit_model_validation_failure(self, mock_validate):
        """Test patching model fails when validation fails."""
        mock_validate.return_value = Exception("Invalid key")

        model = self.create_custom_model()

        data = {
            "id": str(model.id),
            "config_json": {"key": "invalid-key"},
        }

        response = self.client.patch(
            f"{BASE_URL}/custom_models/edit/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_edit_model_not_found(self):
        """Test patching non-existent model."""
        fake_id = uuid.uuid4()

        data = {
            "id": str(fake_id),
            "model_name": "new-name",
        }

        response = self.client.patch(
            f"{BASE_URL}/custom_models/edit/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("model_hub.views.custom_model.validate_model_working")
    def test_patch_edit_model_other_org(self, mock_validate):
        """Test cannot patch another organization's model."""
        mock_validate.return_value = True

        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        data = {
            "id": str(other_model.id),
            "model_name": "hacked-name",
            "config_json": {"key": "hacked-key"},
        }

        response = self.client.patch(
            f"{BASE_URL}/custom_models/edit/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Verify model was not modified
        other_model.refresh_from_db()
        self.assertEqual(other_model.user_model_id, "other-model")


# =============================================================================
# Delete Endpoint Tests
# =============================================================================


class TestDeleteCustomModelView(CustomModelsAPITestCase):
    """Tests for DELETE custom_models/delete/ endpoint."""

    def test_delete_single_model_success(self):
        """Test deleting a single model."""
        model = self.create_custom_model()

        data = {"ids": [str(model.id)]}

        response = self.client.delete(
            f"{BASE_URL}/custom_models/delete/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify soft delete
        model.refresh_from_db()
        self.assertTrue(model.deleted)

    def test_delete_multiple_models_success(self):
        """Test deleting multiple models."""
        model1 = self.create_custom_model(user_model_id="model-1")
        model2 = self.create_custom_model(user_model_id="model-2")

        data = {"ids": [str(model1.id), str(model2.id)]}

        response = self.client.delete(
            f"{BASE_URL}/custom_models/delete/", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify both soft deleted
        model1.refresh_from_db()
        model2.refresh_from_db()
        self.assertTrue(model1.deleted)
        self.assertTrue(model2.deleted)

    def test_delete_empty_ids_list(self):
        """Test deleting with empty IDs list."""
        data = {"ids": []}

        response = self.client.delete(
            f"{BASE_URL}/custom_models/delete/", data, format="json"
        )

        # Should succeed (no-op)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_deleted_models_not_in_list(self):
        """Test deleted models don't appear in list."""
        model = self.create_custom_model(user_model_id="to-delete")

        # Delete the model
        self.client.delete(
            f"{BASE_URL}/custom_models/delete/",
            {"ids": [str(model.id)]},
            format="json",
        )

        # Verify not in list
        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)


# =============================================================================
# Authentication Tests
# =============================================================================


class TestCustomModelsAuthentication(CustomModelsAPITestCase):
    """Tests for authentication on all endpoints."""

    def test_unauthenticated_list(self):
        """Test list endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get(f"{BASE_URL}/custom-models/")

        # API returns 403 Forbidden for unauthenticated requests
        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_simplified_list(self):
        """Test simplified list endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get(f"{BASE_URL}/custom-models/list/")

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_create(self):
        """Test create endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.post(f"{BASE_URL}/custom_models/create/", {})

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_details_get(self):
        """Test details GET endpoint requires authentication."""
        self.client.force_authenticate(user=None)
        fake_id = uuid.uuid4()

        response = self.client.get(f"{BASE_URL}/custom-models/{fake_id}/")

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_details_post(self):
        """Test details POST endpoint requires authentication."""
        self.client.force_authenticate(user=None)
        fake_id = uuid.uuid4()

        response = self.client.post(f"{BASE_URL}/custom-models/{fake_id}/", {})

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_update_baseline(self):
        """Test update baseline endpoint requires authentication."""
        self.client.force_authenticate(user=None)
        fake_id = uuid.uuid4()

        response = self.client.post(
            f"{BASE_URL}/custom_models/update-baseline/{fake_id}/", {}
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_edit_get(self):
        """Test edit GET endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get(f"{BASE_URL}/custom_models/edit/")

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_edit_patch(self):
        """Test edit PATCH endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.patch(f"{BASE_URL}/custom_models/edit/", {})

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_unauthenticated_delete(self):
        """Test delete endpoint requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.delete(f"{BASE_URL}/custom_models/delete/", {})

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )


# =============================================================================
# Organization Isolation Tests
# =============================================================================


class TestCustomModelsOrganizationIsolation(CustomModelsAPITestCase):
    """Tests for organization data isolation."""

    def test_cannot_view_other_org_models(self):
        """Test user cannot view models from other organizations."""
        other_model = self.create_custom_model(
            user_model_id="other-org-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(f"{BASE_URL}/custom-models/{other_model.id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_delete_other_org_models(self):
        """Test user cannot delete models from other organizations."""
        other_model = self.create_custom_model(
            user_model_id="other-org-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        data = {"ids": [str(other_model.id)]}

        # Should not error but should not delete
        response = self.client.delete(
            f"{BASE_URL}/custom_models/delete/", data, format="json"
        )

        # Model should still exist and not be deleted
        other_model.refresh_from_db()
        self.assertFalse(other_model.deleted)

    def test_list_only_shows_own_org_models(self):
        """Test list only shows models from user's organization."""
        my_model = self.create_custom_model(user_model_id="my-model")
        self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(my_model.id))

    def test_cannot_edit_get_other_org_model(self):
        """Test cannot get edit data for another organization's model."""
        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        response = self.client.get(
            f"{BASE_URL}/custom_models/edit/?id={other_model.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_baseline_other_org(self):
        """Test cannot update baseline for another organization's model."""
        other_model = self.create_custom_model(
            user_model_id="other-model",
            organization=self.other_organization,
            user=self.other_user,
        )

        data = {"environment": "hacked", "model_version": "v666"}

        response = self.client.post(
            f"{BASE_URL}/custom_models/update-baseline/{other_model.id}/",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# =============================================================================
# Pagination Tests
# =============================================================================


class TestCustomModelsPagination(CustomModelsAPITestCase):
    """Tests for pagination functionality."""

    def test_list_pagination_default_page_size(self):
        """Test default pagination returns limited results."""
        # Create 15 models
        for i in range(15):
            self.create_custom_model(user_model_id=f"model-{i:02d}")

        response = self.client.get(f"{BASE_URL}/custom-models/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 15)
        # Check pagination fields exist
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertIn("results", response.data)

    def test_list_pagination_page_navigation(self):
        """Test navigating between pages."""
        # Create 25 models
        for i in range(25):
            self.create_custom_model(user_model_id=f"model-{i:02d}")

        # Get first page
        response = self.client.get(f"{BASE_URL}/custom-models/?page=1&page_size=10")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])

        # Get second page
        response = self.client.get(f"{BASE_URL}/custom-models/?page=2&page_size=10")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNotNone(response.data["previous"])

        # Get third (last) page
        response = self.client.get(f"{BASE_URL}/custom-models/?page=3&page_size=10")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertIsNone(response.data["next"])
        self.assertIsNotNone(response.data["previous"])

    def test_simplified_list_custom_limit(self):
        """Test simplified list respects limit parameter."""
        for i in range(10):
            self.create_custom_model(user_model_id=f"model-{i}")

        response = self.client.get(f"{BASE_URL}/custom-models/list/?limit=3")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 3)

    def test_list_pagination_invalid_page(self):
        """Test requesting invalid page number."""
        self.create_custom_model(user_model_id="model-1")

        response = self.client.get(f"{BASE_URL}/custom-models/?page=999")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
