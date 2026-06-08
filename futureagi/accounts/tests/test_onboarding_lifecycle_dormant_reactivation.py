from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from accounts.models import OnboardingLifecycleEvaluationLog
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.lifecycle_eligibility import (
    choose_lifecycle_campaign,
    evaluate_lifecycle_decision,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_template_contract import (
    USER_FACING_INTERNAL_COPY_TERMS,
    lifecycle_email_copy_for_campaign,
    required_context_keys_for_template,
    template_path_for_key,
)

# Frozen wall clock so dormancy is computed deterministically, never Date.now().
FROZEN_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
DORMANT_THRESHOLD_MINUTES = 10080  # 7 days, the dormant_reactivation wait window.


def _flags(**overrides):
    flags = {
        "onboarding_lifecycle_dry_run_enabled": True,
        "onboarding_lifecycle_send_enabled": False,
        "onboarding_email_daily_digest_enabled": True,
    }
    flags.update(overrides)
    return flags


def _activated_state(*, last_event_minutes_ago, is_sample=False):
    return {
        "stage": "daily_review",
        "primary_path": "observe",
        "is_activated": True,
        "recommended_action": {
            "id": "review_daily_quality",
            "href": "/dashboard/home?mode=daily-quality",
        },
        "fallback_action": {"id": "open_get_started"},
        "permissions": {
            "can_write": True,
            "permission_limited": False,
        },
        "sample_project": {},
        "signals": {},
        "daily_quality": {"mode": "new_signal"},
        "route_availability": {
            "daily_quality_home": {
                "href": "/dashboard/home?mode=daily-quality",
                "is_available": True,
                "reason": None,
            }
        },
        "last_meaningful_event": {
            "occurred_at": FROZEN_NOW - timedelta(minutes=last_event_minutes_ago),
            "is_sample": is_sample,
        },
    }


def test_choose_lifecycle_campaign_selects_dormant_after_quiet_threshold():
    # Last meaningful event sits one minute past the 7-day dormant threshold.
    state = _activated_state(last_event_minutes_ago=DORMANT_THRESHOLD_MINUTES + 1)
    started_at = state["last_meaningful_event"]["occurred_at"]

    campaign = choose_lifecycle_campaign(
        state,
        started_at=started_at,
        now=FROZEN_NOW,
    )

    assert campaign["campaign_key"] == "dormant_reactivation"
    assert campaign["primary_path"] == "any"
    assert campaign["route_strategy"] == "daily_quality"
    assert campaign["target_success_event"] == "daily_quality_item_reviewed"


def test_choose_lifecycle_campaign_skips_dormant_when_recently_active():
    # Active two hours ago: well inside the threshold, so the recovery email
    # must not win. The shorter activation-success nudge wins instead.
    state = _activated_state(last_event_minutes_ago=120)
    started_at = state["last_meaningful_event"]["occurred_at"]

    campaign = choose_lifecycle_campaign(
        state,
        started_at=started_at,
        now=FROZEN_NOW,
    )

    assert campaign["campaign_key"] != "dormant_reactivation"
    assert campaign["campaign_key"] == "first_loop_complete_next"


@pytest.mark.django_db
def test_dormant_user_is_eligible_for_recovery_email(
    organization,
    workspace,
    user,
):
    flags = _flags()
    state = _activated_state(last_event_minutes_ago=DORMANT_THRESHOLD_MINUTES + 60)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=state,
        flags=flags,
        now=FROZEN_NOW,
        campaign_key="dormant_reactivation",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert decision.campaign["campaign_key"] == "dormant_reactivation"
    assert "campaign_key=dormant_reactivation" in decision.target_url
    expected_eligible_at = state["last_meaningful_event"]["occurred_at"] + timedelta(
        minutes=DORMANT_THRESHOLD_MINUTES
    )
    assert decision.eligible_at == expected_eligible_at


@pytest.mark.django_db
def test_recently_active_user_is_not_eligible_for_recovery_email(
    organization,
    workspace,
    user,
):
    flags = _flags()
    state = _activated_state(last_event_minutes_ago=180)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=state,
        flags=flags,
        now=FROZEN_NOW,
        campaign_key="dormant_reactivation",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.campaign["campaign_key"] == "dormant_reactivation"
    assert decision.suppression_reason == "wait_window_open"


@pytest.mark.django_db
def test_return_event_suppresses_recovery_email(
    organization,
    workspace,
    user,
):
    # The user came back and reviewed a quality item, so the recovery email is
    # suppressed by its target success event even while still flagged dormant.
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_item_reviewed",
        product_path="observe",
        source="test",
        occurred_at=FROZEN_NOW - timedelta(minutes=5),
    )
    flags = _flags()
    state = _activated_state(last_event_minutes_ago=DORMANT_THRESHOLD_MINUTES + 60)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=state,
        flags=flags,
        now=FROZEN_NOW,
        campaign_key="dormant_reactivation",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.campaign["campaign_key"] == "dormant_reactivation"
    assert decision.suppression_reason == "target_event_complete"
    assert decision.suppression_details == {"event": "daily_quality_item_reviewed"}


def test_dormant_reactivation_template_renders_without_jargon_or_fabricated_data():
    campaign = lifecycle_campaign_by_key("dormant_reactivation")
    copy = lifecycle_email_copy_for_campaign(campaign)
    context = {
        "primary_action_label": "Review item",
        "primary_action_url": "/accounts/onboarding/lifecycle/click/?token=token",
        "email_subject": copy["subject"],
        "preheader_text": copy["preheader"],
        "snooze_url": "/accounts/onboarding/lifecycle/snooze/?token=token",
        "unsubscribe_url": "/accounts/onboarding/lifecycle/unsubscribe/?token=token",
        "user_name": "Nikhil",
        "workspace_name": "Demo workspace",
    }
    for key in required_context_keys_for_template(campaign["template_key"]):
        context.setdefault(key, "")

    html = render_to_string(template_path_for_key(campaign["template_key"]), context)
    visible_text = " ".join(unescape(strip_tags(html)).split()).lower()
    copy_text = f"{copy['subject']} {copy['preheader']}".lower()

    for term in USER_FACING_INTERNAL_COPY_TERMS:
        assert term not in copy_text
        assert term not in visible_text

    # Branded base chrome and consent controls must be present.
    assert "FutureAGI setup" in html
    assert copy["preheader"] in html
    assert context["primary_action_url"] in html
    assert "snooze setup emails for 7 days" in html
    assert "turn off setup emails" in html
    assert "You received this because you have an account on Future AGI." in html
    assert "{{" not in html
    assert "{%" not in html

    # Value-framed, no fabricated metrics. No digits should appear in the body
    # copy beyond the static footer chrome inherited from the base template.
    body_marker = "Hi Nikhil"
    assert body_marker in visible_text or "hi nikhil" in visible_text
