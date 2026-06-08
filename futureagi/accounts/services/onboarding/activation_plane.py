from __future__ import annotations

from dataclasses import dataclass

from accounts.services.onboarding.activation_export_registry import (
    activation_export_paid_plan_values,
)
from accounts.services.onboarding.cloud_runtime import (
    deployment_mode,
    deployment_region,
    organization_subscription_facts,
)


@dataclass(frozen=True)
class ActivationExportDecision:
    allowed: bool
    deployment_mode: str
    deployment_region: str
    plan_tier: str
    subscription_status: str
    suppression_reason: str | None = None


def _deployment_mode():
    return deployment_mode()


def _deployment_region():
    return deployment_region()


def _subscription_facts(organization):
    return organization_subscription_facts(
        organization,
        paid_plan_values=activation_export_paid_plan_values(),
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
