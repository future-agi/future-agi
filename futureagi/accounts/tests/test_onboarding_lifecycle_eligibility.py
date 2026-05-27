from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingQualityAction,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
)
from accounts.services.onboarding.sample_project import hide_sample_project
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import create_observe_project


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
        "onboarding_email_sample_bridge_enabled": True,
        "onboarding_email_daily_digest_enabled": True,
        "onboarding_home_enabled": True,
        "onboarding_observe_mvp_enabled": True,
        "onboarding_sample_project_enabled": False,
        "onboarding_lifecycle_dry_run_enabled": True,
        "onboarding_lifecycle_send_enabled": False,
        "daily_quality_home_enabled": False,
        "activation_state_debug_enabled": False,
    }
    flags.update(overrides)
    return flags


def _context(
    user,
    organization,
    workspace,
    *,
    goal="monitor_production_ai_app",
):
    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role="Owner",
        workspace_role="workspace_admin",
        organization_level=15,
        workspace_level=8,
        selected_goal=goal,
        primary_path="observe" if goal == "monitor_production_ai_app" else None,
        persona="developer",
        source="test",
        email_context=None,
        permissions={
            "role": "Owner",
            "can_read": True,
            "can_write": True,
            "can_manage_workspace": True,
            "missing_permissions": [],
            "request_access_href": "/dashboard/settings/user-management",
            "permission_limited": False,
        },
        warnings=[],
    )


def _set_workspace_created_at(workspace, value):
    Workspace.no_workspace_objects.filter(id=workspace.id).update(created_at=value)
    workspace.refresh_from_db()


def _activation_state(
    user,
    organization,
    workspace,
    *,
    flags=None,
    goal="monitor_production_ai_app",
):
    return resolve_activation_state(
        context=_context(user, organization, workspace, goal=goal),
        flags=flags or _flags(),
        signals=collect_onboarding_signals(
            user=user,
            organization=organization,
            workspace=workspace,
        ),
    )


@pytest.mark.django_db
def test_no_goal_after_wait_is_eligible_for_welcome(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))
    flags = _flags()
    activation_state = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=flags,
        signals=OnboardingSignals(first_checks={}),
    )

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert decision.campaign["campaign_key"] == "welcome_choose_goal"
    assert "campaign_key=welcome_choose_goal" in decision.target_url


@pytest.mark.django_db
def test_wait_window_suppresses_before_campaign_is_ready(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        selected_at=now - timedelta(minutes=10),
    )
    flags = _flags()
    activation_state = _activation_state(user, organization, workspace, flags=flags)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.campaign["campaign_key"] == "welcome_resume_goal"
    assert decision.suppression_reason == "wait_window_open"


@pytest.mark.django_db
def test_later_wait_window_campaign_wins_after_more_time(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        selected_at=now - timedelta(minutes=90),
    )
    flags = _flags()
    activation_state = _activation_state(user, organization, workspace, flags=flags)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert decision.campaign["campaign_key"] == "observe_connect_first"


@pytest.mark.django_db
def test_completed_target_event_suppresses_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        selected_at=now - timedelta(hours=2),
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="observe_project_created",
        product_path="observe",
        source="test",
    )
    flags = _flags()
    activation_state = _activation_state(user, organization, workspace, flags=flags)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "target_event_complete"


@pytest.mark.django_db
def test_daily_quality_open_action_digest_is_repeatable_after_prior_completion(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_completed",
        product_path="observe",
        source="test",
        occurred_at=now - timedelta(minutes=30),
    )
    flags = _flags()
    activation_state = {
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
        "daily_quality": {"mode": "open_action"},
        "route_availability": {
            "daily_quality_home": {
                "href": "/dashboard/home?mode=daily-quality",
                "is_available": True,
                "reason": None,
            }
        },
        "last_meaningful_event": {
            "occurred_at": now - timedelta(hours=2),
            "is_sample": False,
        },
    }

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert decision.campaign["campaign_key"] == "daily_quality_open_actions"
    assert decision.campaign["target_success_event"] == (
        "daily_quality_action_completed"
    )
    assert "campaign_key=daily_quality_open_actions" in decision.target_url


