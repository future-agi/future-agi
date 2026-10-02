"""
Tests for ModelConfigs centralized configuration.

Tests the ModelConfigs pattern for consistency, resolution, and integration.
"""

import pytest
from agentic_eval.core.utils.model_config import ModelConfig, ModelConfigs, LiteLlmProvider


class TestModelConfigsStructure:
    """Test ModelConfigs class structure and all configurations."""

    def test_all_configs_are_model_config_instances(self):
        """All ModelConfigs attributes should be ModelConfig instances."""
        for attr_name in dir(ModelConfigs):
            if attr_name.startswith('_') or attr_name[0].islower():
                continue
            attr_value = getattr(ModelConfigs, attr_name)
            if isinstance(attr_value, ModelConfig):
                assert attr_value.provider is not None
                assert attr_value.model_name is not None
                assert attr_value.temperature is not None
                assert attr_value.max_tokens is not None

    def test_all_configs_have_valid_providers(self):
        """All configs should use valid LiteLlmProvider values."""
        valid_providers = {p.value for p in LiteLlmProvider}
        
        for attr_name in dir(ModelConfigs):
            if attr_name.startswith('_') or attr_name[0].islower():
                continue
            attr_value = getattr(ModelConfigs, attr_name)
            if isinstance(attr_value, ModelConfig):
                assert attr_value.provider in valid_providers

    def test_configs_are_immutable(self):
        """ModelConfig instances should be frozen/immutable."""
        config = ModelConfigs.TURING_LARGE
        
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            config.temperature = 0.5


class TestModelConfigsResolution:
    """Test ModelConfigs.get_config() resolution logic."""

    def test_get_config_with_exact_match(self):
        """get_config should work with full model names."""
        # Test with any known config
        test_config = ModelConfigs.TURING_LARGE
        resolved = ModelConfigs.get_config(test_config.model_name)
        assert resolved == test_config

    def test_get_config_with_short_name(self):
        """get_config should match models without provider prefix."""
        test_config = ModelConfigs.TURING_LARGE
        # Extract short name (e.g., "gemini-2.5-pro" from "vertex_ai/gemini-2.5-pro")
        short_name = test_config.model_name.split('/')[-1]
        resolved = ModelConfigs.get_config(short_name)
        assert resolved == test_config

    def test_get_config_returns_none_for_invalid(self):
        """get_config should return None for invalid model names."""
        assert ModelConfigs.get_config("invalid-model-xyz") is None
        assert ModelConfigs.get_config(None) is None
        assert ModelConfigs.get_config("") is None


class TestModelConfigsHelpers:
    """Test helper methods on ModelConfigs."""

    def test_get_temperature_for_valid_model(self):
        """get_temperature should return value for valid models."""
        test_config = ModelConfigs.TURING_LARGE
        temp = ModelConfigs.get_temperature(test_config.model_name)
        assert temp == test_config.temperature

    def test_get_temperature_for_invalid_model(self):
        """get_temperature should return None for invalid models."""
        assert ModelConfigs.get_temperature("invalid-model") is None

    def test_get_max_tokens_for_valid_model(self):
        """get_max_tokens should return value for valid models."""
        test_config = ModelConfigs.TURING_LARGE
        max_tokens = ModelConfigs.get_max_tokens(test_config.model_name)
        assert max_tokens == test_config.max_tokens

    def test_get_max_tokens_for_invalid_model(self):
        """get_max_tokens should return None for invalid models."""
        assert ModelConfigs.get_max_tokens("invalid-model") is None

    def test_get_provider_for_valid_model(self):
        """get_provider should return value for valid models."""
        test_config = ModelConfigs.TURING_LARGE
        provider = ModelConfigs.get_provider(test_config.model_name)
        assert provider == test_config.provider

    def test_get_provider_for_invalid_model(self):
        """get_provider should return None for invalid models."""
        assert ModelConfigs.get_provider("invalid-model") is None



class TestEvalRunnerIntegration:
    """Test integration with eval_runner.py."""

    def test_futureagi_model_choices_map_to_configs(self):
        """All ModelChoices should map to valid ModelConfigs."""
        from model_hub.models.choices import ModelChoices
        
        # These are the mappings used in _get_futureagi_model_config
        choice_to_config = {
            ModelChoices.TURING_SMALL.value: 'TURING_SMALL',
            ModelChoices.TURING_LARGE.value: 'TURING_LARGE',
            ModelChoices.TURING_FLASH.value: 'TURING_FLASH',
            ModelChoices.PROTECT_FLASH.value: 'PROTECT_FLASH',
            ModelChoices.PROTECT.value: 'GROQ_LLAMA_3_3_70B',
        }
        
        for choice_value, config_name in choice_to_config.items():
            config = getattr(ModelConfigs, config_name)
            assert isinstance(config, ModelConfig)
            assert config.model_name is not None
            assert config.provider is not None


class TestProviderEnum:
    """Test LiteLlmProvider enum."""

    def test_provider_enum_has_expected_values(self):
        """LiteLlmProvider should have all expected providers."""
        expected_providers = [
            "VERTEX_AI", "OPENAI", "PERPLEXITY", "ANTHROPIC",
            "AWS_BEDROCK_ANTHROPIC", "GROQ", "VLLM"
        ]
        
        for provider in expected_providers:
            assert hasattr(LiteLlmProvider, provider)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

