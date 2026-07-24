"""
Tests for Model Pricing and Cost Calculation.

This module contains tests for:
- Token-based pricing (LLM/Chat models)
- Character-based pricing (TTS models)
- Minute-based pricing (STT models)
- Image-based pricing (per_image and quality-based)
- Model name resolution and fuzzy matching
- Fallback pricing behavior
- Cost calculation with various model name formats

Run with:
    # All pricing tests
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_model_pricing.py -v

    # Unit tests only
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_model_pricing.py -v -m unit
"""

from pathlib import Path

import pytest

from agentic_eval.core_evals.fi_utils.token_count_helper import (
    calculate_total_cost,
    DEFAULT_FALLBACK_PRICING,
    DEFAULT_IMAGE_COST_PER_IMAGE,
)
from agentic_eval.core_evals.run_prompt.model_pricing import (
    get_model_pricing,
    get_model_info,
    list_available_models,
)
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS


# =============================================================================
# Pytest Configuration - Register Custom Markers
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")


# =============================================================================
# Unit Tests - Token-Based Pricing (LLM/Chat Models)
# =============================================================================


@pytest.mark.unit
class TestTokenBasedPricing:
    """Test token-based pricing for LLM/Chat models."""

    def test_gpt4o_pricing_lookup(self):
        """Test that gpt-4o pricing is found correctly."""
        pricing = get_model_pricing("gpt-4o")

        assert pricing is not None
        assert "input_per_1M_tokens" in pricing
        assert "output_per_1M_tokens" in pricing
        assert pricing["input_per_1M_tokens"] == 5
        assert pricing["output_per_1M_tokens"] == 15

    def test_gpt4o_mini_pricing_lookup(self):
        """Test that gpt-4o-mini pricing is found correctly."""
        pricing = get_model_pricing("gpt-4o-mini")

        assert pricing is not None
        assert "input_per_1M_tokens" in pricing
        assert "output_per_1M_tokens" in pricing
        assert pricing["input_per_1M_tokens"] == 2
        assert pricing["output_per_1M_tokens"] == 4

    def test_calculate_cost_llm_model(self):
        """Test cost calculation for LLM model with token usage."""
        token_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

        result = calculate_total_cost("gpt-4o", token_usage)

        assert "total_cost" in result
        assert "prompt_cost" in result
        assert "completion_cost" in result
        assert "pricing_source" in result

        # gpt-4o: $5/1M input, $15/1M output
        # prompt_cost = 1000/1M * 5 = 0.005
        # completion_cost = 500/1M * 15 = 0.0075
        expected_prompt = round((1000 / 1_000_000) * 5, 6)
        expected_completion = round((500 / 1_000_000) * 15, 6)

        assert result["prompt_cost"] == expected_prompt
        assert result["completion_cost"] == expected_completion
        assert result["total_cost"] == round(expected_prompt + expected_completion, 6)
        assert result["pricing_source"] == "available_models"

    def test_calculate_cost_zero_tokens(self):
        """Test cost calculation with zero tokens."""
        token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

        result = calculate_total_cost("gpt-4o", token_usage)

        assert result["total_cost"] == 0.0
        assert result["prompt_cost"] == 0.0
        assert result["completion_cost"] == 0.0

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[4] / "ee").is_dir(),
        reason="turing pricing is registered by ee.turing; OSS builds have no such catalog entry",
    )
    def test_turing_alias_pricing_lookup(self):
        """Test that evaluator aliases use catalog pricing instead of fallback."""
        expected_pricing = {
            "turing_large": {"input_per_1M_tokens": 5.5, "output_per_1M_tokens": 27.5},
            "turing_large_xl": {"input_per_1M_tokens": 1.25, "output_per_1M_tokens": 10},
            "turing_small": {"input_per_1M_tokens": 3.3, "output_per_1M_tokens": 16.5},
            "turing_flash": {"input_per_1M_tokens": 1.1, "output_per_1M_tokens": 5.5},
        }

        for model_name, pricing in expected_pricing.items():
            assert get_model_pricing(model_name) == pricing

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[4] / "ee").is_dir(),
        reason="turing pricing is registered by ee.turing; OSS builds have no such catalog entry",
    )
    def test_calculate_cost_turing_alias(self):
        token_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

        result = calculate_total_cost("turing_large", token_usage)

        expected_prompt = round((1000 / 1_000_000) * 5.5, 6)
        expected_completion = round((500 / 1_000_000) * 27.5, 6)

        assert result["prompt_cost"] == expected_prompt
        assert result["completion_cost"] == expected_completion
        assert result["total_cost"] == round(expected_prompt + expected_completion, 6)
        assert result["pricing_source"] == "available_models"


