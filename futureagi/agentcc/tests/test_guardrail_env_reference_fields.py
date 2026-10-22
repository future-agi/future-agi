"""Which guardrail strings Django refuses a "${" in: exactly the settings the
gateway refuses it in for org config, found the same way, in every shape Django
stores guardrail config in. TestMatchesTheGateway reads those lists from the
gateway source.
"""

import re
from pathlib import Path

import pytest

from agentcc.services.config_push import _transform_guardrails
from agentcc.validators import (
    EXTERNAL_GUARDRAIL_PROVIDERS,
    FUTUREAGI_RESOLVED_SETTINGS,
    GATEWAY_BUILTIN_GUARDRAILS,
    find_checks_env_reference,
    find_guardrail_env_reference,
)

_GATEWAY = Path(__file__).resolve().parents[3] / "agentcc-gateway"


def _fi(**config):
    return {"provider": "futureagi", "eval_id": "e", **config}


class TestFindGuardrailEnvReference:
    @pytest.mark.parametrize(
        "guardrails, want",
        [
            # futureagi: api_key, secret_key and base_url, first in that order.
            (
                {"checks": {"x": {"config": _fi(api_key="${A}", secret_key="${S}")}}},
                "g.checks.x.config.api_key",
            ),
            (
                {"checks": {"x": {"config": _fi(base_url="https://h/${B}")}}},
                "g.checks.x.config.base_url",
            ),
            (
                {"checks": {"x": {"config": _fi(secret_key="p-${S}")}}},
                "g.checks.x.config.secret_key",
            ),
            # A rules list and a checks list, named by index.
            (
                {
                    "rules": [
                        {"name": "y", "config": {}},
                        {"name": "x", "config": _fi(api_key="${A}")},
                    ]
                },
                "g.rules[1].config.api_key",
            ),
            (
                {"checks": [{"name": "x", "config": _fi(api_key="${A}")}]},
                "g.checks[0].config.api_key",
            ),
            # The provider a console name implies, when the config names none.
            (
                {"rules": [{"name": "futureagi-eval", "config": {"api_key": "${A}"}}]},
                "g.rules[0].config.api_key",
            ),
            (
                {"checks": {"lakera-guard": {"config": {"endpoint": "${E}"}}}},
                "g.checks.lakera-guard.config.endpoint",
            ),
            # External: every top-level string setting, sorted.
            (
                {
                    "checks": {
                        "cs": {
                            "config": {
                                "provider": "crowdstrike",
                                "client_secret": "${C}",
                                "region": "${R}",
                            }
                        }
                    }
                },
                "g.checks.cs.config.client_secret",
            ),
            # futureagi is built before a webhook, so a url does not change that.
            (
                {"checks": {"x": {"config": _fi(url="https://h", api_key="${A}")}}},
                "g.checks.x.config.api_key",
            ),
            # A disabled check is checked as well: it runs once enabled.
            (
                {"checks": {"x": {"enabled": False, "config": _fi(api_key="${A}")}}},
                "g.checks.x.config.api_key",
            ),
        ],
    )
    def test_refused(self, guardrails, want):
        assert find_guardrail_env_reference(guardrails, "g") == want

    @pytest.mark.parametrize(
        "guardrails",
        [
            # Built-in checks never resolve: blocklist words, topic lists.
            {"checks": {"keyword-blocklist": {"config": {"words": ["${jndi:"]}}}},
            {"rules": [{"name": "topic-restriction", "config": {"denied": ["${x}"]}}]},
            # A built-in under its console name, even with a provider set.
            {"rules": [{"name": "pii-detector", "config": _fi(api_key="${A}")}]},
            # Expression text and webhook settings.
            {"checks": {"e": {"config": {"expression": 'content contains "${"'}}}},
            {
                "checks": {
                    "w": {"config": {"url": "https://h/${p}", "headers": {"A": "${T}"}}}
                }
            },
            # A webhook with an external provider value is a webhook.
            {
                "checks": {
                    "w": {
                        "config": {
                            "provider": "lakera",
                            "url": "https://h",
                            "api_key": "${A}",
                        }
                    }
                }
            },
            # futureagi's other settings.
            {
                "checks": {
                    "x": {
                        "config": _fi(call_type="${c}", model="${m}", eval_ids=["${e}"])
                    }
                }
            },
            # External nested values and lists.
            {
                "checks": {
                    "g": {"config": {"provider": "grayswan", "rules": {"r": "${jndi:"}}}
                }
            },
            {
                "checks": {
                    "l": {"config": {"provider": "lakera", "categories": ["${c}"]}}
                }
            },
            # Tool-permission and MCP-security checks.
            {
                "checks": {
                    "t": {"config": {"provider": "tool_permission", "denied": "${x}"}}
                }
            },
            {"checks": {"m": {"config": {"provider": "mcp_security", "note": "${x}"}}}},
            # Names and map keys.
            {"checks": {"${EXAMPLE_VAR}": {"config": {}}}},
            {"rules": [{"name": "${EXAMPLE_VAR}", "config": {"${k}": "v"}}]},
            {
                "checks": {
                    "x": {"config": _fi(api_key="k", secret_key="s", **{"${k}": "v"})}
                }
            },
            # An unknown provider is built by nothing.
            {"checks": {"u": {"config": {"provider": "unknown", "api_key": "${A}"}}}},
            # Shapes that hold no checks.
            {},
            {"checks": None, "rules": "x"},
            None,
        ],
    )
    def test_free(self, guardrails):
        assert find_guardrail_env_reference(guardrails, "g") is None

    def test_policy_checks_list(self):
        checks = [
            {"name": "keyword-blocklist", "config": {"words": ["${jndi:"]}},
            {"name": "x", "config": _fi(base_url="${B}")},
        ]
        assert (
            find_checks_env_reference(checks, "checks") == "checks[1].config.base_url"
        )
        assert find_checks_env_reference(checks[:1], "checks") is None


