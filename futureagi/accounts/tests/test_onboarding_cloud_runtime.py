import sys
from decimal import Decimal

import pytest

from accounts.services.onboarding.cloud_runtime import (
    deployment_mode,
    deployment_region,
    is_cloud_deployment,
    onboarding_cloud_jobs_enabled,
    organization_subscription_facts,
)
from ee.usage.models.usage import (
    OrganizationStatusChoices,
    OrganizationSubscription,
    PlanChoices,
    SubscriptionTier,
    SubscriptionTierChoices,
)


def test_cloud_runtime_fails_closed_without_deployment_module(monkeypatch, settings):
    settings.CLOUD_DEPLOYMENT = "US"
    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = True
    monkeypatch.setitem(sys.modules, "ee.usage.deployment", None)

    assert deployment_mode() == "oss"
    assert deployment_region() == "us"
    assert is_cloud_deployment() is False
    assert onboarding_cloud_jobs_enabled() is False


def test_cloud_jobs_require_cloud_mode_and_setting(monkeypatch, settings):
    from ee.usage.deployment import DeploymentMode

    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = False
    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: True))

    assert onboarding_cloud_jobs_enabled() is False

    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = True
    assert onboarding_cloud_jobs_enabled() is True

    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: False))
    assert onboarding_cloud_jobs_enabled() is False


@pytest.mark.django_db
def test_subscription_facts_allow_only_active_paid_plans(organization):
    tier, _created = SubscriptionTier.no_workspace_objects.get_or_create(
        name=SubscriptionTierChoices.BUSINESS.value,
        description="Business tier",
    )
    OrganizationSubscription.no_workspace_objects.create(
        organization=organization,
        subscription_tier=tier,
        status=OrganizationStatusChoices.ACTIVE.value,
        plan=PlanChoices.FREE.value,
        wallet_balance=Decimal("0"),
    )

    free_facts = organization_subscription_facts(
        organization,
        paid_plan_values={PlanChoices.PAYG.value},
    )
    assert free_facts.paid is False
    assert free_facts.plan_tier == PlanChoices.FREE.value
    assert free_facts.status == OrganizationStatusChoices.ACTIVE.value
    assert free_facts.suppression_reason == "subscription_not_paid"

    OrganizationSubscription.no_workspace_objects.filter(
        organization=organization
    ).update(plan=PlanChoices.PAYG.value)

    paid_facts = organization_subscription_facts(
        organization,
        paid_plan_values={PlanChoices.PAYG.value},
    )
    assert paid_facts.paid is True
    assert paid_facts.plan_tier == PlanChoices.PAYG.value
    assert paid_facts.status == OrganizationStatusChoices.ACTIVE.value
    assert paid_facts.suppression_reason is None


@pytest.mark.django_db
def test_subscription_facts_suppress_inactive_subscription(organization):
    tier, _created = SubscriptionTier.no_workspace_objects.get_or_create(
        name=SubscriptionTierChoices.BUSINESS.value,
        description="Business tier",
    )
    OrganizationSubscription.no_workspace_objects.create(
        organization=organization,
        subscription_tier=tier,
        status=OrganizationStatusChoices.PAST_DUE.value,
        plan=PlanChoices.PAYG.value,
        wallet_balance=Decimal("0"),
    )

    facts = organization_subscription_facts(
        organization,
        paid_plan_values={PlanChoices.PAYG.value},
    )

    assert facts.paid is False
    assert facts.plan_tier == PlanChoices.PAYG.value
    assert facts.status == OrganizationStatusChoices.PAST_DUE.value
    assert facts.suppression_reason == "subscription_not_active"
