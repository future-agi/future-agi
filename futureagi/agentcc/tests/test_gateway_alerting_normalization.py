"""
Projection of the stored `alerting` config onto the gateway admin contract.

The column is a free-form JSONField the UI writes directly, but the contract is
`extra="forbid"`. Anything the UI stores for its own use has to be projected out
before validation, or the org's payload — and with it the whole bulk sync —
fails. See https://github.com/future-agi/future-agi/issues/3131.
"""

from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload


@pytest.mark.integration
@pytest.mark.api
class TestGatewayAlertingNormalization:
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_strips_ui_only_rule_fields(self, _mock_providers):
        # Exactly what Monitoring → Create Alert Rule stores today: a name-keyed
        # object whose entries carry `severity` and `enabled`.
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "rules": {
                    "Error Rate - Warn": {
                        "name": "Error Rate - Warn",
                        "metric": "error_count",
                        "window": "5m",
                        "enabled": True,
                        "severity": "warning",
                        "condition": ">",
                        "threshold": 10,
                    }
                },
            }
        )

        alerting = _build_payload("org-123", config)["alerting"]

        assert alerting["rules"] == [
            {
                "name": "Error Rate - Warn",
                "metric": "error_count",
                "condition": ">",
                "threshold": 10.0,
                "window": "5m",
            }
        ]

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_keeps_alerting_enabled_flag(self, _mock_providers):
        # `enabled` is a real field on AlertingConfig — only the per-rule and
        # per-channel copies are UI-only.
        config = AgentccOrgConfig(alerting={"enabled": True, "rules": []})

        assert _build_payload("org-123", config)["alerting"]["enabled"] is True

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_omits_disabled_rules(self, _mock_providers):
        # The gateway evaluates every rule it receives, so a rule the UI shows
        # as Disabled must not be sent at all.
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "rules": [
                    {
                        "name": "off",
                        "metric": "error_count",
                        "condition": ">",
                        "threshold": 1,
                        "enabled": False,
                    },
                    {
                        "name": "on",
                        "metric": "error_count",
                        "condition": ">",
                        "threshold": 2,
                        "enabled": True,
                    },
                ],
            }
        )

        rules = _build_payload("org-123", config)["alerting"]["rules"]

        assert [r["name"] for r in rules] == ["on"]

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_preserves_contract_rule_fields(self, _mock_providers):
        # The array shape Settings → Alerting writes must survive untouched.
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "rules": [
                    {
                        "name": "high-errors",
                        "metric": "error_count",
                        "condition": ">=",
                        "threshold": 10,
                        "window": "5m",
                        "cooldown": "15m",
                        "channels": ["ops"],
                    }
                ],
            }
        )

        rules = _build_payload("org-123", config)["alerting"]["rules"]

        assert rules[0]["cooldown"] == "15m"
        assert rules[0]["channels"] == ["ops"]

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_strips_ui_only_channel_fields(self, _mock_providers):
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "channels": {
                    "ops": {
                        "type": "webhook",
                        "url": "https://example.com/hook",
                        "enabled": True,
                        "severity_filter": "critical",
                    }
                },
            }
        )

        channels = _build_payload("org-123", config)["alerting"]["channels"]

        assert channels == [
            {
                "name": "ops",
                "type": "webhook",
                "url": "https://example.com/hook",
            }
        ]

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_drops_unrecognised_rule_fields(self, _mock_providers):
        # Anything the contract does not declare is dropped rather than raising,
        # so a future UI-only field cannot take the sync down again.
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "rules": [
                    {
                        "name": "r1",
                        "metric": "error_count",
                        "condition": ">",
                        "threshold": 1,
                        "notify_oncall": True,
                    }
                ],
            }
        )

        rules = _build_payload("org-123", config)["alerting"]["rules"]

        assert "notify_oncall" not in rules[0]
        assert rules[0]["name"] == "r1"

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_build_payload_tolerates_empty_alerting(self, _mock_providers):
        config = AgentccOrgConfig(alerting={})

        assert _build_payload("org-123", config)["alerting"] == {}
