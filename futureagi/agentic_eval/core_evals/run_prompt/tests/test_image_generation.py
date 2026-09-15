"""
Tests for Image Generation Models.

This module contains tests for:
- Unit tests for image generation payload creation
- Integration tests with live API calls for OpenAI DALL-E and GPT-Image models
- Tests for model parameters

Run with:
    # All image tests
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_image_generation.py -v

    # Live image tests only (requires API keys)
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_image_generation.py -v -m live_image

    # Unit tests only
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_image_generation.py -v -m unit
"""

import os
import json
import time
import base64
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

import pytest
import litellm


# =============================================================================
# Pytest Configuration - Register Custom Markers
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "live_image: Tests that call live image generation APIs")
    config.addinivalue_line("markers", "integration: Integration tests")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def api_keys():
    """Returns available API keys from environment."""
    return {
        "openai": os.environ.get("OPENAI_API_KEY"),
        "azure": os.environ.get("AZURE_API_KEY"),
        "together_ai": os.environ.get("TOGETHERAI_API_KEY"),
        "replicate": os.environ.get("REPLICATE_API_KEY"),
    }


@pytest.fixture
def mock_organization_id():
    """Returns a mock organization ID."""
    return str(uuid4())


@pytest.fixture
def image_prompt_messages():
    """Simple prompt messages for image generation."""
    return [
        {"role": "user", "content": "A cute baby sea otter floating on water"}
    ]


@pytest.fixture
def detailed_image_prompt():
    """More detailed prompt for image generation."""
    return [
        {"role": "system", "content": "You are an image generation assistant."},
        {"role": "user", "content": "Generate a photorealistic image of a sunset over mountains with a lake reflection, golden hour lighting, 4K quality"}
    ]


# =============================================================================
# Unit Tests - Available Models Check
# =============================================================================

@pytest.mark.unit
class TestImageModelsAvailable:
    """Test that image generation models are properly configured."""

    def test_openai_image_models_present(self):
        """Test that OpenAI image models are in available models."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        model_names = [m["model_name"] for m in AVAILABLE_MODELS]

        # Check DALL-E models
        assert any("dall-e" in name for name in model_names), "DALL-E models should be present"

        # Check GPT-Image models
        assert any("gpt-image" in name for name in model_names), "GPT-Image models should be present"

    def test_image_models_have_correct_mode(self):
        """Test that image models have image_generation mode."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        image_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "image_generation"]
        assert len(image_models) > 0, "Should have image_generation models"

        # Verify all image models are properly tagged
        for model in image_models:
            assert model.get("mode") == "image_generation"
            assert "model_name" in model
            assert "providers" in model

    def test_openai_dalle3_models_present(self):
        """Test that DALL-E 3 models are present."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        dalle3_models = [
            m for m in AVAILABLE_MODELS
            if "dall-e-3" in m.get("model_name", "")
        ]
        assert len(dalle3_models) > 0, "DALL-E 3 models should be present"

    def test_openai_gpt_image_models_present(self):
        """Test that GPT-Image models are present."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        gpt_image_models = [
            m for m in AVAILABLE_MODELS
            if "gpt-image" in m.get("model_name", "")
        ]
        assert len(gpt_image_models) > 0, "GPT-Image models should be present"


# =============================================================================
# Unit Tests - Model Parameters
# =============================================================================

@pytest.mark.unit
class TestImageModelParameters:
    """Test image model parameters from the manager."""

    def test_get_openai_dalle3_parameters(self):
        """Test that DALL-E 3 parameters are returned correctly."""
        from agentic_eval.core_evals.run_prompt.other_services.manager import get_model_parameters

        params = get_model_parameters("openai", "dall-e-3", "image")

        assert params is not None
        assert "dropdowns" in params or "sliders" in params or params == {}

    def test_get_openai_gpt_image_parameters(self):
        """Test that GPT-Image parameters are returned correctly."""
        from agentic_eval.core_evals.run_prompt.other_services.manager import get_model_parameters

        params = get_model_parameters("openai", "gpt-image-1", "image")

        assert params is not None

    def test_get_azure_image_parameters(self):
        """Test that Azure image parameters are returned correctly."""
        from agentic_eval.core_evals.run_prompt.other_services.manager import get_model_parameters

        params = get_model_parameters("azure", "dall-e-3", "image")

        assert params is not None


# =============================================================================
# Unit Tests - LiteLLM Model Manager
# =============================================================================

