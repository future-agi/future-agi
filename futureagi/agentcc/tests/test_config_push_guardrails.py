"""
Regression test for the tool-permissions guardrail provider name.

The dashboard's guardrail catalog calls this check "tool-permissions". When
it is pushed to the gateway, ``_transform_guardrails`` must fill in a
``provider`` the gateway's dynamic guardrail factory recognizes
(``toolperm.IsToolPermConfig`` in agentcc-gateway, which only matches the
singular ``tool_permission``). A mismatch here makes the gateway silently
build no guardrail for the check instead of erroring, so a denied tool call
is let through. See issue #3230.
"""

from agentcc.services.config_push import _RULE_PROVIDER_DEFAULTS, _transform_guardrails


def test_tool_permissions_provider_default_matches_gateway_recognizer():
    assert _RULE_PROVIDER_DEFAULTS["tool-permissions"] == "tool_permission"


def test_transform_guardrails_checks_list_fills_tool_permission_provider():
    """Shape produced by guardrail_sync.sync_guardrail_policies (merged policies)."""
    guardrails_data = {
        "enabled": True,
        "checks": [
            {
                "name": "tool-permissions",
                "enabled": True,
                "action": "block",
                "config": {"mode": "denylist", "tools": "db_delete"},
            }
        ],
    }

    result = _transform_guardrails(guardrails_data)

    assert (
        result["checks"]["tool-permissions"]["config"]["provider"] == "tool_permission"
    )


def test_transform_guardrails_rules_list_fills_tool_permission_provider():
    """Shape produced by the org-config UI's ``rules`` array."""
    guardrails_data = {
        "rules": [
            {
                "name": "tool-permissions",
                "enabled": True,
                "action": "block",
                "config": {"mode": "denylist", "tools": "db_delete"},
            }
        ],
    }

    result = _transform_guardrails(guardrails_data)

    assert (
        result["checks"]["tool-permissions"]["config"]["provider"] == "tool_permission"
    )
