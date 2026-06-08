from __future__ import annotations

from accounts.models import OnboardingActivationEvent


def lifecycle_completion_is_sample(campaign):
    if not campaign:
        return False
    if campaign.get("campaign_key") == "gateway_sample_bridge":
        return False
    return campaign.get("sample_policy") == "sample_only"


def lifecycle_target_completed(
    *,
    organization,
    workspace,
    campaign,
    target_success_event=None,
    after=None,
):
    if not campaign:
        return False
    if campaign.get("frequency_cap_key") == "daily_digest":
        return False

    event_name = target_success_event or campaign.get("target_success_event")
    if not event_name:
        return False

    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name=event_name,
        is_sample=lifecycle_completion_is_sample(campaign),
    )
    if after:
        queryset = queryset.filter(occurred_at__gte=after)
    return queryset.exists()
