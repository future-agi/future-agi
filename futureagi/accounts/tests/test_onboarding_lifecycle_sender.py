from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingActivationEvent,
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
from accounts.services.onboarding.notification_preferences import (
    notification_preference_decision,
    upsert_notification_channel,
    upsert_notification_preference,
)
from accounts.tests.onboarding_model_factories import (
    create_custom_eval,
    create_observe_project,
    create_trace,
)


@pytest.fixture(autouse=True)
def _cloud_lifecycle_delivery_enabled():
    with patch(
        "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
        return_value=True,
    ):
        yield


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
    OnboardingActivationEvent.no_workspace_objects.filter(
        workspace=workspace,
        event_name="observe_project_created",
        metadata__project_id=str(project.id),
    ).update(occurred_at=now - timedelta(hours=4))
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
        allow_observe_loop_completion=True,
    )
    return project


def _eligible_log(user, organization, workspace, *, now=None):
    now = now or timezone.now()
    campaign = lifecycle_campaign_by_key("welcome_resume_goal")
    _set_workspace_created_at(workspace, now - timedelta(minutes=30))
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        selected_at=now - timedelta(minutes=20),
    )
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000014",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="connect_observability",
        primary_path="observe",
        recommendation_id="create_observe_project",
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/observe?setup=true&source=onboarding",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
        activation_state_snapshot={
            "stage": "connect_observability",
            "primary_path": "observe",
            "recommended_action_id": "create_observe_project",
        },
        metadata={"source": "test", "send_enabled": False},
    )


