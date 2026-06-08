from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_tokens import sign_lifecycle_token


def _send_log(user, organization, workspace):
    campaign = lifecycle_campaign_by_key("welcome_choose_goal")
    evaluation = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000214",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="choose_goal",
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        registry_snapshot=campaign,
    )
    return OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        primary_path="observe",
        activation_stage="choose_goal",
        recommended_action_id="choose_onboarding_goal",
        target_success_event=campaign["target_success_event"],
        target_route="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleSendLog.STATUS_SENT,
        sent_at=timezone.now() - timedelta(minutes=1),
    )


@pytest.mark.django_db
def test_unsubscribe_token_updates_onboarding_preference_only(
    client,
    organization,
    workspace,
    user,
):
    send_log = _send_log(user, organization, workspace)
    token = sign_lifecycle_token(send_log=send_log, kind="unsubscribe")

    response = client.get(f"/accounts/onboarding/lifecycle/unsubscribe/?token={token}")

    preference = OnboardingLifecyclePreference.no_workspace_objects.get(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    send_log.refresh_from_db()
    assert response.status_code == 200
    assert preference.onboarding_enabled is False
    assert preference.unsubscribed_at is not None
    assert send_log.unsubscribed_at is not None


@pytest.mark.django_db
def test_snooze_token_sets_bounded_snooze(client, organization, workspace, user):
    send_log = _send_log(user, organization, workspace)
    token = sign_lifecycle_token(send_log=send_log, kind="snooze")

    response = client.get(
        f"/accounts/onboarding/lifecycle/snooze/?token={token}&days=90"
    )

    preference = OnboardingLifecyclePreference.no_workspace_objects.get(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    assert response.status_code == 200
    assert preference.snoozed_until is not None
    assert preference.snoozed_until <= timezone.now() + timedelta(days=31)


@pytest.mark.django_db
def test_invalid_preference_token_does_not_update(client):
    response = client.get("/accounts/onboarding/lifecycle/unsubscribe/?token=bad")

    assert response.status_code == 200
    assert not OnboardingLifecyclePreference.no_workspace_objects.exists()
