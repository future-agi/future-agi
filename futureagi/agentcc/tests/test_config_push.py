"""
Tests for config_push._transform_guardrails — Django org-config -> gateway
tenant-config translation.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest

from agentcc.models import AgentccOrgConfig
from agentcc.services.config_push import _build_payload, _transform_guardrails


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

    @pytest.mark.parametrize(
        ("name", "provider"),
        [
            ("futureagi-eval", "futureagi"),
            ("llama-guard", "llama_guard"),
            ("azure-content-safety", "azure_content_safety"),
            ("presidio-pii", "presidio"),
            ("lakera-guard", "lakera"),
            ("bedrock-guardrails", "bedrock_guardrails"),
            ("hiddenlayer-guard", "hiddenlayer"),
            ("aporia-guard", "aporia"),
            ("pangea-guard", "pangea"),
            ("dynamoai-guard", "dynamoai"),
            ("enkrypt-guard", "enkrypt"),
            ("ibm-ai-detector", "ibm_ai"),
            ("grayswan-guard", "grayswan"),
            ("lasso-guard", "lasso"),
            ("crowdstrike-aidr", "crowdstrike"),
            ("zscaler-guard", "zscaler"),
            ("tool-permissions", "tool_permissions"),
            ("mcp-security", "mcp_security"),
        ],
    )
    @pytest.mark.parametrize("shape", ["rules", "checks-list", "checks-dict"])
    def test_provider_defaults_match_across_input_shapes(self, name, provider, shape):
        check = {"enabled": True, "action": "block", "config": {}}
        if shape == "checks-dict":
            guardrails_data = {"checks": {name: check}}
        else:
            key = "rules" if shape == "rules" else "checks"
            guardrails_data = {key: [{"name": name, **check}]}
        original = deepcopy(guardrails_data)

        result = _transform_guardrails(guardrails_data)

        assert result["checks"][name]["config"]["provider"] == provider
        assert guardrails_data == original

    @pytest.mark.parametrize("config", [None, [], "invalid", 0])
    def test_checks_dict_initializes_non_dict_config(self, config):
        guardrails_data = {
            "checks": {"presidio-pii": {"enabled": True, "config": config}},
        }
        original = deepcopy(guardrails_data)

        result = _transform_guardrails(guardrails_data)

        assert result["checks"]["presidio-pii"]["config"] == {"provider": "presidio"}
        assert guardrails_data == original

    def test_checks_dict_normalizes_eval_ids_without_mutating_input(self):
        guardrails_data = {
            "checks": {
                "futureagi-eval": {
                    "enabled": False,
                    "action": "log",
                    "_display_name": "Synthetic evaluation",
                    "config": {"eval_ids": [76, None, "", 15]},
                },
            },
            "fail_open": False,
        }
        original = deepcopy(guardrails_data)

        result = _transform_guardrails(guardrails_data)

        assert result == {
            "checks": {
                "futureagi-eval": {
                    "enabled": False,
                    "action": "log",
                    "config": {
                        "provider": "futureagi",
                        "eval_ids": ["76", "15"],
                        "eval_id": "76",
                    },
                },
            },
            "fail_open": False,
            "pipeline_mode": "parallel",
        }
        assert guardrails_data == original
        assert _transform_guardrails(result) == result

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_provider_default_survives_gateway_contract(self, _mock_providers):
        config = AgentccOrgConfig(
            guardrails={
                "checks": {
                    "llama-guard": {
                        "enabled": True,
                        "action": "block",
                        "config": {"model": "synthetic-guard-model"},
                    },
                },
            },
        )
        original = deepcopy(config.guardrails)

        payload = _build_payload("synthetic-org", config)

        check = payload["guardrails"]["checks"]["llama-guard"]
        assert check["enabled"] is True
        assert check["action"] == "block"
        assert check["config"] == {
            "provider": "llama_guard",
            "model": "synthetic-guard-model",
        }
        assert config.guardrails == original
