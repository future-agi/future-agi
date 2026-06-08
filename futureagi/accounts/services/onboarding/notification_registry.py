from __future__ import annotations

from dataclasses import dataclass

from accounts.models import NotificationPreference


@dataclass(frozen=True)
class NotificationFamily:
    id: str
    label: str
    description: str
    default_channels: tuple[str, ...]
    non_critical: bool
    user_controllable: bool
    workspace_controllable: bool


NOTIFICATION_CHANNELS = (
    NotificationPreference.CHANNEL_EMAIL,
    NotificationPreference.CHANNEL_SLACK,
    NotificationPreference.CHANNEL_WEBHOOK,
)

NOTIFICATION_FAMILIES = {
    NotificationPreference.FAMILY_PRODUCT_ONBOARDING: NotificationFamily(
        id=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        label="Product onboarding",
        description="First action, path recovery, and activation nudges.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=True,
        user_controllable=True,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST: NotificationFamily(
        id=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        label="Daily quality digest",
        description="Return-loop summaries for activated workspaces.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=True,
        user_controllable=True,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_USAGE_BUDGET: NotificationFamily(
        id=NotificationPreference.FAMILY_USAGE_BUDGET,
        label="Usage and budget alerts",
        description="Budget thresholds, warnings, and blocking usage states.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=False,
        user_controllable=False,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_GATEWAY_ALERT: NotificationFamily(
        id=NotificationPreference.FAMILY_GATEWAY_ALERT,
        label="Gateway alerts",
        description="Gateway cost, latency, errors, and guardrail activity.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=False,
        user_controllable=False,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_OBSERVE_MONITOR: NotificationFamily(
        id=NotificationPreference.FAMILY_OBSERVE_MONITOR,
        label="Observe monitors",
        description="Trace, eval, latency, reliability, and spend monitor alerts.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=False,
        user_controllable=False,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_EVAL_QUALITY_ALERT: NotificationFamily(
        id=NotificationPreference.FAMILY_EVAL_QUALITY_ALERT,
        label="Eval quality alerts",
        description="Eval failures, regressions, and quality-review reminders.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=False,
        user_controllable=False,
        workspace_controllable=True,
    ),
    NotificationPreference.FAMILY_WORKSPACE_ADMIN: NotificationFamily(
        id=NotificationPreference.FAMILY_WORKSPACE_ADMIN,
        label="Workspace administration",
        description="Invites, access, security, and account-state messages.",
        default_channels=(NotificationPreference.CHANNEL_EMAIL,),
        non_critical=False,
        user_controllable=False,
        workspace_controllable=True,
    ),
}


def family_payloads():
    return [
        {
            "id": family.id,
            "label": family.label,
            "description": family.description,
            "default_channels": list(family.default_channels),
            "non_critical": family.non_critical,
            "user_controllable": family.user_controllable,
            "workspace_controllable": family.workspace_controllable,
        }
        for family in NOTIFICATION_FAMILIES.values()
    ]


def family_for_campaign_group(campaign_group):
    if campaign_group == "activation_success":
        return NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST
    return NotificationPreference.FAMILY_PRODUCT_ONBOARDING


def validate_family(family):
    if family not in NOTIFICATION_FAMILIES:
        raise ValueError(f"Unknown notification family: {family}")
    return family
