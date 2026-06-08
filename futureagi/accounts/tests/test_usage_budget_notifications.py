import pytest

from accounts.models import NotificationDeliveryLog, NotificationPreference
from accounts.services.onboarding.notification_delivery import _slack_payload
from accounts.services.onboarding.usage_budget_notifications import (
    build_usage_budget_threshold_payload,
    deliver_usage_budget_threshold_notifications_for_usage,
    usage_budget_threshold_stages_for_usage,
)


def test_usage_budget_threshold_payload_uses_safe_shared_notification_shape():
    payload = build_usage_budget_threshold_payload(
        budget_id="budget-123",
        budget_name="AI Credits guardrail",
        scope="ai_credits",
        period="2026-06",
        threshold_percent=80,
        threshold_value="5000",
        current_usage="4100",
        action="warn",
        metadata={
            "dimension": "ai_credits",
            "api_token": "secret-value",
            "source": "budget_evaluator",
        },
    )

    assert payload["type"] == "usage_budget_threshold"
    assert payload["family"] == NotificationPreference.FAMILY_USAGE_BUDGET
    assert payload["notification_key"] == "budget_threshold_80"
    assert payload["stage"] == "80"
    assert payload["severity"] == "warning"
    assert payload["summary"] == {
        "budget_name": "AI Credits guardrail",
        "scope": "ai_credits",
        "period": "2026-06",
        "stage_percent": 80,
        "threshold_value": "5000",
        "current_usage": "4100",
        "action": "warn",
        "action_label": "Warn",
    }
    assert payload["metadata"] == {
        "dimension": "ai_credits",
        "source": "budget_evaluator",
    }
    assert "secret-value" not in str(payload)


def test_usage_budget_threshold_stages_for_usage_selects_crossed_stages():
    assert (
        usage_budget_threshold_stages_for_usage(
            current_usage="2499",
            budget_limit="5000",
        )
        == ()
    )
    assert usage_budget_threshold_stages_for_usage(
        current_usage="2500",
        budget_limit="5000",
    ) == (50,)
    assert usage_budget_threshold_stages_for_usage(
        current_usage="4100",
        budget_limit="5000",
    ) == (50, 80)
    assert usage_budget_threshold_stages_for_usage(
        current_usage="5000",
        budget_limit="5000",
    ) == (50, 80, 100)


@pytest.mark.django_db
def test_usage_budget_threshold_delivery_sends_each_stage_once(
    monkeypatch,
    organization,
    workspace,
    user,
):
    sent_emails = []

    def fake_email_helper(subject, template, context, recipients):
        sent_emails.append(
            {
                "subject": subject,
                "template": template,
                "context": context,
                "recipients": recipients,
            }
        )

    monkeypatch.setattr(
        "accounts.services.onboarding.usage_budget_notifications.email_helper",
        fake_email_helper,
    )

    logs = deliver_usage_budget_threshold_notifications_for_usage(
        organization=organization,
        workspace=workspace,
        user=user,
        budget_id="budget-123",
        budget_name="AI Credits guardrail",
        scope="ai_credits",
        period="2026-06",
        budget_limit="5000",
        current_usage="4100",
        recipients=["owner@example.com"],
    )

    assert [log.notification_key for log in logs] == [
        "budget_threshold_50",
        "budget_threshold_80",
    ]
    assert [email["template"] for email in sent_emails] == [
        "billing/usage_budget_notify.html",
        "billing/usage_budget_warn.html",
    ]

    deliver_usage_budget_threshold_notifications_for_usage(
        organization=organization,
        workspace=workspace,
        user=user,
        budget_id="budget-123",
        budget_name="AI Credits guardrail",
        scope="ai_credits",
        period="2026-06",
        budget_limit="5000",
        current_usage="4100",
        recipients=["owner@example.com"],
    )

    assert len(sent_emails) == 2
    assert (
        NotificationDeliveryLog.no_workspace_objects.filter(
            organization=organization,
            family=NotificationPreference.FAMILY_USAGE_BUDGET,
            source_id="budget-123",
            status=NotificationDeliveryLog.STATUS_SENT,
        ).count()
        == 2
    )


def test_usage_budget_slack_payload_points_to_budget_settings():
    payload = build_usage_budget_threshold_payload(
        budget_id="budget-123",
        budget_name="Gateway requests",
        scope="gateway_requests",
        period="2026-06",
        threshold_percent=100,
        threshold_value="10000",
        current_usage="10050",
        action="pause",
    )

    slack = _slack_payload(payload)

    assert slack == {
        "text": (
            "Usage budget 100%: Gateway requests is at 10050 of 10000. "
            "Action: Pause usage. Review: /dashboard/settings/billing"
        )
    }