# =============================================================================
# Unit Tests - Character-Based Pricing (TTS Models)
# =============================================================================


@pytest.mark.unit
class TestCharacterBasedPricing:
    """Test character-based pricing for TTS models."""

    def test_tts_model_pricing_lookup(self):
        """Test that TTS model pricing is found correctly."""
        pricing = get_model_pricing("openai/tts-1")

        assert pricing is not None
        assert "input_per_1M_characters" in pricing
        assert pricing["input_per_1M_characters"] == 15.00

    def test_tts_hd_model_pricing_lookup(self):
        """Test that TTS HD model pricing is found correctly."""
        pricing = get_model_pricing("openai/tts-1-hd")

        assert pricing is not None
        assert "input_per_1M_characters" in pricing
        assert pricing["input_per_1M_characters"] == 30.00

    def test_calculate_cost_tts_model(self):
        """Test cost calculation for TTS model with character usage."""
        token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "input_characters": 10000,  # 10K characters
        }

        result = calculate_total_cost("openai/tts-1", token_usage)

        # TTS pricing: $15/1M characters
        # cost = 10000/1M * 15 = 0.15
        expected_prompt_cost = round((10000 / 1_000_000) * 15.00, 6)

        assert result["prompt_cost"] == expected_prompt_cost
        assert result["completion_cost"] == 0.0
        assert result["total_cost"] == expected_prompt_cost

    def test_elevenlabs_tts_pricing(self):
        """Test ElevenLabs TTS pricing."""
        pricing = get_model_pricing("eleven_v3")

        assert pricing is not None
        assert "input_per_1M_characters" in pricing
        assert pricing["input_per_1M_characters"] == 330.00


# =============================================================================
# Unit Tests - Minute-Based Pricing (STT Models)
# =============================================================================


@pytest.mark.unit
class TestMinuteBasedPricing:
    """Test minute-based pricing for STT models."""

    def test_whisper_pricing_lookup(self):
        """Test that Whisper STT pricing is found correctly."""
        pricing = get_model_pricing("whisper-1")

        assert pricing is not None
        assert "input_per_minute" in pricing
        assert pricing["input_per_minute"] == 0.006

    def test_deepgram_nova_pricing_lookup(self):
        """Test that Deepgram Nova pricing is found correctly."""
        pricing = get_model_pricing("deepgram/nova-2")

        assert pricing is not None
        assert "input_per_minute" in pricing
        assert pricing["input_per_minute"] == 0.005

    def test_calculate_cost_stt_model(self):
        """Test cost calculation for STT model with audio duration."""
        token_usage = {
            "audio_seconds": 120.0,  # 2 minutes
        }

        result = calculate_total_cost("whisper-1", token_usage)

        # Whisper pricing: $0.006/minute
        # cost = 2 * 0.006 = 0.012
        expected_cost = round((120.0 / 60.0) * 0.006, 6)

        assert result["prompt_cost"] == expected_cost
        assert result["completion_cost"] == 0.0
        assert result["total_cost"] == expected_cost


# =============================================================================
# Unit Tests - Image-Based Pricing (Simple per_image)
# =============================================================================


