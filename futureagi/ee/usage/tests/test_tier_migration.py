"""Phase 1.3: Tier Migration Data Tests

Tests for RunPython migration that backfills:
- plan from subscription_tier.name
- billing_interval for yearly orgs
- CreditBalance from wallet_balance

TH-3681
"""

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from ee.usage.models.usage import (
    CreditBalance,
    OrganizationSubscription,
    PlanChoices,
    SubscriptionTier,
)


@pytest.fixture
def free_tier(db):
    tier, _ = SubscriptionTier.objects.get_or_create(
        name="free",
        defaults={"wallet_refill_amount": 0},
    )
    return tier


@pytest.fixture
def basic_tier(db):
    tier, _ = SubscriptionTier.objects.get_or_create(
        name="basic",
        defaults={"wallet_refill_amount": 20},
    )
    return tier


@pytest.fixture
def basic_yearly_tier(db):
    tier, _ = SubscriptionTier.objects.get_or_create(
        name="basic_yearly",
        defaults={"wallet_refill_amount": 20},
    )
    return tier


@pytest.fixture
def custom_tier(db):
    tier, _ = SubscriptionTier.objects.get_or_create(
        name="custom",
        defaults={"wallet_refill_amount": None},
    )
    return tier


@pytest.fixture
def unknown_tier(db):
    tier = SubscriptionTier.objects.create(
        name="legacy_unknown",
        wallet_refill_amount=0,
    )
    return tier


@pytest.fixture
def org_with_tier(db):
    def _create(tier, wallet_balance=0):
        org = Organization.objects.create(name=f"org-{tier.name}-{wallet_balance}")
        sub = OrganizationSubscription.objects.create(
            organization=org,
            subscription_tier=tier,
            wallet_balance=Decimal(str(wallet_balance)),
        )
        return org, sub

    return _create


import importlib.util
import os

import django.apps