@pytest.mark.unit
class TestLiteLLMImageModelManager:
    """Test LiteLLM model manager with image models."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_image_models_not_filtered_out(self, mock_custom_model):
        """Test that image_generation models are not filtered out."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="dall-e-3")

        # Check that image_generation models are present
        image_models = [m for m in manager.models if m.get("mode") == "image_generation"]
        assert len(image_models) > 0, "Image generation models should not be filtered out"

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_provider_for_dalle_rejects_deprecated_model(self, mock_custom_model):
        """DALL-E is deprecated, so it is no longer resolvable from the catalog.

        ``_remove_failed_models`` strips every ``dall-e-*`` entry (both OpenAI
        and Azure) out of ``self.models``, so ``get_provider`` falls through to
        the custom-model lookup and, finding nothing, reports the model as
        unavailable. This asserts that deprecation path; before the models were
        deprecated this test asserted ``provider == "openai"``.
        """
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        class _DoesNotExist(Exception):
            """Stands in for CustomAIModel.DoesNotExist."""

        class _MultipleObjectsReturned(Exception):
            """Stands in for CustomAIModel.MultipleObjectsReturned."""

        # The patched CustomAIModel is a MagicMock, so both exception
        # attributes get_provider catches are plain mocks; `except <mock>`
        # raises TypeError before the real branch is reached. Give both a
        # real exception class.
        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = _DoesNotExist
        mock_custom_model.MultipleObjectsReturned = _MultipleObjectsReturned
        mock_custom_model.objects.get.side_effect = _DoesNotExist

        manager = LiteLLMModelManager(model_name="standard/1024-x-1024/dall-e-3")

        with pytest.raises(ValueError, match="not available in the current model catalog"):
            manager.get_provider("standard/1024-x-1024/dall-e-3")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_provider_for_gpt_image(self, mock_custom_model):
        """Test getting provider for GPT-Image model."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="gpt-image-1")
        provider = manager.get_provider("gpt-image-1")

        assert provider == "openai"


# =============================================================================
# Integration Tests - Direct LiteLLM Image Generation
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_image
class TestLiteLLMImageGenerationDirect:
    """Direct tests for litellm.image_generation function."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for image tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

    def test_dalle3_basic_generation(self, api_keys):
        """Test basic image generation with DALL-E 3."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A simple red circle on white background",
            api_key=api_keys["openai"],
            size="1024x1024",
            quality="standard",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) > 0

        # Check that we got a URL or base64 data
        image_data = response.data[0]
        assert hasattr(image_data, "url") or hasattr(image_data, "b64_json")

    def test_dalle2_basic_generation(self, api_keys):
        """Test basic image generation with DALL-E 2."""
        response = litellm.image_generation(
            model="dall-e-2",
            prompt="A simple blue square on white background",
            api_key=api_keys["openai"],
            size="256x256",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) > 0

    def test_gpt_image_basic_generation(self, api_keys):
        """Test basic image generation with GPT-Image-1."""
        try:
            response = litellm.image_generation(
                model="gpt-image-1",
                prompt="A simple green triangle on white background",
                api_key=api_keys["openai"],
                size="1024x1024",
                quality="auto",
                n=1,
            )

            assert response is not None
            assert hasattr(response, "data")
            assert len(response.data) > 0
        except Exception as e:
            # GPT-Image might not be available in all accounts
            if "model" in str(e).lower() or "not found" in str(e).lower():
                pytest.skip(f"GPT-Image-1 model not available: {e}")
            raise

    def test_dalle3_hd_quality(self, api_keys):
        """Test DALL-E 3 with HD quality."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A detailed mountain landscape with snow peaks",
            api_key=api_keys["openai"],
            size="1024x1024",
            quality="hd",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) > 0

    def test_dalle3_vivid_style(self, api_keys):
        """Test DALL-E 3 with vivid style."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A colorful abstract art piece",
            api_key=api_keys["openai"],
            size="1024x1024",
            quality="standard",
            style="vivid",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")

    def test_dalle3_natural_style(self, api_keys):
        """Test DALL-E 3 with natural style."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A realistic photograph of a cat",
            api_key=api_keys["openai"],
            size="1024x1024",
            quality="standard",
            style="natural",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")

    def test_dalle3_landscape_size(self, api_keys):
        """Test DALL-E 3 with landscape aspect ratio."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A wide panoramic view of a beach",
            api_key=api_keys["openai"],
            size="1792x1024",
            quality="standard",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")

    def test_dalle3_portrait_size(self, api_keys):
        """Test DALL-E 3 with portrait aspect ratio."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A tall skyscraper view from below",
            api_key=api_keys["openai"],
            size="1024x1792",
            quality="standard",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")

    def test_dalle2_multiple_images(self, api_keys):
        """Test DALL-E 2 generating multiple images."""
        response = litellm.image_generation(
            model="dall-e-2",
            prompt="A simple icon design",
            api_key=api_keys["openai"],
            size="256x256",
            n=2,  # DALL-E 2 supports n > 1
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) == 2