class TestMatchesTheGateway:
    """Django's lists are the gateway's, read from its source (the same pattern
    as test_reserved_key_metadata_parity.py)."""

    def test_external_providers(self):
        source = (_GATEWAY / "internal/guardrails/external/external.go").read_text()
        body = re.search(
            r"func IsExternalProviderConfig\(.*?switch provider \{(.*?)return true",
            source,
            re.DOTALL,
        )
        assert body, "IsExternalProviderConfig's switch not found"
        gateway = set(re.findall(r'"([a-z_]+)"', body.group(1)))
        assert gateway == set(EXTERNAL_GUARDRAIL_PROVIDERS)

    def test_builtin_guardrails(self):
        source = (_GATEWAY / "cmd/agentcc/main.go").read_text()
        body = re.search(
            # The factory takes the guarded egress dialer: match any parameter list.
            r"func newDynamicGuardrailFactory\([^)]*\).*?switch name \{(.*?)\n\t\t\}",
            source,
            re.DOTALL,
        )
        assert body, "newDynamicGuardrailFactory's switch not found"
        gateway = set(re.findall(r'case "([a-z-]+)":', body.group(1)))
        assert gateway == set(GATEWAY_BUILTIN_GUARDRAILS)

    def test_futureagi_settings(self):
        source = (_GATEWAY / "internal/guardrails/futureagi/futureagi.go").read_text()
        call = re.search(r"guardrails\.EnvReferenceField\(cfg, ([^)]*)\)", source)
        assert call, "futureagi.New's EnvReferenceField call not found"
        gateway = tuple(re.findall(r'"([a-z_]+)"', call.group(1)))
        assert gateway == FUTUREAGI_RESOLVED_SETTINGS


class TestPushDefaultsByShape:
    """What the gateway receives for a check stored without an action or
    "enabled": the rules list and the checks list (policy sync) fill in
    enabled and action block; the checks object passes the entry as stored,
    and the gateway reads a missing action as log and a missing "enabled" as
    off (tenant.GuardrailCheck, parseAction)."""

    CONFIG = {
        "provider": "futureagi",
        "api_key": "k",
        "secret_key": "s",
        "eval_id": "e",
    }

    def test_rules_list(self):
        pushed = _transform_guardrails(
            {"rules": [{"name": "x", "config": dict(self.CONFIG)}]}
        )
        assert pushed["checks"]["x"]["enabled"] is True
        assert pushed["checks"]["x"]["action"] == "block"

    def test_checks_list(self):
        pushed = _transform_guardrails(
            {"checks": [{"name": "x", "config": dict(self.CONFIG)}]}
        )
        assert pushed["checks"]["x"]["enabled"] is True
        assert pushed["checks"]["x"]["action"] == "block"

    def test_checks_object(self):
        pushed = _transform_guardrails({"checks": {"x": {"config": dict(self.CONFIG)}}})
        assert "action" not in pushed["checks"]["x"]
        assert "enabled" not in pushed["checks"]["x"]
