"""
An alert rule's severity reaches the gateway.

The Monitoring dialog and Settings > Alerting store `severity` (info, warning
or critical) on each rule, and the gateway contract carries it; a stored value
the contract cannot take is left out of the push (the gateway sends its default,
warning) rather than failing the org's whole config.
"""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.contracts.gateway_admin import AlertRuleConfig as GatewayAlertRuleConfig
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.config_push import _build_payload, push_org_config


def _rule(**extra):
    return {
        "name": "errors",
        "metric": "error_count",
        "condition": ">=",
        "threshold": 5,
        "window": "5m",
        "cooldown": "30m",
        "channels": ["ops"],
        **extra,
    }


def _config(rules):
    return AgentccOrgConfig(
        alerting={
            "enabled": True,
            "rules": rules,
            "channels": [
                {
                    "name": "ops",
                    "type": "webhook",
                    "url": "https://alerts.example.com/hook",
                }
            ],
        }
    )


@pytest.mark.unit
class TestMonitorAlertSeverityPush:
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_severity_reaches_the_push(self, _providers):
        client = MagicMock()
        with patch(
            "agentcc.services.config_push.get_gateway_client", return_value=client
        ):
            assert (
                push_org_config("org-1", _config([_rule(severity="critical")])) is True
            )

        org_id, payload = client.set_org_config.call_args.args
        assert org_id == "org-1"
        assert payload["alerting"]["rules"] == [
            {
                "name": "errors",
                "metric": "error_count",
                "condition": ">=",
                "threshold": 5.0,
                "window": "5m",
                "cooldown": "30m",
                "channels": ["ops"],
                "severity": "critical",
            }
        ]

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_every_severity_the_console_offers_is_pushed(self, _providers):
        for severity in ("info", "warning", "critical"):
            rules = _build_payload("org-1", _config([_rule(severity=severity)]))[
                "alerting"
            ]["rules"]
            assert rules[0]["severity"] == severity

    def test_the_contract_offers_exactly_three_severities(self):
        assert config_push._ALERT_RULE_SEVERITIES == frozenset(
            {"info", "warning", "critical"}
        )
        GatewayAlertRuleConfig.model_validate({"severity": "critical"})

    def test_severity_is_not_a_ui_only_key(self):
        assert "severity" not in config_push._UI_ONLY_ALERTING_KEYS

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_a_severity_in_another_case_is_pushed_lower_case(self, _providers):
        rules = _build_payload("org-1", _config([_rule(severity="Critical")]))[
            "alerting"
        ]["rules"]
        assert rules[0]["severity"] == "critical"

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_an_unknown_stored_severity_is_left_out_not_fatal(self, _providers):
        # A stored rule may hold any string as severity; the push leaves an unknown
        # one out (the gateway sends warning) and the rest of the org's config still
        # reaches the gateway.
        client = MagicMock()
        with (
            patch(
                "agentcc.services.config_push.get_gateway_client", return_value=client
            ),
            patch.object(config_push.logger, "warning") as warn,
        ):
            assert (
                push_org_config(
                    "org-1",
                    _config(
                        [_rule(severity="sev1"), _rule(name="none", severity=None)]
                    ),
                )
                is True
            )

        rules = client.set_org_config.call_args.args[1]["alerting"]["rules"]
        assert "severity" not in rules[0]
        assert rules[0]["name"] == "errors"
        assert "severity" not in rules[1]
        warned = [
            c
            for c in warn.call_args_list
            if c.args and c.args[0] == "alerting_severity_ignored"
        ]
        assert len(warned) == 1
        assert warned[0].kwargs["rule"] == "errors"

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_a_name_keyed_rule_keeps_its_severity(self, _providers):
        # The name-keyed rule shape.
        config = AgentccOrgConfig(
            alerting={
                "enabled": True,
                "rules": {
                    "errors": {
                        "metric": "error_count",
                        "condition": ">",
                        "threshold": 1,
                        "severity": "info",
                    }
                },
            }
        )
        rules = _build_payload("org-1", config)["alerting"]["rules"]
        assert rules == [
            {
                "name": "errors",
                "metric": "error_count",
                "condition": ">",
                "threshold": 1.0,
                "severity": "info",
            }
        ]
