from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class OnboardingSubscriptionFacts:
    plan_tier: str
    status: str
    paid: bool
    suppression_reason: str | None = None


def deployment_mode() -> str:
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return "oss"
    return str(DeploymentMode.get_mode() or "oss")


def deployment_region() -> str:
    region = getattr(settings, "CLOUD_DEPLOYMENT", "") or ""
    return str(region).lower()


def is_cloud_deployment() -> bool:
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return False
    return bool(DeploymentMode.is_cloud())


def onboarding_cloud_jobs_enabled() -> bool:
    if not getattr(settings, "ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED", False):
        return False
    return is_cloud_deployment()


def lifecycle_delivery_cloud_enabled() -> bool:
    return is_cloud_deployment()


def organization_subscription_facts(
    organization,
    *,
    paid_plan_values: frozenset[str] | set[str] | None = None,
) -> OnboardingSubscriptionFacts:
    if organization is None:
        return OnboardingSubscriptionFacts(
            plan_tier="",
            status="",
            paid=False,
            suppression_reason="organization_missing",
        )

    try:
        from ee.usage.models.usage import (
            OrganizationStatusChoices,
            OrganizationSubscription,
        )
    except ImportError:
        return OnboardingSubscriptionFacts(
            plan_tier="",
            status="",
            paid=False,
            suppression_reason="subscription_unavailable",
        )

    subscription = (
        OrganizationSubscription.no_workspace_objects.select_related(
            "subscription_tier"
        )
        .filter(organization=organization)
        .order_by("-updated_at")
        .first()
    )
    if subscription is None:
        return OnboardingSubscriptionFacts(
            plan_tier="",
            status="",
            paid=False,
            suppression_reason="subscription_missing",
        )

    status = str(getattr(subscription, "status", "") or "")
    plan = str(getattr(subscription, "plan", "") or "")
    if status != OrganizationStatusChoices.ACTIVE.value:
        return OnboardingSubscriptionFacts(
            plan_tier=plan,
            status=status,
            paid=False,
            suppression_reason="subscription_not_active",
        )

    if paid_plan_values is None:
        from accounts.services.onboarding.activation_export_registry import (
            activation_export_paid_plan_values,
        )

        paid_plan_values = activation_export_paid_plan_values()

    paid = plan in paid_plan_values
    return OnboardingSubscriptionFacts(
        plan_tier=plan,
        status=status,
        paid=paid,
        suppression_reason=None if paid else "subscription_not_paid",
    )