def _load_migration():
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "migrations",
        "0015_backfill_plan_and_wallet_credits.py",
    )
    spec = importlib.util.spec_from_file_location("migration_0015", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_migration = _load_migration()
populate_plan = _migration.populate_plan
set_annual_interval = _migration.set_annual_interval
migrate_wallet_to_credits = _migration.migrate_wallet_to_credits
reverse_plan = _migration.reverse_plan
reverse_credits = _migration.reverse_credits


class SchemaEditorStub:
    connection = connection


APPS = django.apps.apps
SCHEMA_EDITOR = SchemaEditorStub()


class TestPlanMapping:
    def test_free_tier_maps_to_free_plan(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier)
        assert sub.plan == PlanChoices.FREE.value
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.FREE.value

    def test_basic_tier_maps_to_payg_plan(self, db, basic_tier, org_with_tier):
        org, sub = org_with_tier(basic_tier)
        assert sub.plan == PlanChoices.FREE.value
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.PAYG.value

    def test_basic_yearly_maps_to_payg_plan(self, db, basic_yearly_tier, org_with_tier):
        org, sub = org_with_tier(basic_yearly_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.PAYG.value

    def test_custom_tier_maps_to_enterprise_plan(self, db, custom_tier, org_with_tier):
        org, sub = org_with_tier(custom_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.ENTERPRISE.value

    def test_unknown_tier_defaults_to_free(self, db, unknown_tier, org_with_tier):
        org, sub = org_with_tier(unknown_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.FREE.value

    def test_multiple_orgs_migrated_correctly(
        self, db, free_tier, basic_tier, basic_yearly_tier, custom_tier, org_with_tier
    ):
        _, sub_free = org_with_tier(free_tier)
        _, sub_basic = org_with_tier(basic_tier)
        _, sub_yearly = org_with_tier(basic_yearly_tier)
        _, sub_custom = org_with_tier(custom_tier)

        populate_plan(APPS, SCHEMA_EDITOR)

        sub_free.refresh_from_db()
        sub_basic.refresh_from_db()
        sub_yearly.refresh_from_db()
        sub_custom.refresh_from_db()

        assert sub_free.plan == PlanChoices.FREE.value
        assert sub_basic.plan == PlanChoices.PAYG.value
        assert sub_yearly.plan == PlanChoices.PAYG.value
        assert sub_custom.plan == PlanChoices.ENTERPRISE.value


class TestBillingInterval:
    def test_basic_yearly_sets_annual_interval(
        self, db, basic_yearly_tier, org_with_tier
    ):
        org, sub = org_with_tier(basic_yearly_tier)
        assert sub.billing_interval == "monthly"
        set_annual_interval(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.billing_interval == "annual"

    def test_basic_keeps_monthly_interval(self, db, basic_tier, org_with_tier):
        org, sub = org_with_tier(basic_tier)
        set_annual_interval(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.billing_interval == "monthly"

    def test_free_keeps_monthly_interval(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier)
        set_annual_interval(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.billing_interval == "monthly"


class TestWalletMigration:
    def test_positive_wallet_creates_credit_balance(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=50)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        credit = CreditBalance.objects.filter(organization=org).first()
        assert credit is not None
        assert credit.credit_type == "prepaid"
        assert credit.original_amount == Decimal("50")
        assert credit.remaining_amount == Decimal("50")

    def test_zero_wallet_no_credit_balance(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=0)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        assert not CreditBalance.objects.filter(organization=org).exists()

    def test_negative_wallet_no_credit_balance(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=-5)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        assert not CreditBalance.objects.filter(organization=org).exists()

    def test_fractional_cents_wallet_balance(self, db, free_tier, org_with_tier):
        """wallet_balance(16,8) → CreditBalance(10,2): fractional cents are truncated."""
        org, sub = org_with_tier(free_tier, wallet_balance=Decimal("50.12345678"))
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        credit = CreditBalance.objects.get(organization=org)
        assert credit.original_amount == Decimal("50.12")
        assert credit.remaining_amount == Decimal("50.12")

    def test_credit_balance_type_is_prepaid(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=100)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        credit = CreditBalance.objects.get(organization=org)
        assert credit.credit_type == "prepaid"

    def test_credit_balance_no_expiry(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=100)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        credit = CreditBalance.objects.get(organization=org)
        assert credit.expires_at is None

    def test_credit_balance_description(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=100)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        credit = CreditBalance.objects.get(organization=org)
        assert "Migrated from legacy wallet balance" in credit.description

    def test_wallet_migration_uses_constant_queries(self, db, free_tier, org_with_tier):
        for wallet_balance in range(1, 6):
            org_with_tier(free_tier, wallet_balance=wallet_balance)

        with CaptureQueriesContext(connection) as queries:
            migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)

        assert CreditBalance.objects.count() == 5
        assert len(queries) <= 3

    def test_total_credits_equals_total_wallet(
        self, db, free_tier, basic_tier, org_with_tier
    ):
        _, sub1 = org_with_tier(free_tier, wallet_balance=100)
        _, sub2 = org_with_tier(basic_tier, wallet_balance=250)
        _, sub3 = org_with_tier(free_tier, wallet_balance=0)

        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)

        total_wallet = sum(
            sub.wallet_balance
            for sub in OrganizationSubscription.objects.filter(wallet_balance__gt=0)
        )
        total_credits = sum(
            CreditBalance.objects.values_list("remaining_amount", flat=True)
        )
        assert total_credits == total_wallet


class TestIdempotency:
    def test_plan_migration_is_idempotent(self, db, basic_tier, org_with_tier):
        org, sub = org_with_tier(basic_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        plan_after_first = sub.plan

        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == plan_after_first

    def test_wallet_migration_is_idempotent(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=100)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        first_count = CreditBalance.objects.filter(organization=org).count()

        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        second_count = CreditBalance.objects.filter(organization=org).count()
        assert first_count == second_count == 1


class TestReversibility:
    def test_plan_reverse_resets_to_free(self, db, basic_tier, org_with_tier):
        org, sub = org_with_tier(basic_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.PAYG.value

        reverse_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.FREE.value

    def test_billing_interval_reverse_resets_to_monthly(
        self, db, basic_yearly_tier, org_with_tier
    ):
        org, sub = org_with_tier(basic_yearly_tier)
        populate_plan(APPS, SCHEMA_EDITOR)
        set_annual_interval(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.PAYG.value
        assert sub.billing_interval == "annual"

        reverse_plan(APPS, SCHEMA_EDITOR)
        sub.refresh_from_db()
        assert sub.plan == PlanChoices.FREE.value
        assert sub.billing_interval == "monthly"

    def test_credits_reverse_deletes_migrated_only(self, db, free_tier, org_with_tier):
        org, sub = org_with_tier(free_tier, wallet_balance=100)
        migrate_wallet_to_credits(APPS, SCHEMA_EDITOR)
        assert CreditBalance.objects.filter(organization=org).count() == 1

        manually_created = CreditBalance.objects.create(
            organization=org,
            credit_type="startup",
            original_amount=Decimal("5000"),
            remaining_amount=Decimal("5000"),
            description="Manual startup credit",
        )

        reverse_credits(APPS, SCHEMA_EDITOR)

        assert not CreditBalance.objects.filter(
            organization=org,
            description="Migrated from legacy wallet balance",
        ).exists()
        assert CreditBalance.objects.filter(pk=manually_created.pk).exists()
