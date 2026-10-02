"""
Phase 2: UsageEvent + CheckResult Schema Tests

Tests that the core billing data contracts validate correctly.
"""

from datetime import datetime

import pytest

from ee.usage.schemas.events import CheckResult, UpgradeCTA, UsageEvent
from ee.usage.utils.event_properties import (
    llm_usage_properties,
    token_usage_properties,
)

# ---------------------------------------------------------------------------
# UsageEvent
# ---------------------------------------------------------------------------


class TestUsageEvent:
    def test_default_values(self):
        event = UsageEvent(org_id="org-1", event_type="test")
        assert event.event_id  # auto-generated
        assert len(event.event_id) == 36  # UUID format
        assert event.amount == 1
        assert event.properties == {}
        assert isinstance(event.timestamp, datetime)

    def test_with_all_fields(self):
        ts = datetime(2026, 3, 29, 12, 0, 0)
        event = UsageEvent(
            event_id="custom-id",
            org_id="org-123",
            event_type="turing_large_evaluator",
            timestamp=ts,
            amount=20,
            properties={"source": "tracer", "model": "claude-opus"},
        )
        assert event.event_id == "custom-id"
        assert event.org_id == "org-123"
        assert event.event_type == "turing_large_evaluator"
        assert event.timestamp == ts
        assert event.amount == 20
        assert event.properties["source"] == "tracer"

    def test_rejects_empty_org_id(self):
        with pytest.raises(Exception):
            UsageEvent(org_id="", event_type="test")

    def test_rejects_empty_event_type(self):
        with pytest.raises(Exception):
            UsageEvent(org_id="org-1", event_type="")

    def test_rejects_long_event_type(self):
        with pytest.raises(Exception):
            UsageEvent(org_id="org-1", event_type="x" * 51)

    def test_rejects_negative_amount(self):
        with pytest.raises(Exception):
            UsageEvent(org_id="org-1", event_type="test", amount=-1)

    def test_zero_amount_is_valid(self):
        event = UsageEvent(org_id="org-1", event_type="test", amount=0)
        assert event.amount == 0

    def test_json_serializable(self):
        event = UsageEvent(org_id="org-1", event_type="test", amount=42)
        dumped = event.model_dump(mode="json")
        assert isinstance(dumped, dict)
        assert dumped["org_id"] == "org-1"
        assert dumped["amount"] == 42
        assert isinstance(dumped["timestamp"], str)

    def test_unique_event_ids(self):
        e1 = UsageEvent(org_id="org-1", event_type="test")
        e2 = UsageEvent(org_id="org-1", event_type="test")
        assert e1.event_id != e2.event_id

    def test_max_length_event_type_is_valid(self):
        event = UsageEvent(org_id="org-1", event_type="x" * 50)
        assert len(event.event_type) == 50


class TestUsageEventTokenProperties:
    def test_token_usage_properties_normalizes_input_output_tokens(self):
        assert token_usage_properties(
            {"input_tokens": "10", "output_tokens": 5}
        ) == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def test_token_usage_properties_preserves_explicit_zero_values(self):
        assert token_usage_properties(
            {
                "prompt_tokens": 0,
                "input_tokens": 12,
                "completion_tokens": 0,
                "output_tokens": 8,
            }
        ) == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_llm_usage_properties_reads_nested_llm(self):
        class LLM:
            token_usage = {
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "total_tokens": 27,
            }

        class Agent:
            llm = LLM()

        assert llm_usage_properties(Agent()) == {
            "prompt_tokens": 20,
            "completion_tokens": 7,
            "total_tokens": 27,
        }

    def test_llm_usage_properties_prefers_direct_zero_usage(self):
        class LLM:
            token_usage = {
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "total_tokens": 27,
            }

        class Agent:
            token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            llm = LLM()

        assert llm_usage_properties(Agent()) == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_allowed(self):
        result = CheckResult(allowed=True)
        assert result.allowed is True
        assert result.reason == ""
        assert result.error_code == ""
        assert result.upgrade_cta is None

    def test_denied_with_details(self):
        result = CheckResult(
            allowed=False,
            error_code="FREE_TIER_LIMIT",
            reason="AI credit limit reached",
            dimension="ai_credits",
            current_usage=2000,
            limit=2000,
        )
        assert result.allowed is False
        assert result.error_code == "FREE_TIER_LIMIT"
        assert result.dimension == "ai_credits"
        assert result.current_usage == 2000

    def test_with_upgrade_cta(self):
        cta = UpgradeCTA(text="Upgrade to PAYG", plan="payg")
        result = CheckResult(allowed=False, upgrade_cta=cta)
        assert result.upgrade_cta is not None
        assert result.upgrade_cta.plan == "payg"

    def test_immutable(self):
        result = CheckResult(allowed=True)
        with pytest.raises(Exception):
            result.allowed = False  # type: ignore


# ---------------------------------------------------------------------------
# UpgradeCTA
# ---------------------------------------------------------------------------


class TestUpgradeCTA:
    def test_immutable(self):
        cta = UpgradeCTA(text="Upgrade", plan="payg")
        with pytest.raises(Exception):
            cta.plan = "boost"  # type: ignore
