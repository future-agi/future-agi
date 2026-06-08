from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key


def _safe_preview(workspace, *, now):
    return {
        "kind": "daily_quality_open_actions",
        "campaign_key": "daily_quality_open_actions",
        "template_key": "daily_quality_open_actions_v1",
        "generated_at": now.isoformat(),
        "action_count": 2,
        "omitted_count": 1,
        "api_token": "secret-value",
        "actions": [
            {
                "action_id": "trace-action-1",
                "label": "Review trace regression",
                "route": "/dashboard/home?mode=daily-quality",
                "fallback_route": "/dashboard/get-started",
                "source_type": "trace",
                "source_id": "trace-123",
                "primary_path": "observe",
                "status": "open",
                "age_minutes": 180,
                "last_event_at": (now - timedelta(hours=3)).isoformat(),
                "assigned_to_user_id": "user-1",
                "due_at": (now - timedelta(hours=1)).isoformat(),
                "is_overdue": True,
                "body": "Sensitive debugging notes must not leak.",
                "metadata": {"api_token": "secret-value"},
            },
            {
                "action_id": "external-route-action",
                "label": "External route fallback",
                "route": "https://example.com/not-safe",
                "fallback_route": "/dashboard/home",
                "source_type": "trace",
                "source_id": "trace-456",
                "primary_path": "observe",
                "status": "open",
                "age_minutes": 30,
                "last_event_at": (now - timedelta(minutes=30)).isoformat(),
                "is_overdue": False,
            },
        ],
    }


def _evaluation_log(organization, workspace, user, *, now, preview=None):
    campaign = lifecycle_campaign_by_key("daily_quality_open_actions")
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000201",
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
        eligible_at=now - timedelta(minutes=20),
        evaluated_at=now - timedelta(minutes=10),
        activation_state_snapshot={
            "stage": "daily_review",
            "primary_path": "observe",
        },
        registry_snapshot=campaign,
        metadata={
            "digest_preview": _safe_preview(workspace, now=now)
            if preview is None
            else preview
        },
    )


@pytest.mark.django_db
def test_digest_preview_review_lists_safe_snapshots_and_delivery_outcomes(
    auth_client,
    organization,
    workspace,
    user,
):
    now = timezone.now()
    evaluation_log = _evaluation_log(organization, workspace, user, now=now)
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation_log,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=evaluation_log.campaign_key,
        campaign_group=evaluation_log.campaign_group,
        template_key=evaluation_log.template_key,
        template_version=evaluation_log.template_version,
        primary_path="observe",
        activation_stage="daily_review",
        recommended_action_id="review_daily_quality",
        target_success_event="daily_quality_action_completed",
        target_route="/dashboard/home?mode=daily-quality",
        status=OnboardingLifecycleSendLog.STATUS_SENT,
        queued_at=now - timedelta(minutes=8),
        sent_at=now - timedelta(minutes=5),
        metadata={"digest_preview": _safe_preview(workspace, now=now)},
    )
    NotificationDeliveryLog.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        source_type="onboarding_lifecycle",
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_EMAIL,
        recipient_type="user",
        recipient_identifier_masked="us***@example.com",
        notification_key="daily_quality_open_actions",
        status=NotificationDeliveryLog.STATUS_SENT,
        sent_at=now - timedelta(minutes=5),
    )

    response = auth_client.get(
        "/accounts/onboarding/lifecycle/digest-previews/?limit=10",
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["count"] == 2
    send_item = next(
        item for item in result["items"] if item["source_type"] == "send_log"
    )
    assert send_item["delivery_logs"][0]["status"] == "sent"
    assert send_item["summary"]["overdue_count"] == 1
    assert send_item["summary"]["assigned_count"] == 1
    assert send_item["summary"]["omitted_action_count"] == 1
    assert send_item["preview"]["workspace_id"] == str(workspace.id)
    assert send_item["preview"]["actions"][1]["route"] == "/dashboard/home"
    payload_text = str(result)
    assert "secret-value" not in payload_text
    assert "Sensitive debugging notes" not in payload_text


@pytest.mark.django_db
def test_digest_preview_review_filters_campaign_and_skips_missing_preview(
    auth_client,
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _evaluation_log(organization, workspace, user, now=now)
    campaign = lifecycle_campaign_by_key("welcome_choose_goal")
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000202",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="choose_goal",
        primary_path="observe",
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=20),
        evaluated_at=now - timedelta(minutes=10),
        activation_state_snapshot={"stage": "choose_goal"},
        registry_snapshot=campaign,
        metadata={"digest_preview": _safe_preview(workspace, now=now)},
    )

    response = auth_client.get(
        "/accounts/onboarding/lifecycle/digest-previews/"
        "?campaign_key=daily_quality_open_actions&limit=5",
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["count"] == 1
    assert result["items"][0]["campaign_key"] == "daily_quality_open_actions"


@pytest.mark.django_db
def test_digest_preview_promotion_creates_reviewed_user_allowlist(
    auth_client,
    organization,
    workspace,
    user,
):
    now = timezone.now()
    evaluation_log = _evaluation_log(organization, workspace, user, now=now)

    response = auth_client.post(
        "/accounts/onboarding/lifecycle/digest-previews/promote/",
        {
            "sources": [
                {
                    "source_type": "evaluation_log",
                    "source_id": str(evaluation_log.id),
                }
            ],
            "scope_type": "user",
            "reason": "Reviewed internal digest candidate",
        },
        format="json",
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["promoted_count"] == 1
    assert result["created_count"] == 1
    assert result["skipped_count"] == 0
    assert result["entries"][0]["operation"] == "created"
    allowlist = OnboardingLifecycleSendAllowlist.no_workspace_objects.get(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        campaign_group=evaluation_log.campaign_group,
        environment="local",
    )
    assert allowlist.enabled is True
    assert allowlist.created_by == user
    assert "Reviewed internal digest candidate" in allowlist.reason
    assert str(evaluation_log.id) in allowlist.reason


@pytest.mark.django_db
def test_digest_preview_promotion_dry_run_skips_unreviewable_sources(
    auth_client,
    organization,
    workspace,
    user,
):
    now = timezone.now()
    missing_preview_log = _evaluation_log(
        organization,
        workspace,
        user,
        now=now,
        preview={},
    )
    campaign = lifecycle_campaign_by_key("welcome_choose_goal")
    unsupported_log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000203",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="choose_goal",
        primary_path="observe",
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=20),
        evaluated_at=now - timedelta(minutes=10),
        activation_state_snapshot={"stage": "choose_goal"},
        registry_snapshot=campaign,
        metadata={"digest_preview": _safe_preview(workspace, now=now)},
    )

    response = auth_client.post(
        "/accounts/onboarding/lifecycle/digest-previews/promote/",
        {
            "sources": [
                {
                    "source_type": "evaluation_log",
                    "source_id": str(missing_preview_log.id),
                },
                {
                    "source_type": "evaluation_log",
                    "source_id": str(unsupported_log.id),
                },
            ],
            "dry_run": True,
        },
        format="json",
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["dry_run"] is True
    assert result["promoted_count"] == 0
    assert result["created_count"] == 0
    assert {item["reason"] for item in result["skipped"]} == {
        "missing_digest_preview",
        "unsupported_campaign",
    }
    assert not OnboardingLifecycleSendAllowlist.no_workspace_objects.exists()