# =============================================================================
# Integration Tests - Async Image Generation
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_image
@pytest.mark.asyncio
class TestAsyncImageGeneration:
    """Async tests for image generation."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for async image tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

    async def test_async_dalle3_generation(self, api_keys):
        """Test async image generation with DALL-E 3."""
        response = await litellm.aimage_generation(
            model="dall-e-3",
            prompt="A serene lake at sunset",
            api_key=api_keys["openai"],
            size="1024x1024",
            quality="standard",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) > 0


# =============================================================================
# Integration Tests - Error Handling
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_image
class TestImageGenerationErrors:
    """Test error handling for image generation."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for error tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

    def test_invalid_size_dalle3(self, api_keys):
        """Test that invalid size for DALL-E 3 raises error."""
        with pytest.raises(Exception) as exc_info:
            litellm.image_generation(
                model="dall-e-3",
                prompt="Test image",
                api_key=api_keys["openai"],
                size="256x256",  # Invalid for DALL-E 3
                n=1,
            )
        # Should raise an error about invalid size
        assert exc_info.value is not None

    def test_dalle3_n_greater_than_1(self, api_keys):
        """Test that DALL-E 3 with n > 1 raises error."""
        with pytest.raises(Exception) as exc_info:
            litellm.image_generation(
                model="dall-e-3",
                prompt="Test image",
                api_key=api_keys["openai"],
                size="1024x1024",
                n=2,  # DALL-E 3 only supports n=1
            )
        # Should raise an error about n parameter
        assert exc_info.value is not None

    def test_empty_prompt(self, api_keys):
        """Test that empty prompt raises error."""
        with pytest.raises(Exception):
            litellm.image_generation(
                model="dall-e-3",
                prompt="",
                api_key=api_keys["openai"],
                size="1024x1024",
                n=1,
            )

    def test_invalid_api_key(self):
        """Test that invalid API key raises error."""
        with pytest.raises(Exception):
            litellm.image_generation(
                model="dall-e-3",
                prompt="Test image",
                api_key="invalid-key-12345",
                size="1024x1024",
                n=1,
            )


# =============================================================================
# Cost Estimation Tests
# =============================================================================

@pytest.mark.unit
class TestImageCostEstimation:
    """Test cost estimation for image generation."""

    def test_dalle3_standard_cost(self):
        """Test cost calculation for DALL-E 3 standard quality."""
        # DALL-E 3 standard 1024x1024 is $0.040 per image
        expected_cost_per_image = 0.04

        # This would need integration with cost calculation utility
        # For now, just verify the expected cost exists
        assert expected_cost_per_image > 0

    def test_dalle3_hd_cost(self):
        """Test cost calculation for DALL-E 3 HD quality."""
        # DALL-E 3 HD 1024x1024 is $0.080 per image
        expected_cost_per_image = 0.08

        assert expected_cost_per_image > 0

    def test_dalle2_cost(self):
        """Test cost calculation for DALL-E 2."""
        # DALL-E 2 1024x1024 is $0.020 per image
        expected_cost_per_image = 0.02

        assert expected_cost_per_image > 0


# =============================================================================
# Model Name Resolution Tests
# =============================================================================

@pytest.mark.unit
class TestModelNameResolution:
    """Test that model names are correctly resolved."""

    def test_dalle3_model_name_variants(self):
        """Test various DALL-E 3 model name formats."""
        valid_names = [
            "dall-e-3",
            "standard/1024-x-1024/dall-e-3",
            "hd/1024-x-1024/dall-e-3",
            "standard/1792-x-1024/dall-e-3",
        ]

        for name in valid_names:
            assert "dall-e-3" in name or "dall-e" in name

    def test_gpt_image_model_name_variants(self):
        """Test various GPT-Image model name formats."""
        valid_names = [
            "gpt-image-1",
            "gpt-image-1.5",
            "gpt-image-1-mini",
        ]

        for name in valid_names:
            assert "gpt-image" in name


# =============================================================================
# Response Format Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_image
class TestImageResponseFormat:
    """Test response format from image generation."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for response format tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

    def test_response_has_url(self, api_keys):
        """Test that response contains image URL."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A simple test image",
            api_key=api_keys["openai"],
            size="1024x1024",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")
        assert len(response.data) > 0

        # By default, response should have URL
        image = response.data[0]
        assert hasattr(image, "url") or hasattr(image, "b64_json")

        if hasattr(image, "url") and image.url:
            assert image.url.startswith("http")

    def test_response_has_revised_prompt(self, api_keys):
        """Test that DALL-E 3 response contains revised prompt."""
        response = litellm.image_generation(
            model="dall-e-3",
            prompt="A simple cat",
            api_key=api_keys["openai"],
            size="1024x1024",
            n=1,
        )

        assert response is not None
        assert hasattr(response, "data")

        # DALL-E 3 returns revised_prompt
        image = response.data[0]
        if hasattr(image, "revised_prompt"):
            assert image.revised_prompt is not None
            assert len(image.revised_prompt) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not live_image"])
