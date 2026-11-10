"""
Phase 1.1: PlanChoices Enum Tests

Tests that all new billing enums exist with correct values and
that old enums are preserved for backward compatibility.
"""

import pytest

from ee.usage.models.usage import (
    BillingIntervalChoices,
    BillingMethodChoices,
    IntervalChoices,
    PlanChoices,
    SubscriptionTierChoices,
    TracingBillingModeChoices,
)

# ---------------------------------------------------------------------------
# PlanChoices
# ---------------------------------------------------------------------------


class TestPlanChoices:
    def test_has_six_values(self):
        assert len(PlanChoices) == 6

    def test_values_are_strings(self):
        for choice in PlanChoices:
            assert isinstance(choice.value, str)

    def test_free_value(self):
        assert PlanChoices.FREE.value == "free"

    def test_payg_value(self):
        assert PlanChoices.PAYG.value == "payg"

    def test_boost_value(self):
        assert PlanChoices.BOOST.value == "boost"

    def test_scale_value(self):
        assert PlanChoices.SCALE.value == "scale"

    def test_enterprise_value(self):
        assert PlanChoices.ENTERPRISE.value == "enterprise"

    def test_custom_value(self):
        assert PlanChoices.CUSTOM.value == "custom"

    def test_labels(self):
        assert PlanChoices.FREE.label == "Free"
        assert PlanChoices.PAYG.label == "Pay-as-you-go"
        assert PlanChoices.BOOST.label == "Boost"
        assert PlanChoices.SCALE.label == "Scale"
        assert PlanChoices.ENTERPRISE.label == "Enterprise"
        assert PlanChoices.CUSTOM.label == "Custom"

    def test_values_are_valid_identifiers(self):
        """All values should be lowercase, no spaces, no special chars."""
        import re

        for choice in PlanChoices:
            assert re.match(r"^[a-z_]+$", choice.value), (
                f"PlanChoices.{choice.name}.value = '{choice.value}' "
                f"is not a valid lowercase identifier"
            )

    def test_choices_returns_tuples(self):
        choices = PlanChoices.choices
        assert len(choices) == 6
        assert ("free", "Free") in choices
        assert ("payg", "Pay-as-you-go") in choices
        assert ("custom", "Custom") in choices


# ---------------------------------------------------------------------------
# BillingMethodChoices
# ---------------------------------------------------------------------------


class TestBillingMethodChoices:
    def test_has_six_values(self):
        assert len(BillingMethodChoices) == 6

    def test_default_is_card(self):
        assert BillingMethodChoices.CARD.value == "card"

    def test_all_values(self):
        values = {c.value for c in BillingMethodChoices}
        assert values == {
            "card",
            "invoice_30",
            "invoice_60",
            "wire",
            "aws_marketplace",
            "gcp_marketplace",
        }


# ---------------------------------------------------------------------------
# TracingBillingModeChoices
# ---------------------------------------------------------------------------


class TestTracingBillingModeChoices:
    def test_has_two_values(self):
        assert len(TracingBillingModeChoices) == 2

    def test_default_is_storage(self):
        assert TracingBillingModeChoices.STORAGE.value == "storage"

    def test_events_value(self):
        assert TracingBillingModeChoices.EVENTS.value == "events"


# ---------------------------------------------------------------------------
# BillingIntervalChoices
# ---------------------------------------------------------------------------


class TestBillingIntervalChoices:
    def test_has_two_values(self):
        assert len(BillingIntervalChoices) == 2

    def test_monthly_value(self):
        assert BillingIntervalChoices.MONTHLY.value == "monthly"

    def test_annual_value(self):
        assert BillingIntervalChoices.ANNUAL.value == "annual"


# ---------------------------------------------------------------------------
# Backward compatibility — old enums still work
# ---------------------------------------------------------------------------


class TestOldEnumsBackwardCompat:
    def test_subscription_tier_choices_still_exists(self):
        assert len(SubscriptionTierChoices) == 4

    def test_subscription_tier_choices_values(self):
        values = {c.value for c in SubscriptionTierChoices}
        assert values == {"free", "basic", "basic_yearly", "custom"}

    def test_interval_choices_still_exists(self):
        assert len(IntervalChoices) == 2

    def test_interval_choices_values(self):
        values = {c.value for c in IntervalChoices}
        assert values == {"month", "year"}


# ---------------------------------------------------------------------------
# Import paths
# ---------------------------------------------------------------------------


class TestImportPaths:
    def test_import_from_models_module(self):
        from ee.usage.models.usage import PlanChoices as PC

        assert PC is PlanChoices

    def test_all_enums_importable(self):
        """All new enums should be importable from ee.usage.models.usage."""
        from ee.usage.models.usage import (  # noqa: F401
            BillingIntervalChoices,
            BillingMethodChoices,
            PlanChoices,
            TracingBillingModeChoices,
        )
