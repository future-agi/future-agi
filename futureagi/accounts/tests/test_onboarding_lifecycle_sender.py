from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_sender import (
    queue_onboarding_lifecycle_email,
    send_onboarding_lifecycle_email,
)


def _flags(**overrides):
    flags = {
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
        "onboarding_sample_project": False,
        "onboarding_daily_quality_home": False,
        "onboarding_lifecycle_email_dry_run": True,
        "onboarding_email_welcome_enabled": True,
        "onboarding_email_first_action_recovery_enabled": True,
        "onboarding_email_first_signal_enabled": True,
        "onboarding_email_next_loop_enabled": True,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
        "onboarding_lifecycle_send_enabled": True,
    }
    flags.update(overrides)
    return flags


def _set_workspace_created_at(workspace, value):
    Workspace.no_workspace_objects.filter(id=workspace.id).update(created_at=value)
    workspace.refresh_from_db()


def _allow_user(user):
    return OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        reason="test",
    )


def _eligible_log(user, organization, workspace, *, now=None):
    now = now or timezone.now()
    campaign = lifecycle_campaign_by_key("welcome_choose_goal")
    _set_workspace_created_at(workspace, now - timedelta(minutes=30))
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000014",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="choose_goal",
        primary_path="observe",
        recommendation_id="choose_onboarding_goal",
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
        activation_state_snapshot={
            "stage": "choose_goal",
            "primary_path": "observe",
            "recommended_action_id": "choose_onboarding_goal",
        },
        metadata={"source": "test", "send_enabled": False},
    )


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS=_flags(onboarding_lifecycle_send_enabled=False)
)
def test_send_flag_off_suppresses_before_helper(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        send_log = queue_onboarding_lifecycle_email(log)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "send_flag_disabled"
    helper.assert_not_called()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_user_not_allowlisted_suppresses(
    organization,
    workspace,
    user,
):
    log = _eligible_log(user, organization, workspace)

    send_log = queue_onboarding_lifecycle_email(log)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "not_in_send_cohort"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_helper_success_records_sent_log(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        send_log = queue_onboarding_lifecycle_email(log)
        sent_log = send_onboarding_lifecycle_email(send_log)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    assert sent_log.sent_at is not None
    assert sent_log.provider_status == "accepted"
    assert sent_log.click_url
    helper.assert_called_once()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_helper_failure_records_failed_send(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)

    with patch(
        "accounts.services.onboarding.lifecycle_sender.email_helper",
        side_effect=RuntimeError("provider unavailable"),
    ):
        send_log = queue_onboarding_lifecycle_email(log)
        failed_log = send_onboarding_lifecycle_email(send_log)

    assert failed_log.status == OnboardingLifecycleSendLog.STATUS_FAILED
    assert failed_log.provider_status == "failed"
    assert "provider unavailable" in failed_log.failure_reason


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_duplicate_send_does_not_call_helper_twice(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        first = send_onboarding_lifecycle_email(
            queue_onboarding_lifecycle_email(log),
        )
        second = send_onboarding_lifecycle_email(
            queue_onboarding_lifecycle_email(log),
        )

    assert first.id == second.id
    assert second.status == OnboardingLifecycleSendLog.STATUS_SENT
    helper.assert_called_once()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_unsubscribed_user_suppresses_send(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        onboarding_enabled=False,
    )

    send_log = queue_onboarding_lifecycle_email(log)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "unsubscribed"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_completed_target_suppresses_stale_send(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="test",
        product_path="observe",
    )

    send_log = queue_onboarding_lifecycle_email(log)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "target_success_event_completed"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_completion_event_marks_send_completed(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        send_log = send_onboarding_lifecycle_email(
            queue_onboarding_lifecycle_email(log),
        )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="test",
        product_path="observe",
        occurred_at=timezone.now() + timedelta(minutes=1),
    )

    send_log.refresh_from_db()
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_COMPLETED
    assert send_log.completed_at is not None