def _queued_daily_quality_send_log(user, organization, workspace, *, now):
    campaign = lifecycle_campaign_by_key("daily_quality_open_actions")
    preview = {
        "kind": "daily_quality_open_actions",
        "campaign_key": "daily_quality_open_actions",
        "template_key": "daily_quality_open_actions_v1",
        "generated_at": now.isoformat(),
        "workspace_id": str(workspace.id),
        "action_count": 1,
        "omitted_count": 0,
        "actions": [
            {
                "action_id": "trace-action-1",
                "label": "Review trace regression",
                "route": "/dashboard/home?mode=daily-quality",
                "fallback_route": "/dashboard/home",
                "source_type": "trace",
                "source_id": "trace-123",
                "primary_path": "observe",
                "status": "open",
                "age_minutes": 30,
                "last_event_at": (now - timedelta(minutes=30)).isoformat(),
                "is_overdue": False,
                "body": "Sensitive debugging notes must not leak.",
                "metadata": {"api_token": "secret-value"},
            }
        ],
    }
    evaluation_log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000017",
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
        metadata={"digest_preview": preview},
    )
    return OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation_log,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        primary_path="observe",
        activation_stage="daily_review",
        recommended_action_id="review_daily_quality",
        target_success_event=campaign["target_success_event"],
        target_route="/dashboard/home?mode=daily-quality",
        status=OnboardingLifecycleSendLog.STATUS_QUEUED,
        queued_at=now,
        metadata={"digest_preview": preview},
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
def test_non_cloud_delivery_suppresses_queued_send(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    send_log = queue_onboarding_lifecycle_email(log)
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_QUEUED

    with (
        patch(
            "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
            return_value=False,
        ),
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        sent_log = send_onboarding_lifecycle_email(send_log)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert sent_log.suppression_reason == "cloud_deployment_required"
    helper.assert_not_called()
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="cloud_deployment_required",
    ).exists()


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
    due_at = now + timedelta(hours=6)
    campaign = lifecycle_campaign_by_key("daily_quality_open_actions")
    _activated_observe_workspace(organization, workspace, user, now=now)
    OnboardingQualityAction.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        assigned_to=user,
        product_path="observe",
        action_key="trace-action-1",
        status=OnboardingQualityAction.STATUS_OPEN,
        label="Assign trace owner",
        route="/dashboard/home?mode=daily-quality",
        fallback_route="/dashboard/get-started",
        source_type="trace",
        source_id="trace-123",
        is_sample=False,
        due_at=due_at,
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
    assert preview["actions"][0]["assigned_to_user_id"] == str(user.id)
    assert preview["actions"][0]["due_at"] == due_at.isoformat()
    assert preview["actions"][0]["is_overdue"] is False
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
def test_daily_quality_digest_delivers_slack_when_channel_enabled(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    send_log = _queued_daily_quality_send_log(user, organization, workspace, now=now)
    upsert_notification_channel(
        organization=organization,
        workspace=workspace,
        actor=user,
        type=NotificationChannel.TYPE_SLACK_WEBHOOK,
        display_name="Daily quality",
        config={"webhook_url": "https://hooks.slack.com/services/T000/B000/secret"},
        is_active=True,
    )
    upsert_notification_preference(
        organization=organization,
        workspace=workspace,
        user=None,
        actor=user,
        scope="workspace",
        family=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        channel=NotificationPreference.CHANNEL_SLACK,
        enabled=True,
    )

    with (
        patch("accounts.services.onboarding.lifecycle_sender.email_helper"),
        patch(
            "accounts.services.onboarding.notification_delivery.requests.post"
        ) as post,
    ):
        post.return_value.raise_for_status.return_value = None
        sent_log = send_onboarding_lifecycle_email(send_log, now=now)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    post.assert_called_once()
    payload_text = str(post.call_args.kwargs["json"])
    assert "Review trace regression" in payload_text
    assert "Sensitive debugging notes" not in payload_text
    assert "secret-value" not in payload_text
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_SLACK,
        status=NotificationDeliveryLog.STATUS_SENT,
    ).exists()


@pytest.mark.django_db
def test_daily_quality_digest_delivers_webhook_when_channel_enabled(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    send_log = _queued_daily_quality_send_log(user, organization, workspace, now=now)
    upsert_notification_channel(
        organization=organization,
        workspace=workspace,
        actor=user,
        type=NotificationChannel.TYPE_WEBHOOK,
        display_name="Daily quality webhook",
        config={"url": "https://example.com/digest", "secret": "delivery-token"},
        is_active=True,
    )
    upsert_notification_preference(
        organization=organization,
        workspace=workspace,
        user=None,
        actor=user,
        scope="workspace",
        family=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        channel=NotificationPreference.CHANNEL_WEBHOOK,
        enabled=True,
    )

    with (
        patch("accounts.services.onboarding.lifecycle_sender.email_helper"),
        patch(
            "accounts.services.onboarding.notification_delivery.requests.post"
        ) as post,
    ):
        post.return_value.raise_for_status.return_value = None
        sent_log = send_onboarding_lifecycle_email(send_log, now=now)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    post.assert_called_once()
    assert post.call_args.args[0] == "https://example.com/digest"
    assert post.call_args.kwargs["headers"] == {
        "content-type": "application/json",
        "x-futureagi-notification-token": "delivery-token",
    }
    payload_text = str(post.call_args.kwargs["json"])
    assert "Review trace regression" in payload_text
    assert "Sensitive debugging notes" not in payload_text
    assert "secret-value" not in payload_text
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_WEBHOOK,
        status=NotificationDeliveryLog.STATUS_SENT,
    ).exists()


@pytest.mark.django_db
def test_daily_quality_digest_suppresses_slack_without_channel_preference(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    send_log = _queued_daily_quality_send_log(user, organization, workspace, now=now)
    upsert_notification_channel(
        organization=organization,
        workspace=workspace,
        actor=user,
        type=NotificationChannel.TYPE_SLACK_WEBHOOK,
        display_name="Daily quality",
        config={"webhook_url": "https://hooks.slack.com/services/T000/B000/secret"},
        is_active=True,
    )

    with (
        patch("accounts.services.onboarding.lifecycle_sender.email_helper"),
        patch(
            "accounts.services.onboarding.notification_delivery.requests.post"
        ) as post,
    ):
        sent_log = send_onboarding_lifecycle_email(send_log, now=now)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    post.assert_not_called()
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_SLACK,
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="channel_not_enabled",
    ).exists()


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS=_flags(
        onboarding_daily_quality_home=True,
        onboarding_email_daily_digest_enabled=True,
    )
)
def test_daily_quality_digest_send_requires_safe_preview(
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
    )
    log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000016",
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

    with (
        patch(
            "accounts.services.onboarding.lifecycle_eligibility.build_lifecycle_digest_preview",
            return_value=None,
        ),
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        send_log = queue_onboarding_lifecycle_email(log, now=now)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "missing_digest_preview"
    helper.assert_not_called()
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="missing_digest_preview",
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_notification_frequency_cap_suppresses_lifecycle_send(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    now = timezone.now()
    for family in (
        NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
    ):
        NotificationPreference.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            user=user,
            family=family,
            channel=NotificationPreference.CHANNEL_EMAIL,
            enabled=True,
            frequency_cap_minutes=120,
        )
        NotificationDeliveryLog.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            user=user,
            family=family,
            source_type="onboarding_lifecycle",
            source_id=f"prior-send-{family}",
            channel=NotificationPreference.CHANNEL_EMAIL,
            recipient_type="user",
            recipient_identifier_masked="u***@example.com",
            notification_key="welcome_choose_goal",
            status=NotificationDeliveryLog.STATUS_SENT,
            sent_at=now - timedelta(minutes=30),
        )
    log = _eligible_log(user, organization, workspace, now=now)

    assert (
        notification_preference_decision(
            organization=organization,
            workspace=workspace,
            user=user,
            family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
            channel=NotificationPreference.CHANNEL_EMAIL,
            now=now,
        ).reason
        == "frequency_capped"
    )

    with (
        patch(
            "accounts.services.onboarding.lifecycle_sender.notification_preference_decision",
            wraps=notification_preference_decision,
        ) as preference_decision,
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        send_log = queue_onboarding_lifecycle_email(log, now=now)

    assert preference_decision.call_args_list
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "frequency_capped"
    helper.assert_not_called()


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
        event_name=log.target_success_event,
        source="test",
        product_path="observe",
    )

    send_log = queue_onboarding_lifecycle_email(log)

    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert send_log.suppression_reason == "target_success_event_completed"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_completed_target_after_queue_suppresses_before_provider(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    send_log = queue_onboarding_lifecycle_email(log)
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_QUEUED
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=log.target_success_event,
        source="test",
        product_path="observe",
    )

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        suppressed_log = send_onboarding_lifecycle_email(send_log)

    assert suppressed_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert suppressed_log.suppression_reason == "target_success_event_completed"
    helper.assert_not_called()
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(send_log.id),
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="target_success_event_completed",
    ).exists()


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
        event_name=log.target_success_event,
        source="test",
        product_path="observe",
        occurred_at=timezone.now() + timedelta(minutes=1),
    )

    send_log.refresh_from_db()
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_COMPLETED
    assert send_log.completed_at is not None
