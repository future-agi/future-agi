from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class ActivationExportDecision:
    allowed: bool
    deployment_mode: str
    deployment_region: str
    plan_tier: str
    subscription_status: str
    suppression_reason: str | None = None


@dataclass(frozen=True)
class _SubscriptionFacts:
    plan_tier: str
    status: str
    paid: bool
    suppression_reason: str | None = None


PAID_PLAN_VALUES = {"payg", "boost", "scale", "enterprise", "custom"}


def _deployment_mode():
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return "oss"
    return DeploymentMode.get_mode()


def _deployment_region():
    region = getattr(settings, "CLOUD_DEPLOYMENT", "") or ""
    return str(region).lower()


def _subscription_facts(organization) -> _SubscriptionFacts:
    if organization is None:
        return _SubscriptionFacts(
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
        return _SubscriptionFacts(
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
        return _SubscriptionFacts(
            plan_tier="",
            status="",
            paid=False,
            suppression_reason="subscription_missing",
        )

    status = str(getattr(subscription, "status", "") or "")
    if status != OrganizationStatusChoices.ACTIVE.value:
        return _SubscriptionFacts(
            plan_tier=str(getattr(subscription, "plan", "") or ""),
            status=status,
            paid=False,
            suppression_reason="subscription_not_active",
        )

    plan = str(getattr(subscription, "plan", "") or "")
    paid = plan in PAID_PLAN_VALUES
    return _SubscriptionFacts(
        plan_tier=plan,
        status=status,
        paid=paid,
        suppression_reason=None if paid else "subscription_not_paid",
    )


def activation_export_decision(organization) -> ActivationExportDecision:
    mode = _deployment_mode()
    region = _deployment_region()
    subscription = _subscription_facts(organization)

    if mode != "cloud":
        return ActivationExportDecision(
            allowed=False,
            deployment_mode=mode,
            deployment_region=region,
            plan_tier=subscription.plan_tier,
            subscription_status=subscription.status,
            suppression_reason="deployment_not_cloud",
        )

    if not subscription.paid:
        return ActivationExportDecision(
            allowed=False,
            deployment_mode=mode,
            deployment_region=region,
            plan_tier=subscription.plan_tier,
            subscription_status=subscription.status,
            suppression_reason=subscription.suppression_reason,
        )

    return ActivationExportDecision(
        allowed=True,
        deployment_mode=mode,
        deployment_region=region,
        plan_tier=subscription.plan_tier,
        subscription_status=subscription.status,
    )
