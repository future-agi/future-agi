"""
Tests for ApiKeyViewSet — POST /model-hub/api-keys/

Covers the save-time provider key validation wired into
ApiKeyViewSet.create() (model_hub/views/run_prompt.py).

Run with: pytest model_hub/tests/test_api_key_viewset.py -v
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey
from tfc.middleware.workspace_context import set_workspace_context


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


class TestApiKeyViewSetCreate:
    def test_rejects_and_does_not_persist_invalid_key(self, auth_client, organization):
        with patch(
            "model_hub.views.run_prompt.validate_provider_key",
            return_value=(
                False,
                "Invalid API key for openai — the provider rejected it.",
            ),
        ):
            response = auth_client.post(
                "/model-hub/api-keys/",
                {"provider": "openai", "key": "sk-bad-key"},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ApiKey.objects.filter(
            provider="openai", organization=organization
        ).exists()

    def test_accepts_and_persists_valid_key(self, auth_client, organization):
        with patch(
            "model_hub.views.run_prompt.validate_provider_key",
            return_value=(True, None),
        ):
            response = auth_client.post(
                "/model-hub/api-keys/",
                {"provider": "openai", "key": "sk-good-key"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert ApiKey.objects.filter(
            provider="openai", organization=organization
        ).exists()

    def test_validation_skipped_provider_still_persists(
        self, auth_client, organization
    ):
        """Providers without a probe (e.g. huggingface) should save exactly
        as before — validate_provider_key fails open for them."""
        response = auth_client.post(
            "/model-hub/api-keys/",
            {"provider": "huggingface", "key": "hf-some-key"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert ApiKey.objects.filter(
            provider="huggingface", organization=organization
        ).exists()
