import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
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
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_METADATA_KEY,
    PREVIEW_APPROVAL_MISSING_REASON,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_send_reports import (
    DRY_RUN_REPORT_METADATA_KEY,
    DRY_RUN_REPORT_MISSING_REASON,
)
from accounts.services.onboarding.lifecycle_sender import (
    queue_onboarding_lifecycle_email,
    send_limited_onboarding_lifecycle_batch,
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
        "onboarding_email_eval": True,
        "onboarding_email_voice": True,
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


def _preview_approval_metadata(campaign_key):
    return {
        APPROVAL_METADATA_KEY: {
            "manifest_path": "/tmp/manifest.json",
            "manifest_sha256": "a" * 64,
            "manifest_generated_at": "2026-05-29T10:00:00+00:00",
            "approval_record_path": "/tmp/approval-record.json",
            "approval_record_sha256": "b" * 64,
            "approved_by": "Lifecycle reviewer <reviewer@example.com>",
            "approved_at": "2026-05-29T10:05:00+00:00",
            "campaign_key": campaign_key,
            "html_file": f"{campaign_key}.html",
            "text_file": f"{campaign_key}.txt",
            "html_sha256": "c" * 64,
            "text_sha256": "d" * 64,
        }
    }


def _approval_metadata(campaign_key):
    return {
        **_preview_approval_metadata(campaign_key),
        DRY_RUN_REPORT_METADATA_KEY: {
            "path": "/tmp/dry-run-report.json",
            "sha256": "e" * 64,
            "generated_at": "2026-05-29T10:10:00+00:00",
            "command": "run_onboarding_lifecycle_send",
            "cohort": "internal",
            "limit": 1,
            "campaign_group": None,
            "user_id": None,
            "workspace_id": None,
            "require_campaign_group_allowlist": False,
            "approval_manifest_sha256": "a" * 64,
            "approval_record_sha256": "b" * 64,
            "evaluated": 1,
            "candidate_count": 1,
            "status_counts": {"would_send": 1},
            "suppression_counts": {},
            "review_record_path": "/tmp/dry-run-report-review.json",
            "review_record_sha256": "f" * 64,
            "reviewed_by": "Lifecycle reviewer <reviewer@example.com>",
            "reviewed_at": "2026-05-29T10:15:00+00:00",
        },
    }


def _mark_preview_approved(send_log):
    send_log.metadata = {
        **(send_log.metadata or {}),
        **_approval_metadata(send_log.campaign_key),
    }
    send_log.save(update_fields=["metadata", "updated_at"])
    return send_log


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


def _eligible_log(
    user,
    organization,
    workspace,
    *,
    now=None,
    campaign_key="welcome_resume_goal",
    activation_stage=None,
    primary_path=None,
    target_url=None,
):
    now = now or timezone.now()
    campaign = lifecycle_campaign_by_key(campaign_key)
    primary_path = primary_path or (
        "observe" if campaign["primary_path"] == "any" else campaign["primary_path"]
    )
    activation_stage = activation_stage or campaign["entry_stages"][0]
    target_url = target_url or (
        "/dashboard/observe?setup=true&source=onboarding"
        if campaign_key == "welcome_resume_goal"
        else "/dashboard/home?source=onboarding"
    )
    _set_workspace_created_at(workspace, now - timedelta(minutes=30))
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path=primary_path,
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
        activation_stage=activation_stage,
        primary_path=primary_path,
        recommendation_id=campaign["target_action_id"],
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url=target_url,
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
        activation_state_snapshot={
            "stage": activation_stage,
            "primary_path": primary_path,
            "recommended_action_id": campaign["target_action_id"],
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
        metadata={
            "digest_preview": preview,
            **_approval_metadata(campaign["campaign_key"]),
        },
    )


def _sent_send_log_for_campaign(
    user,
    organization,
    workspace,
    *,
    campaign_key="welcome_resume_goal",
    now=None,
    sent_at=None,
    clicked_at=None,
    status=OnboardingLifecycleSendLog.STATUS_SENT,
):
    now = now or timezone.now()
    sent_at = sent_at or now - timedelta(minutes=5)
    campaign = lifecycle_campaign_by_key(campaign_key)
    activation_stage = campaign["entry_stages"][0]
    evaluation_log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage=activation_stage,
        primary_path=campaign["primary_path"],
        recommendation_id=campaign["target_action_id"],
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?mode=daily-quality",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=sent_at - timedelta(minutes=5),
        evaluated_at=sent_at - timedelta(minutes=1),
        registry_snapshot=campaign,
        activation_state_snapshot={
            "stage": activation_stage,
            "primary_path": campaign["primary_path"],
            "recommended_action_id": campaign["target_action_id"],
        },
        metadata={"source": "test", "send_enabled": True},
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
        primary_path=campaign["primary_path"],
        activation_stage=activation_stage,
        recommended_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_route="/dashboard/home?mode=daily-quality",
        status=status,
        sent_at=sent_at,
        clicked_at=clicked_at,
        metadata={"source": "test"},
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
def test_batch_real_send_requires_preview_approval_record(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    _eligible_log(user, organization, workspace)

    with pytest.raises(
        ImproperlyConfigured,
        match="approval record is required",
    ):
        send_limited_onboarding_lifecycle_batch(
            cohort="internal",
            limit=1,
        )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_batch_real_send_requires_dry_run_report_review(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    _eligible_log(user, organization, workspace)
    preview_approval = type(
        "PreviewApproval",
        (),
        {
            "approval_record_sha256": "b" * 64,
        },
    )()

    with pytest.raises(
        ImproperlyConfigured,
        match="dry-run report review is required",
    ):
        send_limited_onboarding_lifecycle_batch(
            cohort="internal",
            limit=1,
            preview_approval=preview_approval,
        )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_provider_send_requires_preview_approval_metadata(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    send_log = queue_onboarding_lifecycle_email(log)

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        suppressed_log = send_onboarding_lifecycle_email(send_log)

    assert suppressed_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert suppressed_log.suppression_reason == PREVIEW_APPROVAL_MISSING_REASON
    assert not suppressed_log.click_url
    helper.assert_not_called()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_provider_send_requires_dry_run_report_metadata(
    organization,
    workspace,
    user,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace)
    send_log = queue_onboarding_lifecycle_email(log)
    send_log.metadata = {
        **(send_log.metadata or {}),
        **_preview_approval_metadata(send_log.campaign_key),
    }
    send_log.save(update_fields=["metadata", "updated_at"])

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper:
        suppressed_log = send_onboarding_lifecycle_email(send_log)

    assert suppressed_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    assert suppressed_log.suppression_reason == DRY_RUN_REPORT_MISSING_REASON
    assert not suppressed_log.click_url
    helper.assert_not_called()


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
        _mark_preview_approved(send_log)
        sent_log = send_onboarding_lifecycle_email(send_log)

    assert sent_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    assert sent_log.sent_at is not None
    assert sent_log.provider_status == "accepted"
    assert sent_log.click_url
    helper.assert_called_once()
    assert helper.call_args.args[0] == log.registry_snapshot["email_subject"]
    assert (
        helper.call_args.args[2]["preheader_text"]
        == (log.registry_snapshot["email_preheader"])
    )


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
    _mark_preview_approved(send_log)
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
    _mark_preview_approved(send_log)

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
    assert helper.call_args.args[0] == campaign["email_subject"]
    assert template_context["preheader_text"] == campaign["email_preheader"]
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
        _mark_preview_approved(send_log)
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
            _mark_preview_approved(queue_onboarding_lifecycle_email(log)),
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
@pytest.mark.parametrize("campaign_key", ["eval_create_source", "voice_create_agent"])
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_product_loop_recovery_preference_suppresses_eval_and_voice_sends(
    organization,
    workspace,
    user,
    campaign_key,
):
    _allow_user(user)
    log = _eligible_log(user, organization, workspace, campaign_key=campaign_key)
    OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        first_action_recovery_enabled=False,
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
            _mark_preview_approved(queue_onboarding_lifecycle_email(log)),
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


@pytest.mark.django_db
def test_completion_event_prefers_exact_send_log_id(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    exact_send = _sent_send_log_for_campaign(
        user,
        organization,
        workspace,
        now=now,
        sent_at=now - timedelta(minutes=20),
    )
    newer_clicked_send = _sent_send_log_for_campaign(
        user,
        organization,
        workspace,
        now=now,
        sent_at=now - timedelta(minutes=10),
        clicked_at=now - timedelta(minutes=1),
        status=OnboardingLifecycleSendLog.STATUS_CLICKED,
    )

    event = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=exact_send.target_success_event,
        source="daily_quality_home",
        product_path="observe",
        occurred_at=now,
        metadata={
            "send_log_id": str(exact_send.id),
            "campaign_key": exact_send.campaign_key,
            "email_key": exact_send.template_key,
            "target_stage": exact_send.activation_stage,
            "target_event": exact_send.target_success_event,
        },
    )

    exact_send.refresh_from_db()
    newer_clicked_send.refresh_from_db()
    assert exact_send.status == OnboardingLifecycleSendLog.STATUS_COMPLETED
    assert exact_send.completed_at == event.occurred_at
    assert exact_send.metadata["completed_event_id"] == str(event.id)
    assert exact_send.metadata["completion_source"] == "exact_send_log_id"
    assert newer_clicked_send.status == OnboardingLifecycleSendLog.STATUS_CLICKED


@pytest.mark.django_db
def test_mismatched_exact_send_log_id_does_not_fallback(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    mismatched_send = _sent_send_log_for_campaign(
        user,
        organization,
        workspace,
        now=now,
        sent_at=now - timedelta(minutes=20),
    )
    fallback_candidate = _sent_send_log_for_campaign(
        user,
        organization,
        workspace,
        now=now,
        sent_at=now - timedelta(minutes=10),
        clicked_at=now - timedelta(minutes=1),
        status=OnboardingLifecycleSendLog.STATUS_CLICKED,
    )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=mismatched_send.target_success_event,
        source="daily_quality_home",
        product_path="observe",
        occurred_at=now,
        metadata={
            "send_log_id": str(mismatched_send.id),
            "campaign_key": "different_campaign",
        },
    )

    mismatched_send.refresh_from_db()
    fallback_candidate.refresh_from_db()
    assert mismatched_send.status == OnboardingLifecycleSendLog.STATUS_SENT
    assert fallback_candidate.status == OnboardingLifecycleSendLog.STATUS_CLICKED


@pytest.mark.django_db
def test_sample_campaign_completion_event_can_complete_sample_send(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    send_log = _sent_send_log_for_campaign(
        user,
        organization,
        workspace,
        campaign_key="observe_sample_bridge",
        now=now,
        sent_at=now - timedelta(minutes=10),
    )

    event = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=send_log.target_success_event,
        source="sample_project",
        product_path="sample",
        is_sample=True,
        occurred_at=now,
        metadata={
            "send_log_id": str(send_log.id),
            "campaign_key": send_log.campaign_key,
            "email_key": send_log.template_key,
            "target_stage": send_log.activation_stage,
            "target_event": send_log.target_success_event,
        },
    )

    send_log.refresh_from_db()
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_COMPLETED
    assert send_log.completed_at == event.occurred_at