@pytest.mark.django_db
def test_daily_quality_open_action_digest_preview_uses_safe_action_snapshot(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    OnboardingQualityAction.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        product_path="observe",
        action_key="trace-action-1",
        status=OnboardingQualityAction.STATUS_OPEN,
        label="Assign trace owner",
        body="Internal investigation text should stay out of the digest preview.",
        route="https://example.invalid/private-trace",
        fallback_route="https://example.invalid/fallback",
        source_type="trace",
        source_id="trace-123",
        is_sample=False,
        last_event_at=now - timedelta(minutes=75),
        metadata={
            "api_token": "secret-value",
            "raw_payload": {"request": "private prompt text"},
        },
    )
    flags = _flags()
    activation_state = {
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
        "daily_quality": {"mode": "open_action"},
        "route_availability": {
            "daily_quality_home": {
                "href": "/dashboard/home?mode=daily-quality",
                "is_available": True,
                "reason": None,
            }
        },
        "last_meaningful_event": {
            "occurred_at": now - timedelta(hours=2),
            "is_sample": False,
        },
    }

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    preview = decision.metadata["digest_preview"]
    assert preview["kind"] == "daily_quality_open_actions"
    assert preview["action_count"] == 1
    assert preview["omitted_count"] == 0
    assert preview["safe_fields"] == [
        "action_id",
        "label",
        "route",
        "fallback_route",
        "source_type",
        "source_id",
        "primary_path",
        "status",
        "age_minutes",
        "last_event_at",
    ]
    assert preview["actions"] == [
        {
            "action_id": "trace-action-1",
            "label": "Assign trace owner",
            "route": "/dashboard/home",
            "fallback_route": "/dashboard/get-started",
            "source_type": "trace",
            "source_id": "trace-123",
            "primary_path": "observe",
            "status": OnboardingQualityAction.STATUS_OPEN,
            "age_minutes": 75,
            "last_event_at": (now - timedelta(minutes=75)).isoformat(),
        }
    ]
    assert "body" not in preview["actions"][0]
    assert "metadata" not in preview["actions"][0]
    assert "secret-value" not in str(preview)
    assert "private prompt text" not in str(preview)


@pytest.mark.django_db
def test_non_digest_campaign_does_not_include_digest_preview(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))
    flags = _flags()
    activation_state = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=flags,
        signals=OnboardingSignals(first_checks={}),
    )

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.campaign["campaign_key"] == "welcome_choose_goal"
    assert "digest_preview" not in decision.metadata


@pytest.mark.django_db
def test_hidden_sample_suppresses_sample_bridge(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    project.created_at = now - timedelta(days=2)
    project.save(update_fields=["created_at"])
    hide_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="user_hidden",
    )
    flags = _flags(
        onboarding_sample_project=True,
        onboarding_sample_project_enabled=True,
    )
    activation_state = _activation_state(user, organization, workspace, flags=flags)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
        campaign_key="observe_sample_bridge",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "sample_hidden"


@pytest.mark.django_db
def test_preference_unsubscribe_suppresses_lifecycle_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))
    OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        onboarding_enabled=False,
    )
    flags = _flags()
    activation_state = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=flags,
        signals=OnboardingSignals(first_checks={}),
    )

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "user_unsubscribed"


@pytest.mark.django_db
def test_frequency_cap_suppresses_after_recent_eligible_log(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000001",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key="welcome_choose_goal",
        activation_stage="choose_goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        evaluated_at=now - timedelta(hours=1),
    )
    flags = _flags()
    activation_state = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=flags,
        signals=OnboardingSignals(first_checks={}),
    )

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "frequency_cap_user_24h"
