"""
Phase 1.2: OrganizationSubscription New Fields Tests

Tests that new billing fields exist, have correct defaults,
validate input, and don't break existing functionality.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from ee.usage.models.usage import (
    BillingIntervalChoices,
    BillingMethodChoices,
    CreditBalance,
    OrganizationSubscription,
    PlanChoices,
    SubscriptionTier,
    SubscriptionTierChoices,
    TracingBillingModeChoices,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def free_tier(db):
    tier, _ = SubscriptionTier.objects.get_or_create(
        name=SubscriptionTierChoices.FREE.value,
        defaults={"wallet_refill_amount": 0},
    )
    return tier


@pytest.fixture
def org(db):
    return Organization.objects.create(name="test-org")


@pytest.fixture
def workspace(db, org):
    return Workspace.objects.create(
        name="default",
        organization=org,
        is_default=True,
        is_active=True,
    )


@pytest.fixture
def subscription(db, org, free_tier):
    return OrganizationSubscription.objects.create(
        organization=org,
        subscription_tier=free_tier,
    )


# ---------------------------------------------------------------------------
# Field existence and defaults
# ---------------------------------------------------------------------------


class TestFieldDefaults:
    def test_plan_default_is_free(self, subscription):
        assert subscription.plan == PlanChoices.FREE.value

    def test_billing_method_default_is_card(self, subscription):
        assert subscription.billing_method == BillingMethodChoices.CARD.value

    def test_billing_interval_default_is_monthly(self, subscription):
        assert subscription.billing_interval == BillingIntervalChoices.MONTHLY.value

    def test_tracing_billing_mode_default_is_storage(self, subscription):
        assert (
            subscription.tracing_billing_mode == TracingBillingModeChoices.STORAGE.value
        )

    def test_stripe_subscription_id_is_nullable(self, subscription):
        assert subscription.stripe_subscription_id is None

    def test_billing_period_start_is_nullable(self, subscription):
        assert subscription.billing_period_start is None

    def test_billing_period_end_is_nullable(self, subscription):
        assert subscription.billing_period_end is None


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


class TestFieldValidation:
    def test_plan_accepts_all_valid_values(self, org, free_tier):
        for plan_choice in PlanChoices:
            sub = OrganizationSubscription(
                organization=org,
                subscription_tier=free_tier,
                plan=plan_choice.value,
            )
            sub.full_clean()  # Should not raise

    def test_plan_rejects_invalid_value(self, subscription):
        subscription.plan = "invalid_plan"
        with pytest.raises(ValidationError) as exc_info:
            subscription.full_clean()
        assert "plan" in str(exc_info.value)

    def test_billing_method_accepts_all_valid_values(self, org, free_tier):
        for method in BillingMethodChoices:
            sub = OrganizationSubscription(
                organization=org,
                subscription_tier=free_tier,
                billing_method=method.value,
            )
            sub.full_clean()

    def test_billing_method_rejects_invalid_value(self, subscription):
        subscription.billing_method = "bitcoin"
        with pytest.raises(ValidationError) as exc_info:
            subscription.full_clean()
        assert "billing_method" in str(exc_info.value)

    def test_tracing_billing_mode_rejects_invalid_value(self, subscription):
        subscription.tracing_billing_mode = "hybrid"
        with pytest.raises(ValidationError) as exc_info:
            subscription.full_clean()
        assert "tracing_billing_mode" in str(exc_info.value)

    def test_billing_interval_rejects_invalid_value(self, subscription):
        subscription.billing_interval = "weekly"
        with pytest.raises(ValidationError) as exc_info:
            subscription.full_clean()
        assert "billing_interval" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestFieldPersistence:
    def test_plan_persists(self, subscription):
        subscription.plan = PlanChoices.BOOST.value
        subscription.save()
        subscription.refresh_from_db()
        assert subscription.plan == "boost"

    def test_billing_method_persists(self, subscription):
        subscription.billing_method = BillingMethodChoices.INVOICE_30.value
        subscription.save()
        subscription.refresh_from_db()
        assert subscription.billing_method == "invoice_30"

    def test_billing_period_persists(self, subscription):
        from datetime import date

        subscription.billing_period_start = date(2026, 3, 1)
        subscription.billing_period_end = date(2026, 3, 31)
        subscription.save()
        subscription.refresh_from_db()
        assert subscription.billing_period_start == date(2026, 3, 1)
        assert subscription.billing_period_end == date(2026, 3, 31)

    def test_stripe_subscription_id_persists(self, subscription):
        subscription.stripe_subscription_id = "sub_1234567890"
        subscription.save()
        subscription.refresh_from_db()
        assert subscription.stripe_subscription_id == "sub_1234567890"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_existing_fields_still_work(self, subscription):
        """Old fields should still be readable and writable."""
        assert subscription.subscription_tier is not None
        assert subscription.wallet_balance == Decimal("0")
        assert subscription.auto_recharge_enabled is False
        assert subscription.status == "active"

    def test_str_still_works(self, subscription):
        """__str__ reads subscription_tier.name — should not crash."""
        result = str(subscription)
        assert "test-org" in result or "free" in result.lower()


# ---------------------------------------------------------------------------
# CreditBalance model
# ---------------------------------------------------------------------------


class TestCreditBalance:
    def test_create_credit_balance(self, org):
        credit = CreditBalance.objects.create(
            organization=org,
            credit_type="startup",
            original_amount=Decimal("50000.00"),
            remaining_amount=Decimal("50000.00"),
            description="YC Startup Program",
        )
        assert credit.remaining_amount == Decimal("50000.00")
        assert credit.currency == "usd"
        assert credit.expires_at is None

    def test_credit_type_choices(self, org):
        for credit_type in ["startup", "referral", "goodwill", "prepaid"]:
            credit = CreditBalance(
                organization=org,
                credit_type=credit_type,
                original_amount=Decimal("100.00"),
                remaining_amount=Decimal("100.00"),
                description=f"Test {credit_type}",
            )
            credit.full_clean()

    def test_credit_with_expiry(self, org):
        credit = CreditBalance.objects.create(
            organization=org,
            credit_type="referral",
            original_amount=Decimal("50.00"),
            remaining_amount=Decimal("50.00"),
            description="Referral credit",
            expires_at=timezone.now() + timezone.timedelta(days=365),
        )
        assert credit.expires_at is not None

    def test_credit_str(self, org):
        credit = CreditBalance.objects.create(
            organization=org,
            credit_type="prepaid",
            original_amount=Decimal("100.00"),
            remaining_amount=Decimal("75.00"),
            description="Test",
        )
        result = str(credit)
        assert "75.00" in result
        assert "100.00" in result

    def test_multiple_credits_per_org(self, org):
        CreditBalance.objects.create(
            organization=org,
            credit_type="startup",
            original_amount=Decimal("50000.00"),
            remaining_amount=Decimal("40000.00"),
            description="Startup credit",
        )
        CreditBalance.objects.create(
            organization=org,
            credit_type="referral",
            original_amount=Decimal("50.00"),
            remaining_amount=Decimal("50.00"),
            description="Referral credit",
        )
        assert CreditBalance.objects.filter(organization=org).count() == 2

    def test_credit_remaining_can_be_decremented(self, org):
        credit = CreditBalance.objects.create(
            organization=org,
            credit_type="prepaid",
            original_amount=Decimal("100.00"),
            remaining_amount=Decimal("100.00"),
            description="Test",
        )
        credit.remaining_amount = Decimal("75.50")
        credit.save()
        credit.refresh_from_db()
        assert credit.remaining_amount == Decimal("75.50")
