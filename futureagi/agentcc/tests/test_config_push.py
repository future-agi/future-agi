"""
Tests for config_push._transform_guardrails — Django org-config -> gateway
tenant-config translation.
"""

from agentcc.services.config_push import _transform_guardrails


class TestTransformGuardrailsProviderDefaults:
    """Provider auto-fill must apply the same way regardless of whether the
    frontend saved guardrails as a `checks` dict or a `rules` list."""

    def test_rules_list_injects_provider_default(self):
        guardrails_data = {
            "rules": [
                {
                    "name": "llama-guard",
                    "enabled": True,
                    "action": "block",
                    "config": {},
                },
            ],
        }

        result = _transform_guardrails(guardrails_data)

        assert result["checks"]["llama-guard"]["config"]["provider"] == "llama_guard"

    def test_checks_dict_injects_provider_default(self):
        guardrails_data = {
            "checks": {
                "llama-guard": {"enabled": True, "action": "block", "config": {}},
            },
        }

        result = _transform_guardrails(guardrails_data)

        assert result["checks"]["llama-guard"]["config"]["provider"] == "llama_guard"

    def test_checks_dict_injects_provider_default_when_config_missing(self):
        guardrails_data = {
            "checks": {
                "presidio-pii": {"enabled": True, "action": "block"},
            },
        }

        result = _transform_guardrails(guardrails_data)

        assert result["checks"]["presidio-pii"]["config"]["provider"] == "presidio"

    def test_checks_dict_does_not_override_explicit_provider(self):
        guardrails_data = {
            "checks": {
                "llama-guard": {
                    "enabled": True,
                    "action": "block",
                    "config": {"provider": "custom_override"},
                },
            },
        }

        result = _transform_guardrails(guardrails_data)

        assert (
            result["checks"]["llama-guard"]["config"]["provider"] == "custom_override"
        )

    def test_checks_dict_leaves_non_provider_rules_untouched(self):
        guardrails_data = {
            "checks": {
                "content-moderation": {
                    "enabled": True,
                    "action": "block",
                    "config": {},
                },
            },
        }

        result = _transform_guardrails(guardrails_data)

        assert "provider" not in result["checks"]["content-moderation"]["config"]
