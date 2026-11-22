"""Unit coverage for Error Feed email + optional Slack alert destinations."""

from unittest.mock import patch

import pytest

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from tracer.models.feed_alert import (
    ErrorFeedAlertDelivery,
    ErrorFeedAlertRule,
    ErrorFeedIssueEvent,
    FeedAlertDeliveryStatus,
    FeedAlertTrigger,
)
from tracer.models.trace_error_analysis import (
    ClusterSource,
    FeedIssueStatus,
    Priority,
    TraceErrorGroup,
)
from tracer.services.feed_alerts import events, rules


@pytest.fixture
def slack_connection(organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="Engineering Slack",
        host_url="https://slack.com",
        encrypted_credentials=b"test-credentials",
        external_project_name="T-ALERTS",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


@pytest.fixture
def issue(observe_project):
    return TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="EMAIL-ALERT-1",
        error_type="tool_failure",
        source=ClusterSource.SCANNER,
        issue_group="Tool failures",
        issue_category="Timeout",
        title="Checkout inventory API timed out",
        error_count=4,
        total_events=4,
        unique_traces=3,
        priority=Priority.HIGH,
        status=FeedIssueStatus.ESCALATING,
        severity_assessment_status="assessed",
        severity_source="model",
    )


@pytest.mark.django_db
class TestErrorFeedEmailAlerts:
    def test_create_email_only_rule_without_slack_connection(
        self, monkeypatch, organization, workspace, user, observe_project
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        result = rules.create_rule(
            data={
                "name": "Email-only escalation rule",
                "project_id": str(observe_project.id),
                "trigger_type": FeedAlertTrigger.NEW_ISSUE,
                "slack_enabled": False,
                "email_enabled": True,
                "email_recipients": [" OnCall@Example.com ", "oncall@example.com"],
                "cooldown_seconds": 300,
            },
            organization=organization,
            workspace=workspace,
            user=user,
        )

        assert result["slack_enabled"] is False
        assert result["slack_connection_id"] is None
        assert result["email_enabled"] is True
        assert result["email_recipients"] == ["oncall@example.com"]
        stored = ErrorFeedAlertRule.no_workspace_objects.get(pk=result["id"])
        assert stored.slack_connection is None
        assert stored.email_enabled is True
        assert stored.email_recipients == ["oncall@example.com"]

    def test_create_rejects_neither_slack_nor_email_enabled(
        self, monkeypatch, organization, workspace, user
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        with pytest.raises(
            rules.FeedAlertError, match="Enable at least one notification destination"
        ):
            rules.create_rule(
                data={
                    "name": "Disabled channels",
                    "trigger_type": FeedAlertTrigger.NEW_ISSUE,
                    "slack_enabled": False,
                    "email_enabled": False,
                },
                organization=organization,
                workspace=workspace,
                user=user,
            )

    def test_create_rejects_email_enabled_without_valid_recipients(
        self, monkeypatch, organization, workspace, user
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        with pytest.raises(rules.FeedAlertError, match="Invalid email address"):
            rules.create_rule(
                data={
                    "name": "Bad email",
                    "trigger_type": FeedAlertTrigger.NEW_ISSUE,
                    "slack_enabled": False,
                    "email_enabled": True,
                    "email_recipients": ["not-an-email"],
                },
                organization=organization,
                workspace=workspace,
                user=user,
            )

    def test_test_rule_sends_email_only_notification(
        self, monkeypatch, organization, workspace, user, observe_project
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        rule = ErrorFeedAlertRule.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            project=observe_project,
            name="Email test rule",
            trigger_type=FeedAlertTrigger.NEW_ISSUE,
            slack_enabled=False,
            slack_connection=None,
            slack_channel_id="",
            slack_channel_name="",
            email_enabled=True,
            email_recipients=["alerts@example.com"],
        )

        with patch("tfc.utils.email.email_helper") as send_email:
            result = rules.test_rule(
                rule_id=rule.id,
                organization=organization,
                workspace=workspace,
                user=user,
            )

        assert result == {"sent": True, "slack_ts": ""}
        send_email.assert_called_once()
        assert send_email.call_args.kwargs["to_email_list"] == ["alerts@example.com"]
        assert send_email.call_args.kwargs["template_name"] == "alert_user.html"

    def test_queue_and_deliver_both_slack_and_email(
        self,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
        issue,
    ):
        rule = ErrorFeedAlertRule.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            project=observe_project,
            name="Dual channel alert",
            trigger_type=FeedAlertTrigger.NEW_ISSUE,
            slack_enabled=True,
            slack_connection=slack_connection,
            slack_channel_id="C99999999",
            slack_channel_name="incidents",
            email_enabled=True,
            email_recipients=["sre@example.com"],
        )
        events.record_issue_event(
            cluster=issue,
            before=None,
            source_key=f"test-dual:{issue.id}",
        )
        event = ErrorFeedIssueEvent.no_workspace_objects.get(
            source_key=f"test-dual:{issue.id}"
        )
        events._queue_event(event)

        deliveries = list(
            ErrorFeedAlertDelivery.no_workspace_objects.filter(
                rule=rule, event=event
            ).order_by("channel_id")
        )
        assert {d.channel_id for d in deliveries} == {"C99999999", "email"}

        email_delivery = next(d for d in deliveries if d.channel_id == "email")
        with patch("tfc.utils.email.email_helper") as send_email:
            events._send_delivery(email_delivery.id)

        email_delivery.refresh_from_db()
        assert email_delivery.status == FeedAlertDeliveryStatus.SENT
        assert email_delivery.sent_at is not None
        send_email.assert_called_once()
        assert send_email.call_args.kwargs["to_email_list"] == ["sre@example.com"]
