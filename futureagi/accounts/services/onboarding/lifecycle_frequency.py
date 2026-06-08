from datetime import timedelta

from accounts.models import OnboardingLifecycleEvaluationLog
from accounts.services.onboarding.lifecycle_send_policy import (
    eligibility_frequency_caps,
)


def _cap_applies_to_campaign(cap, campaign):
    campaign_keys = cap.get("campaign_keys") or ()
    if campaign_keys and campaign.get("campaign_key") not in campaign_keys:
        return False
    campaign_groups = cap.get("campaign_groups") or ()
    if campaign_groups and campaign.get("campaign_group") not in campaign_groups:
        return False
    frequency_cap_keys = cap.get("frequency_cap_keys") or ()
    if (
        frequency_cap_keys
        and campaign.get("frequency_cap_key") not in frequency_cap_keys
    ):
        return False
    return True


def _eligible_logs_for_cap(*, user, workspace, campaign, now, cap):
    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
    )
    window_hours = cap.get("window_hours")
    if window_hours is not None:
        queryset = queryset.filter(
            evaluated_at__gte=now - timedelta(hours=window_hours)
        )

    scope = cap["scope"]
    if scope == "user":
        return queryset.filter(user=user)
    if scope == "workspace":
        return queryset.filter(workspace=workspace)
    if scope == "campaign_user":
        return queryset.filter(user=user, campaign_key=campaign["campaign_key"])
    if scope == "campaign_key":
        return queryset.filter(user=user, campaign_key=campaign["campaign_key"])
    return queryset.none()


def frequency_cap_suppression(*, user, workspace, campaign, now):
    if not user or not workspace or not campaign:
        return None

    for cap in eligibility_frequency_caps():
        if not _cap_applies_to_campaign(cap, campaign):
            continue
        eligible_logs = _eligible_logs_for_cap(
            user=user,
            workspace=workspace,
            campaign=campaign,
            now=now,
            cap=cap,
        )
        if eligible_logs.count() >= cap["limit"]:
            return cap["reason"]

    return None
