from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
)
from accounts.services.onboarding.notification_preferences import (
    notification_channel_delivery_config,
    notification_preference_decision,
    record_notification_delivery,
    upsert_notification_preference,
)


@pytest.mark.django_db
def test_notification_settings_get_returns_registered_families(auth_client):
    response = auth_client.get("/accounts/notification-preferences/")

    assert response.status_code == 200
    result = response.json()["result"]
    family_ids = {family["id"] for family in result["families"]}
    assert "product_onboarding" in family_ids
    assert "usage_budget" in family_ids
    assert result["can_manage_workspace"] is True


@pytest.mark.django_db
def test_notification_settings_get_returns_recent_delivery_logs(
    auth_client,
    organization,
    workspace,
    user,
):
    record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        source_type="onboarding_lifecycle",
        source_id="welcome-1",
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=NotificationDeliveryLog.STATUS_SENT,
        recipient_type="email",
        recipient_identifier="owner@example.com",
        notification_key="welcome_choose_goal",
        stage="choose_goal",
        severity="info",
    )
    record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        source_type="usage_budget",
        source_id="budget-1",
        channel=NotificationPreference.CHANNEL_SLACK,
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        recipient_type="slack_webhook",
        recipient_identifier="alerts@example.com",
        notification_key="budget_threshold_80",
        stage="80",
        severity="warning",
        suppressed_reason="channel_disabled",
    )

    response = auth_client.get("/accounts/notification-preferences/")

    assert response.status_code == 200
    logs = response.json()["result"]["delivery_logs"]
    assert [log["notification_key"] for log in logs[:2]] == [
        "budget_threshold_80",
        "welcome_choose_goal",
    ]
    assert logs[0]["recipient_identifier_masked"] == "al***@example.com"
    assert logs[0]["status"] == NotificationDeliveryLog.STATUS_SUPPRESSED
    assert logs[0]["suppressed_reason"] == "channel_disabled"


@pytest.mark.django_db
def test_user_can_disable_non_critical_onboarding_email(
    auth_client,
    organization,
    workspace,
    user,
):
    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "preferences": [
                {
                    "scope": "user_workspace",
                    "family": "product_onboarding",
                    "channel": "email",
                    "enabled": False,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        channel=NotificationPreference.CHANNEL_EMAIL,
    )
    assert decision.allowed is False
    assert decision.reason == "user_disabled_family"


@pytest.mark.django_db
def test_usage_budget_remains_owner_visible_when_user_disables_email(
    auth_client,
    organization,
    workspace,
    user,
):
    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "preferences": [
                {
                    "scope": "user_workspace",
                    "family": "usage_budget",
                    "channel": "email",
                    "enabled": False,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        channel=NotificationPreference.CHANNEL_EMAIL,
    )
    assert decision.allowed is True
    assert decision.reason == "critical_family_owner_visible"


@pytest.mark.django_db
def test_slack_channel_is_masked_and_testable(auth_client):
    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "scope": "workspace",
                    "type": "slack_webhook",
                    "display_name": "Workspace alerts",
                    "config": {
                        "webhook_url": (
                            "https://hooks.slack.com/services/T000/B000/secret"
                        )
                    },
                    "is_active": True,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    channel = NotificationChannel.no_workspace_objects.get()
    assert (
        "secret"
        not in response.json()["result"]["channels"][0]["config"]["webhook_url"]
    )

    test_response = auth_client.post(
        f"/accounts/notification-channels/{channel.id}/test/",
        {},
        format="json",
    )

    channel.refresh_from_db()
    assert test_response.status_code == 200
    assert channel.last_test_status == NotificationChannel.STATUS_READY
    assert NotificationDeliveryLog.no_workspace_objects.filter(
        source_id=str(channel.id),
        notification_key="notification_channel_test",
    ).exists()


@pytest.mark.django_db
def test_channel_can_be_disabled_without_resubmitting_secret_config(auth_client):
    create_response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "scope": "workspace",
                    "type": "slack_webhook",
                    "display_name": "Workspace alerts",
                    "config": {
                        "webhook_url": (
                            "https://hooks.slack.com/services/T000/B000/secret"
                        )
                    },
                    "is_active": True,
                    "metadata": {"owner": "growth"},
                }
            ]
        },
        format="json",
    )
    assert create_response.status_code == 200
    channel = NotificationChannel.no_workspace_objects.get()
    encrypted_config = channel.encrypted_config

    update_response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "id": str(channel.id),
                    "scope": "workspace",
                    "type": "slack_webhook",
                    "display_name": "Workspace alerts paused",
                    "is_active": False,
                }
            ]
        },
        format="json",
    )

    assert update_response.status_code == 200
    channel.refresh_from_db()
    assert channel.display_name == "Workspace alerts paused"
    assert channel.is_active is False
    assert channel.encrypted_config == encrypted_config
    assert channel.metadata == {"owner": "growth"}
    assert notification_channel_delivery_config(channel)["webhook_url"].endswith(
        "/secret"
    )


