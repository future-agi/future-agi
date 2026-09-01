"""Tests for model_hub.utils.llm_providers catalog checks.

Regression coverage for is_model_in_catalog: it must resolve against the
same runtime model source as execution (LiteLLMModelManager), not the raw
AVAILABLE_MODELS list, which still contains deny-listed models.
"""

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils.llm_providers import is_model_in_catalog


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Catalog Check Org")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="catalog-check@example.com",
        password="testpassword123",
        name="Catalog Check User",
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


@pytest.mark.unit
class TestIsModelInCatalog:
    def test_available_catalog_model_is_in_catalog(self):
        assert is_model_in_catalog("gpt-4o-mini") is True

    def test_unknown_model_is_not_in_catalog(self):
        assert is_model_in_catalog("definitely-not-a-real-model") is False

    def test_deny_listed_model_is_not_in_catalog(self):
        # text-embedding-3-large exists in AVAILABLE_MODELS but is stripped
        # by LiteLLMModelManager._remove_failed_models, so execution would
        # fail. The catalog check must agree with the runtime source.
        assert is_model_in_catalog("text-embedding-3-large") is False

    def test_bare_tts_model_is_in_catalog(self):
        # Audio requests send "tts-1" while the catalog stores
        # "openai/tts-1"; the TTS handler accepts the bare name.
        assert is_model_in_catalog("tts-1") is True

    def test_bare_image_model_is_in_catalog(self):
        # Image requests send "dall-e-3" while the catalog stores sized
        # variants like "hd/1024-x-1024/dall-e-3"; the image handler
        # accepts the bare name.
        assert is_model_in_catalog("dall-e-3") is True

    def test_deprecated_chat_model_is_not_matched_by_suffix(self):
        # Suffix matching applies only to non-chat modes: retired chat
        # models must still be reported as unavailable.
        assert (
            is_model_in_catalog("perplexity/llama-3.1-sonar-huge-128k-online")
            is False
        )

    def test_custom_model_is_in_catalog_for_its_org(
        self, organization, workspace, user
    ):
        CustomAIModel.objects.create(
            user_model_id="my-custom-model",
            provider="openai",
            input_token_cost=0.01,
            output_token_cost=0.02,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config={"key": "test-api-key"},
        )

        assert (
            is_model_in_catalog("my-custom-model", organization_id=organization.id)
            is True
        )

    def test_custom_model_not_visible_without_org(self, organization, workspace, user):
        CustomAIModel.objects.create(
            user_model_id="my-custom-model",
            provider="openai",
            input_token_cost=0.01,
            output_token_cost=0.02,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config={"key": "test-api-key"},
        )

        assert is_model_in_catalog("my-custom-model") is False

    def test_soft_deleted_custom_model_is_not_in_catalog(
        self, organization, workspace, user
    ):
        custom_model = CustomAIModel.objects.create(
            user_model_id="my-deleted-model",
            provider="openai",
            input_token_cost=0.01,
            output_token_cost=0.02,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config={"key": "test-api-key"},
        )
        CustomAIModel.all_objects.filter(pk=custom_model.pk).update(deleted=True)

        assert (
            is_model_in_catalog("my-deleted-model", organization_id=organization.id)
            is False
        )
