"""G8 default-off cap policy: central transaction and K1 behavioral regressions."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.log_ingestion import _budget_dedupe_key, _budget_k1
from agentcc.services.org_config_writer import activate_config_version


def cap_sections():
    return {
        "budgets": {
            "enabled": True,
            "admission_caps_enabled": True,
            "no_max_tokens": "clamp",
            "agents": {"agent-a": {"limit": 0.05, "period": "daily"}},
            "key_ids": {"key-a": {"limit": 0.05, "period": "daily"}},
        },
        "routing": {"agents": {"key_bindings": {"key-a": "agent-a"}}},
    }


def cap_entry(level="agent:agent-a"):
    return {
        "timestamp": "2026-10-27T12:00:00Z",
        "error_code": "org_budget_exceeded",
        "status_code": 429,
        "metadata": {
            "budget_exceeded_level": level,
            "budget_period": "daily",
            "budget_period_start": "2026-10-27T00:00:00Z",
            "budget_action": "block",
        },
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_g8_cap_already_active_target_is_validated(organization):
    sections = cap_sections()
    sections["budgets"]["fail_mode"] = "open"
    stored = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **sections
    )
    push = MagicMock(return_value=True)
    with pytest.raises(OrgConfigRejected):
        activate_config_version(stored, push=push)
    stored.refresh_from_db()
    assert stored.is_active
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == 1
    )
    push.assert_not_called()


@pytest.mark.parametrize("level", ["agent:agent-a", "key_id:key-a"])
def test_g8_cap_k1_preserves_authoritative_level(level):
    entry = cap_entry(level)
    parsed = _budget_k1(entry)
    assert parsed is not None, "G8_BEHAVIORAL_RED: authoritative cap level discarded"
    assert parsed["level"] == level
    assert parsed["period"] == "daily"
    assert parsed["action"] == "block"
    assert _budget_dedupe_key(entry) == ("level", level, "2026-10-27T00:00:00Z")
    another = deepcopy(entry)
    another["metadata"]["budget_exceeded_level"] += "-other"
    assert _budget_dedupe_key(another) != _budget_dedupe_key(entry)


def test_g8_cap_k1_legacy_control():
    assert _budget_k1(cap_entry("key:display-name"))["level"] == "key:display-name"
    assert _budget_k1(cap_entry("agent:")) is None
    assert _budget_k1(cap_entry("key_id:")) is None