@pytest.mark.django_db
def test_channel_endpoint_can_be_edited_with_new_secret_config(auth_client):
    auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "scope": "workspace",
                    "type": "webhook",
                    "display_name": "Lifecycle webhook",
                    "config": {
                        "url": "https://example.com/hooks/old",
                        "secret": "old-token",
                    },
                    "is_active": True,
                }
            ]
        },
        format="json",
    )
    channel = NotificationChannel.no_workspace_objects.get()

    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "id": str(channel.id),
                    "scope": "workspace",
                    "type": "webhook",
                    "display_name": "Lifecycle webhook",
                    "config": {
                        "url": "https://example.com/hooks/new",
                        "secret": "new-token",
                    },
                    "is_active": True,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    channel.refresh_from_db()
    assert channel.target_identifier == "https://.../new"
    assert notification_channel_delivery_config(channel) == {
        "url": "https://example.com/hooks/new",
        "secret": "new-token",
    }


@pytest.mark.django_db
def test_workspace_channel_update_rejects_wrong_scope(auth_client):
    auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "scope": "workspace",
                    "type": "slack_webhook",
                    "display_name": "Workspace alerts",
                    "config": {
                        "webhook_url": (
                            "https://hooks.slack.com/services/T000/B000/secret"
                        )
                    },
                    "is_active": True,
                }
            ]
        },
        format="json",
    )
    channel = NotificationChannel.no_workspace_objects.get()

    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "id": str(channel.id),
                    "scope": "organization",
                    "type": "slack_webhook",
                    "display_name": "Moved alerts",
                    "is_active": False,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 400
    channel.refresh_from_db()
    assert channel.workspace_id is not None
    assert channel.display_name == "Workspace alerts"
    assert channel.is_active is True


@pytest.mark.django_db
def test_slack_channel_requires_enabled_family_preference(
    auth_client,
    organization,
    workspace,
    user,
):
    response = auth_client.patch(
        "/accounts/notification-preferences/",
        {
            "channels": [
                {
                    "scope": "workspace",
                    "type": "slack_webhook",
                    "display_name": "Daily quality",
                    "config": {
                        "webhook_url": (
                            "https://hooks.slack.com/services/T000/B000/secret"
                        )
                    },
                    "is_active": True,
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 200

    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        channel=NotificationPreference.CHANNEL_SLACK,
    )
    assert decision.allowed is False
    assert decision.reason == "channel_not_enabled"

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

    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        channel=NotificationPreference.CHANNEL_SLACK,
    )
    assert decision.allowed is True
    assert decision.source == "workspace"


@pytest.mark.django_db
def test_delivery_log_idempotency_updates_existing_row(organization, workspace, user):
    key = "usage_budget:test:2026-05:80:email"
    record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        source_type="usage_budget",
        source_id="budget-1",
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="frequency_cap",
        idempotency_key=key,
        stage="80",
        severity="warning",
        now=timezone.now() - timedelta(minutes=5),
    )
    record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        source_type="usage_budget",
        source_id="budget-1",
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=NotificationDeliveryLog.STATUS_SENT,
        idempotency_key=key,
        stage="80",
        severity="warning",
    )

    logs = NotificationDeliveryLog.no_workspace_objects.filter(idempotency_key=key)
    assert logs.count() == 1
    assert logs.get().status == NotificationDeliveryLog.STATUS_SENT


@pytest.mark.django_db
def test_frequency_cap_preference_suppresses_recent_sent_delivery(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    NotificationPreference.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        channel=NotificationPreference.CHANNEL_EMAIL,
        enabled=True,
        frequency_cap_minutes=90,
    )
    record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        source_type="onboarding_lifecycle",
        source_id="send-1",
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=NotificationDeliveryLog.STATUS_SENT,
        notification_key="welcome_choose_goal",
        idempotency_key="onboarding:send-1:email:sent",
        now=now - timedelta(minutes=15),
    )

    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        channel=NotificationPreference.CHANNEL_EMAIL,
        now=now,
    )

    assert decision.allowed is False
    assert decision.reason == "frequency_capped"
