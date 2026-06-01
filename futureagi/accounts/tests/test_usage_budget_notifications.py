from accounts.models import NotificationPreference
from accounts.services.onboarding.notification_delivery import _slack_payload
from accounts.services.onboarding.usage_budget_notifications import (
    build_usage_budget_threshold_payload,
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
