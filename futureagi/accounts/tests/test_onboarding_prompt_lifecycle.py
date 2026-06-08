from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import (
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
)
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
)
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import (
    create_prompt_template,
    create_prompt_version,
)


def _flags(**overrides):
    flags = {
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
        "onboarding_sample_project": False,
        "onboarding_daily_quality_home": False,
        "onboarding_prompt_path": True,
        "onboarding_prompt_route_modes": True,
        "onboarding_lifecycle_email_dry_run": True,
        "onboarding_email_welcome_enabled": True,
        "onboarding_email_first_action_recovery_enabled": True,
        "onboarding_email_first_signal_enabled": True,
        "onboarding_email_next_loop_enabled": True,
        "onboarding_email_sample_bridge_enabled": True,
        "onboarding_email_daily_digest_enabled": True,
        "onboarding_email_prompt_enabled": True,
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


def _context(user, organization, workspace):
    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role="Owner",
        workspace_role="workspace_admin",
        organization_level=15,
        workspace_level=8,
        selected_goal="improve_prompts",
        primary_path="prompt",
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


def _select_prompt_goal(user, organization, workspace, selected_at):
    return OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="improve_prompts",
        primary_path="prompt",
        selected_at=selected_at,
    )


def _activation_state(user, organization, workspace, *, flags=None, signals=None):
    return resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=flags or _flags(),
        signals=signals
        or collect_onboarding_signals(
            user=user,
            organization=organization,
            workspace=workspace,
        ),
    )


@pytest.mark.django_db
def test_prompt_create_campaign_is_eligible_after_wait(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_prompt_goal(user, organization, workspace, now - timedelta(hours=2))
    flags = _flags()
    activation_state = _activation_state(
        user,
        organization,
        workspace,
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
    assert decision.campaign["campaign_key"] == "prompt_create_first"
    assert "campaign_key=prompt_create_first" in decision.target_url


@pytest.mark.django_db
def test_prompt_run_campaign_is_eligible_after_prompt_created(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_prompt_goal(user, organization, workspace, now - timedelta(days=1))
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(template=template, output=[])
    type(template).no_workspace_objects.filter(id=template.id).update(
        created_at=now - timedelta(hours=6)
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
    assert decision.campaign["campaign_key"] == "prompt_run_first_test"
    assert decision.target_url and "onboarding=run-test" in decision.target_url


@pytest.mark.django_db
def test_prompt_completed_target_event_suppresses_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_prompt_goal(user, organization, workspace, now - timedelta(hours=2))
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="prompt_created",
        source="test",
        product_path="prompt",
        activation_stage="start_prompt",
    )
    flags = _flags()
    activation_state = _activation_state(
        user,
        organization,
        workspace,
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
        campaign_key="prompt_create_first",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "target_event_complete"


@pytest.mark.django_db
def test_prompt_preference_toggle_suppresses_prompt_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_prompt_goal(user, organization, workspace, now - timedelta(hours=2))
    OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        first_action_recovery_enabled=False,
    )
    flags = _flags()
    activation_state = _activation_state(
        user,
        organization,
        workspace,
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
    assert decision.campaign["campaign_group"] == "prompt"
    assert decision.suppression_reason == "user_unsubscribed"
