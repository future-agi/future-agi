from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
    OnboardingQualityAction,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_sender import (
    queue_onboarding_lifecycle_email,
    send_onboarding_lifecycle_email,
)
from accounts.tests.onboarding_model_factories import (
    create_custom_eval,
    create_observe_project,
    create_trace,
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


def _activated_observe_workspace(organization, workspace, user, *, now):
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        selected_at=now - timedelta(hours=4),
    )
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    type(trace).no_workspace_objects.filter(id=trace.id).update(
        created_at=now - timedelta(hours=3)
    )
    create_custom_eval(organization=organization, workspace=workspace, project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="test",
        product_path="observe",
        occurred_at=now - timedelta(hours=2, minutes=30),
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        source="test",
        product_path="observe",
        occurred_at=now - timedelta(hours=2),
    )
    return project


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
@override_settings(
    ONBOARDING_FEATURE_FLAGS=_flags(
        onboarding_daily_quality_home=True,
        onboarding_email_daily_digest_enabled=True,
    )
)
def test_daily_quality_digest_send_log_carries_safe_preview(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    now = timezone.now()
    campaign = lifecycle_campaign_by_key("daily_quality_open_actions")
    _activated_observe_workspace(organization, workspace, user, now=now)
    OnboardingQualityAction.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        product_path="observe",
        action_key="trace-action-1",
        status=OnboardingQualityAction.STATUS_OPEN,
        label="Assign trace owner",
        route="/dashboard/home?mode=daily-quality",
        fallback_route="/dashboard/get-started",
        source_type="trace",
        source_id="trace-123",
        is_sample=False,
        last_event_at=now - timedelta(minutes=30),
        metadata={"api_token": "secret-value"},
    )
    log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000015",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="daily_review",
        primary_path="observe",
        recommendation_id="review_daily_quality",
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?mode=daily-quality",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
        activation_state_snapshot={
            "stage": "daily_review",
            "primary_path": "observe",
            "recommended_action_id": "review_daily_quality",
        },
        metadata={"source": "test", "send_enabled": False},
    )

    send_log = queue_onboarding_lifecycle_email(log, now=now)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_QUEUED
    preview = send_log.metadata["digest_preview"]
    assert preview["campaign_key"] == "daily_quality_open_actions"
    assert preview["actions"][0]["action_id"] == "trace-action-1"
    assert preview["actions"][0]["route"] == "/dashboard/home?mode=daily-quality"
    assert "api_token" not in str(preview)
    assert "secret-value" not in str(preview)

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        sent_log = send_onboarding_lifecycle_email(send_log, now=now)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    assert helper.call_args.args[1] == (
        "onboarding_lifecycle/daily_quality_open_actions_v1.html"
    )
    template_context = helper.call_args.args[2]
    assert template_context["digest_preview"]["actions"][0]["action_id"] == (
        "trace-action-1"
    )


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