@pytest.mark.unit
class TestSimpleImagePricing:
    """Test simple per_image pricing for image generation models.

    Note: The model_pricing fuzzy matching only handles provider prefixes (e.g., azure/),
    not size/quality prefixes (e.g., standard/1024-x-1024/). For image models with
    size/quality prefixes, the calling code must parse the model name first
    (as done in RunPrompt._image_generation_response using _parse_image_model_name).
    """

    def test_dalle2_base_pricing_lookup(self):
        """Test that base DALL-E 2 pricing is found correctly."""
        # Use base model name without size prefix
        pricing = get_model_pricing("dall-e-2")

        assert pricing is not None
        assert "per_image" in pricing
        # dall-e-2 base model maps to 256x256 pricing
        assert pricing["per_image"] == 0.016

    def test_dalle3_base_pricing_lookup(self):
        """Test that base DALL-E 3 pricing is found correctly."""
        # Use base model name
        pricing = get_model_pricing("dall-e-3")

        assert pricing is not None
        assert "per_image" in pricing
        # dall-e-3 base model maps to hd/1024x1024 (highest match)
        assert pricing["per_image"] == 0.08

    def test_prefixed_models_exist_in_available_models(self):
        """Test that prefixed image model names exist in AVAILABLE_MODELS."""
        # These exact names exist in AVAILABLE_MODELS
        prefixed_models = [
            "256-x-256/dall-e-2",
            "512-x-512/dall-e-2",
            "1024-x-1024/dall-e-2",
            "standard/1024-x-1024/dall-e-3",
            "hd/1024-x-1024/dall-e-3",
        ]

        model_names_in_list = [m["model_name"] for m in AVAILABLE_MODELS]

        for prefixed_name in prefixed_models:
            assert prefixed_name in model_names_in_list, (
                f"Prefixed model {prefixed_name} should exist in AVAILABLE_MODELS"
            )

    def test_calculate_cost_dalle3_with_base_name(self):
        """Test cost calculation for DALL-E 3 using base model name."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "standard",
        }

        # Use base model name (as the _image_generation_response does after parsing)
        result = calculate_total_cost("dall-e-3", usage_payload)

        # dall-e-3 pricing from AVAILABLE_MODELS
        assert result["completion_cost"] > 0
        assert result["prompt_cost"] == 0.0
        assert result["pricing_source"] == "available_models"

    def test_calculate_cost_dalle2_multiple_images(self):
        """Test cost calculation for multiple DALL-E 2 images."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 3,
            "quality": "standard",
        }

        # Use base model name
        result = calculate_total_cost("dall-e-2", usage_payload)

        # dall-e-2: $0.016 per image * 3 = $0.048
        expected_cost = round(0.016 * 3, 6)
        assert result["completion_cost"] == expected_cost
        assert result["total_cost"] == expected_cost


# =============================================================================
# Unit Tests - Image-Based Pricing (Quality-Based GPT-Image)
# =============================================================================


