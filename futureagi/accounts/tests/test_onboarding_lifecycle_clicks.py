from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_tokens import sign_lifecycle_token


def _sent_log(
    user,
    organization,
    workspace,
    *,
    campaign_key="welcome_choose_goal",
    status=None,
    sent_at=None,
):
    campaign = lifecycle_campaign_by_key(campaign_key)
    sent_at = sent_at or timezone.now() - timedelta(minutes=1)
    evaluation = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000114",
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
        status=status or OnboardingLifecycleSendLog.STATUS_SENT,
        sent_at=sent_at,
    )


@pytest.mark.django_db
def test_valid_click_marks_clicked_and_redirects(client, organization, workspace, user):
    send_log = _sent_log(user, organization, workspace)
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?onboarding=choose-goal")
    assert "source=onboarding_email" in response["Location"]
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_CLICKED
    assert send_log.clicked_at is not None


@pytest.mark.django_db
def test_stale_click_redirects_to_current_home(client, organization, workspace, user):
    send_log = _sent_log(user, organization, workspace)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="test",
        product_path="observe",
    )
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?")
    assert "status=stale" in response["Location"]
    assert "stale_reason=target_complete" in response["Location"]
    assert send_log.metadata["stale_reason"] == "target_complete"


@pytest.mark.django_db
def test_sample_completion_does_not_stale_lifecycle_click(
    client,
    organization,
    workspace,
    user,
):
    send_log = _sent_log(user, organization, workspace)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="test",
        product_path="observe",
        is_sample=True,
    )
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?onboarding=choose-goal")
    assert send_log.metadata["click_status"] == "current"
    assert "stale_reason" not in send_log.metadata


@pytest.mark.django_db
def test_sample_campaign_completion_stales_lifecycle_click(
    client,
    organization,
    workspace,
    user,
):
    send_log = _sent_log(
        user,
        organization,
        workspace,
        campaign_key="observe_sample_bridge",
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_sample_project_opened",
        source="test",
        product_path="sample",
        is_sample=True,
    )
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?")
    assert send_log.metadata["stale_reason"] == "target_complete"


@pytest.mark.django_db
def test_prior_completion_does_not_stale_fresh_lifecycle_click(
    client,
    organization,
    workspace,
    user,
):
    sent_at = timezone.now()
    send_log = _sent_log(user, organization, workspace, sent_at=sent_at)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="test",
        product_path="observe",
        occurred_at=sent_at - timedelta(minutes=1),
    )
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?onboarding=choose-goal")
    assert send_log.metadata["click_status"] == "current"
    assert "stale_reason" not in send_log.metadata


@pytest.mark.django_db
def test_unsafe_click_target_redirects_to_current_home(
    client,
    organization,
    workspace,
    user,
):
    send_log = _sent_log(user, organization, workspace)
    send_log.target_route = "https://example.invalid/unsafe"
    send_log.save(update_fields=["target_route", "updated_at"])
    token = sign_lifecycle_token(send_log=send_log, kind="click")

    response = client.get(f"/accounts/onboarding/lifecycle/click/?token={token}")

    send_log.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home?")
    assert "status=stale" in response["Location"]
    assert "stale_reason=route_unavailable" in response["Location"]
    assert send_log.metadata["stale_reason"] == "route_unavailable"


@pytest.mark.django_db
def test_invalid_click_token_redirects_safely(client):
    response = client.get("/accounts/onboarding/lifecycle/click/?token=bad")

    assert response.status_code == 302
    assert response["Location"].startswith("/dashboard/home")
