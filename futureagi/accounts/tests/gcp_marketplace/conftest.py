from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.gcp_marketplace import (
    GCPMarketplaceAccount,
    GCPMarketplaceAccountState,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
)
from accounts.tests.gcp_marketplace.support import (
    ACCOUNT_ID,
    CREATE_TIME,
    ENTITLEMENT_ID,
    UPDATE_TIME,
    USAGE_REPORTING_ID,
    FakeProcurement,
    google_time,
    remote_entitlement,
)


@pytest.fixture
def procurement():
    """Replace the Procurement singleton everywhere the handlers reach it."""
    fake = FakeProcurement()
    with (
        patch("accounts.gcp_marketplace_events.gcp_procurement", fake),
        patch("accounts.gcp_marketplace_utils.gcp_procurement", fake),
    ):
        yield fake


@pytest.fixture
def free_tier(db):
    from ee.usage.models.usage import SubscriptionTier, SubscriptionTierChoices

    tier, _ = SubscriptionTier.objects.get_or_create(
        name=SubscriptionTierChoices.FREE.value,
        defaults={"wallet_refill_amount": Decimal("0")},
    )
    return tier


@pytest.fixture
def subscription(organization, free_tier):
    """The organization's subscription, on the free plan and card billing."""
    from ee.usage.models.usage import OrganizationSubscription

    return OrganizationSubscription.objects.create(
        organization=organization, subscription_tier=free_tier
    )


@pytest.fixture
def gcp_account(db, organization):
    """An account whose customer has completed sign-up."""
    return GCPMarketplaceAccount.objects.create(
        procurement_account_id=ACCOUNT_ID,
        organization=organization,
        state=GCPMarketplaceAccountState.ACTIVE,
        approved_at=timezone.now(),
    )


@pytest.fixture
def entitlement(gcp_account):
    """A live payg entitlement, as after ENTITLEMENT_ACTIVE."""
    return GCPMarketplaceEntitlement.objects.create(
        entitlement_id=ENTITLEMENT_ID,
        account=gcp_account,
        organization=gcp_account.organization,
        plan_id="payg",
        status=GCPMarketplaceEntitlementState.ACTIVE,
        usage_reporting_id=USAGE_REPORTING_ID,
        effective_at=google_time(CREATE_TIME),
        google_update_time=google_time(UPDATE_TIME),
        raw_payload=remote_entitlement(state="ENTITLEMENT_ACTIVE"),
    )