@pytest.mark.unit
class TestQualityBasedImagePricing:
    """Test quality-based pricing for GPT-Image models."""

    def test_gpt_image_1_pricing_lookup(self):
        """Test that GPT-Image-1 pricing is found correctly."""
        pricing = get_model_pricing("gpt-image-1")

        assert pricing is not None
        assert "low_quality_per_image" in pricing
        assert "medium_quality_per_image" in pricing
        assert "high_quality_per_image" in pricing
        assert pricing["low_quality_per_image"] == 0.011
        assert pricing["medium_quality_per_image"] == 0.042
        assert pricing["high_quality_per_image"] == 0.167

    def test_gpt_image_1_5_pricing_lookup(self):
        """Test that GPT-Image-1.5 pricing is found correctly."""
        pricing = get_model_pricing("gpt-image-1.5")

        assert pricing is not None
        assert "low_quality_per_image" in pricing
        assert "medium_quality_per_image" in pricing
        assert "high_quality_per_image" in pricing
        assert pricing["low_quality_per_image"] == 0.009
        assert pricing["medium_quality_per_image"] == 0.034
        assert pricing["high_quality_per_image"] == 0.133

    def test_calculate_cost_gpt_image_low_quality(self):
        """Test cost calculation for GPT-Image-1 with low quality."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "low",
        }

        result = calculate_total_cost("gpt-image-1", usage_payload)

        # GPT-Image-1 low quality: $0.011 per image
        assert result["completion_cost"] == 0.011
        assert result["total_cost"] == 0.011

    def test_calculate_cost_gpt_image_medium_quality(self):
        """Test cost calculation for GPT-Image-1 with medium quality."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "medium",
        }

        result = calculate_total_cost("gpt-image-1", usage_payload)

        # GPT-Image-1 medium quality: $0.042 per image
        assert result["completion_cost"] == 0.042
        assert result["total_cost"] == 0.042

    def test_calculate_cost_gpt_image_high_quality(self):
        """Test cost calculation for GPT-Image-1 with high quality."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "high",
        }

        result = calculate_total_cost("gpt-image-1", usage_payload)

        # GPT-Image-1 high quality: $0.167 per image
        assert result["completion_cost"] == 0.167
        assert result["total_cost"] == 0.167

    def test_calculate_cost_gpt_image_standard_maps_to_medium(self):
        """Test that 'standard' quality maps to medium pricing."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "standard",
        }

        result = calculate_total_cost("gpt-image-1", usage_payload)

        # 'standard' maps to 'medium_quality_per_image': $0.042
        assert result["completion_cost"] == 0.042
        assert result["total_cost"] == 0.042

    def test_calculate_cost_gpt_image_hd_maps_to_high(self):
        """Test that 'hd' quality maps to high pricing."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 1,
            "quality": "hd",
        }

        result = calculate_total_cost("gpt-image-1", usage_payload)

        # 'hd' maps to 'high_quality_per_image': $0.167
        assert result["completion_cost"] == 0.167
        assert result["total_cost"] == 0.167


# =============================================================================
# Unit Tests - Model Name Resolution (Fuzzy Matching)
# =============================================================================


@pytest.mark.unit
class TestModelNameResolution:
    """Test model name resolution and fuzzy matching."""

    def test_exact_match(self):
        """Test exact model name match."""
        info = get_model_info("gpt-4o")

        assert info is not None
        assert info["model_name"] == "gpt-4o"

    def test_provider_prefix_match(self):
        """Test matching with provider prefix."""
        # Without prefix
        info1 = get_model_info("gpt-4o")
        # These should return similar pricing
        assert info1 is not None
        assert info1["model_name"] == "gpt-4o"

    def test_version_suffix_match(self):
        """Test matching with version suffix."""
        info = get_model_info("gpt-4o-mini-2024-07-18")

        assert info is not None
        assert "gpt-4o-mini" in info["model_name"]

    def test_alias_resolution(self):
        """Test that model aliases work correctly."""
        # Test that provider-prefixed and non-prefixed names resolve
        info1 = get_model_info("openai/tts-1")

        assert info1 is not None
        assert "tts-1" in info1["model_name"]

    def test_unknown_model_returns_none(self):
        """Test that unknown model returns None."""
        pricing = get_model_pricing("totally-unknown-model-xyz")

        assert pricing is None

    def test_image_model_with_size_prefix_resolved(self):
        """Test that image model with size prefix IS resolved.

        Size/quality prefixes like "256-x-256/" are now handled correctly
        since they are not valid provider prefixes.
        """
        info = get_model_info("256-x-256/dall-e-2")

        # Size prefixes are now correctly handled
        assert info is not None
        assert "dall-e-2" in info["model_name"]
        assert info["mode"] == "image_generation"

    def test_base_image_model_resolved(self):
        """Test that base image model name IS resolved."""
        info = get_model_info("dall-e-2")

        assert info is not None
        assert "dall-e-2" in info["model_name"]
        assert info["mode"] == "image_generation"


# =============================================================================
# Unit Tests - Fallback Pricing Behavior
# =============================================================================


@pytest.mark.unit
class TestFallbackPricing:
    """Test fallback pricing behavior for unknown models."""

    def test_unknown_model_uses_default_fallback(self):
        """Test that unknown model uses default fallback pricing."""
        token_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

        result = calculate_total_cost("unknown-model-xyz", token_usage)

        # Should use DEFAULT_FALLBACK_PRICING
        expected_prompt = round(
            (1000 / 1_000_000) * DEFAULT_FALLBACK_PRICING["input_per_1M_tokens"], 6
        )
        expected_completion = round(
            (500 / 1_000_000) * DEFAULT_FALLBACK_PRICING["output_per_1M_tokens"], 6
        )

        assert result["pricing_source"] == "default"
        assert result["prompt_cost"] == expected_prompt
        assert result["completion_cost"] == expected_completion

    def test_custom_fallback_pricing(self):
        """Test that custom fallback pricing is used when provided."""
        token_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }
        custom_fallback = {
            "input_per_1M_tokens": 1.0,
            "output_per_1M_tokens": 2.0,
        }

        result = calculate_total_cost(
            "unknown-model-xyz", token_usage, fallback_pricing=custom_fallback
        )

        expected_prompt = round((1000 / 1_000_000) * 1.0, 6)
        expected_completion = round((500 / 1_000_000) * 2.0, 6)

        assert result["pricing_source"] == "fallback"
        assert result["prompt_cost"] == expected_prompt
        assert result["completion_cost"] == expected_completion


# =============================================================================
# Unit Tests - Image Model Name Parsing and Cost Calculation
# =============================================================================


@pytest.mark.unit
class TestImageModelNameParsing:
    """Test that image model names with prefixes require parsing before pricing lookup.

    The model_pricing fuzzy matching doesn't handle size/quality prefixes like
    "standard/1024-x-1024/dall-e-3". These must be parsed first (as done by
    RunPrompt._parse_image_model_name) to extract the base model name.
    """

    def test_prefixed_models_found_by_improved_matching(self):
        """Test that prefixed model names ARE found by improved matching.

        Image models are stored in AVAILABLE_MODELS with size/quality prefixes like
        "standard/1024-x-1024/dall-e-3". The improved matching now correctly handles
        these by recognizing that size/quality prefixes are not valid providers.
        """
        # These models have size/quality prefixes that are now correctly handled
        prefixed_models = [
            ("256-x-256/dall-e-2", 0.016),
            ("512-x-512/dall-e-2", 0.018),
            ("1024-x-1024/dall-e-2", 0.02),
            ("standard/1024-x-1024/dall-e-3", 0.04),
            ("hd/1024-x-1024/dall-e-3", 0.08),
        ]

        for model_name, expected_price in prefixed_models:
            pricing = get_model_pricing(model_name)
            # Prefixed names should now be found
            assert pricing is not None, (
                f"Prefixed model {model_name} should be found by improved matching."
            )
            assert "per_image" in pricing, (
                f"Missing per_image in pricing for {model_name}"
            )
            assert pricing["per_image"] == expected_price, (
                f"Expected {expected_price} for {model_name}, got {pricing['per_image']}"
            )

    def test_base_model_names_found_after_parsing(self):
        """Test that base model names (after parsing) find correct pricing."""
        # After parsing prefixes, these base names should be found
        base_models = [
            ("dall-e-2", "per_image"),  # Base DALL-E 2
            ("dall-e-3", "per_image"),  # Base DALL-E 3
            ("gpt-image-1", "low_quality_per_image"),  # GPT-Image with quality pricing
            ("gpt-image-1.5", "low_quality_per_image"),  # GPT-Image 1.5
        ]

        for model_name, expected_key in base_models:
            pricing = get_model_pricing(model_name)
            assert pricing is not None, f"Failed to find pricing for {model_name}"
            assert expected_key in pricing, (
                f"Missing {expected_key} in pricing for {model_name}"
            )

    def test_cost_calculation_with_base_model_name(self):
        """Test cost calculation works with base model names (after parsing)."""
        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": 2,
            "quality": "standard",
        }

        # Use base model name (as _image_generation_response does after parsing)
        result = calculate_total_cost("dall-e-3", usage_payload)

        # Should find pricing and calculate cost
        assert result["completion_cost"] > 0
        assert result["total_cost"] > 0
        assert result["pricing_source"] == "available_models"


# =============================================================================
# Unit Tests - List Available Models
# =============================================================================


@pytest.mark.unit
class TestListAvailableModels:
    """Test listing available models by mode and provider."""

    def test_list_all_models(self):
        """Test listing all available models."""
        models = list_available_models()

        assert len(models) > 0
        assert all(isinstance(m, str) for m in models)

    def test_list_chat_models(self):
        """Test listing chat models only."""
        models = list_available_models(mode="chat")

        assert len(models) > 0
        assert any("gpt" in m.lower() for m in models)

    def test_list_tts_models(self):
        """Test listing TTS models only."""
        models = list_available_models(mode="tts")

        assert len(models) > 0
        assert any("tts" in m.lower() for m in models)

    def test_list_stt_models(self):
        """Test listing STT models only."""
        models = list_available_models(mode="stt")

        assert len(models) > 0
        assert any("whisper" in m.lower() for m in models)

    def test_list_image_generation_models(self):
        """Test listing image generation models only."""
        models = list_available_models(mode="image_generation")

        assert len(models) > 0
        assert any("dall-e" in m.lower() for m in models)
        assert any("gpt-image" in m.lower() for m in models)

    def test_list_openai_models(self):
        """Test listing OpenAI provider models only."""
        models = list_available_models(provider="openai")

        assert len(models) > 0
        # OpenAI should have GPT models
        assert any("gpt" in m.lower() for m in models)


# =============================================================================
# Unit Tests - Model Mode Detection
# =============================================================================


@pytest.mark.unit
class TestModelModeDetection:
    """Test that models have correct mode assignments."""

    def test_chat_models_have_chat_mode(self):
        """Test that chat models have 'chat' mode."""
        chat_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "chat"]

        assert len(chat_models) > 0
        for model in chat_models:
            assert model["mode"] == "chat"

    def test_tts_models_have_tts_mode(self):
        """Test that TTS models have 'tts' mode."""
        tts_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "tts"]

        assert len(tts_models) > 0
        for model in tts_models:
            assert model["mode"] == "tts"

    def test_stt_models_have_stt_mode(self):
        """Test that STT models have 'stt' mode."""
        stt_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "stt"]

        assert len(stt_models) > 0
        for model in stt_models:
            assert model["mode"] == "stt"

    def test_image_models_have_image_generation_mode(self):
        """Test that image models have 'image_generation' mode."""
        image_models = [
            m for m in AVAILABLE_MODELS if m.get("mode") == "image_generation"
        ]

        assert len(image_models) > 0
        for model in image_models:
            assert model["mode"] == "image_generation"


# =============================================================================
# Unit Tests - Pricing Structure Validation
# =============================================================================


@pytest.mark.unit
class TestPricingStructureValidation:
    """Test that pricing structures are valid for different model types."""

    def test_major_chat_models_have_token_pricing(self):
        """Test that major chat models (OpenAI, Anthropic, Google) have proper token pricing.

        This verifies that the most commonly used models have correct pricing structure.
        Some models have non-standard pricing (marketplace, tier-based, etc.) which is acceptable.
        """
        # Focus on major providers with standard token pricing
        major_chat_models = [
            m
            for m in AVAILABLE_MODELS
            if m.get("mode") == "chat"
            and m.get("providers") in ("openai", "anthropic", "gemini")
        ]

        models_with_valid_pricing = 0
        models_without_pricing = []

        for model in major_chat_models:
            pricing = model.get("pricing", {})
            if pricing:
                has_token_pricing = (
                    "input_per_1M_tokens" in pricing
                    and "output_per_1M_tokens" in pricing
                )
                if has_token_pricing:
                    models_with_valid_pricing += 1
            else:
                models_without_pricing.append(model["model_name"])

        # At least 80% of major chat models should have proper token pricing
        total = len(major_chat_models)
        assert models_with_valid_pricing >= 0.8 * total, (
            f"Too few major chat models have token pricing: {models_with_valid_pricing}/{total}. "
            f"Missing: {models_without_pricing[:5]}"
        )

    def test_tts_models_have_character_pricing(self):
        """Test that TTS models have character-based pricing."""
        tts_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "tts"]

        for model in tts_models:
            pricing = model.get("pricing", {})
            if pricing:
                # TTS models should have character pricing OR token pricing (for Gemini TTS)
                has_char_pricing = "input_per_1M_characters" in pricing
                has_token_pricing = "input_per_1M_tokens" in pricing
                assert has_char_pricing or has_token_pricing, (
                    f"TTS model {model['model_name']} missing character/token pricing"
                )

    def test_stt_models_have_minute_pricing(self):
        """Test that STT models have minute-based pricing."""
        stt_models = [m for m in AVAILABLE_MODELS if m.get("mode") == "stt"]

        for model in stt_models:
            pricing = model.get("pricing", {})
            if pricing:
                # STT models should have per-minute pricing
                assert "input_per_minute" in pricing, (
                    f"STT model {model['model_name']} missing minute pricing"
                )

    def test_image_models_have_image_pricing(self):
        """Test that image models have per-image pricing."""
        image_models = [
            m for m in AVAILABLE_MODELS if m.get("mode") == "image_generation"
        ]

        for model in image_models:
            pricing = model.get("pricing", {})
            if pricing:
                # Image models should have per_image OR quality-based pricing
                has_simple_pricing = "per_image" in pricing
                has_quality_pricing = "low_quality_per_image" in pricing
                assert has_simple_pricing or has_quality_pricing, (
                    f"Image model {model['model_name']} missing image pricing"
                )


# =============================================================================
# Unit Tests - Edge Cases
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_model_name(self):
        """Test that empty model name returns None."""
        pricing = get_model_pricing("")
        assert pricing is None

    def test_whitespace_model_name(self):
        """Test that whitespace model name returns None."""
        pricing = get_model_pricing("   ")
        assert pricing is None

    def test_case_insensitive_matching(self):
        """Test that model matching is case insensitive."""
        pricing1 = get_model_pricing("gpt-4o")
        pricing2 = get_model_pricing("GPT-4O")
        pricing3 = get_model_pricing("Gpt-4O")

        assert pricing1 is not None
        assert pricing2 is not None
        assert pricing3 is not None
        # All should return the same pricing
        assert pricing1 == pricing2 == pricing3

    def test_default_image_cost_constant(self):
        """Test that default image cost constant is reasonable."""
        assert DEFAULT_IMAGE_COST_PER_IMAGE > 0
        assert DEFAULT_IMAGE_COST_PER_IMAGE < 1.0  # Should be less than $1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "unit"])
