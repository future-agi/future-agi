from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from accounts.models import OnboardingLifecyclePreference


def lifecycle_preference_for(*, user, organization, workspace=None):
    if not user or not organization:
        return None
    return (
        OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=user,
            organization=organization,
            workspace=workspace,
        ).first()
        or OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=user,
            organization=organization,
            workspace__isnull=True,
        ).first()
    )


def ensure_lifecycle_preference(*, user, organization, workspace=None):
    preference = lifecycle_preference_for(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    if preference:
        return preference
    return OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
    )


def unsubscribe_onboarding_lifecycle(*, send_log, now=None):
    now = now or timezone.now()
    preference = ensure_lifecycle_preference(
        user=send_log.user,
        organization=send_log.organization,
        workspace=send_log.workspace,
    )
    preference.onboarding_enabled = False
    preference.unsubscribed_at = now
    preference.save(
        update_fields=["onboarding_enabled", "unsubscribed_at", "updated_at"]
    )
    send_log.unsubscribed_at = now
    send_log.save(update_fields=["unsubscribed_at", "updated_at"])
    return preference


def snooze_onboarding_lifecycle(*, send_log, days=7, now=None):
    now = now or timezone.now()
    days = max(1, min(int(days), 30))
    preference = ensure_lifecycle_preference(
        user=send_log.user,
        organization=send_log.organization,
        workspace=send_log.workspace,
    )
    preference.snoozed_until = now + timedelta(days=days)
    preference.save(update_fields=["snoozed_until", "updated_at"])
    return preference
