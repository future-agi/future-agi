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
    create_agent_definition,
    create_agent_scenario,
    create_agent_version,
    create_call_execution,
    create_run_test,
    create_test_execution,
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
        "onboarding_agent_path": True,
        "onboarding_agent_route_modes": True,
        "onboarding_gateway_path": True,
        "onboarding_gateway_route_modes": True,
        "onboarding_eval_path": True,
        "onboarding_eval_route_modes": True,
        "onboarding_voice_path": True,
        "onboarding_voice_route_modes": True,
        "onboarding_lifecycle_email_dry_run": True,
        "onboarding_email_welcome_enabled": True,
        "onboarding_email_first_action_recovery_enabled": True,
        "onboarding_email_first_signal_enabled": True,
        "onboarding_email_next_loop_enabled": True,
        "onboarding_email_sample_bridge_enabled": True,
        "onboarding_email_daily_digest_enabled": True,
        "onboarding_email_voice": True,
        "onboarding_voice_notifications": True,
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
        selected_goal="connect_voice_ai_agent",
        primary_path="voice",
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


def _select_voice_goal(user, organization, workspace, selected_at):
    return OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="connect_voice_ai_agent",
        primary_path="voice",
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


def _voice_call_setup(*, organization, workspace, completed_at, status="completed"):
    agent = create_agent_definition(
        organization=organization,
        workspace=workspace,
        agent_type="voice",
    )
    version = create_agent_version(agent_definition=agent)
    scenario = create_agent_scenario(
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    run_test = create_run_test(
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        agent_version=version,
        scenario=scenario,
    )
    execution = create_test_execution(
        run_test=run_test,
        status=status,
        completed_at=completed_at,
    )
    call = create_call_execution(
        test_execution=execution,
        scenario=scenario,
        status=status,
        simulation_call_type="voice",
        recording_available=status == "completed",
        response_time_ms=640,
    )
    return agent, run_test, call


def _record_voice_review(*, user, organization, workspace, call, occurred_at):
    return record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="voice_call_reviewed",
        source="test",
        product_path="voice",
        activation_stage="review_voice_call",
        occurred_at=occurred_at,
        metadata={"call_execution_id": str(call.id)},
    )


@pytest.mark.django_db
def test_voice_agent_campaign_is_eligible_after_wait(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_key"] == "voice_create_agent"
    assert "campaign_key=voice_create_agent" in decision.target_url


@pytest.mark.django_db
def test_voice_run_call_campaign_is_eligible_after_agent_created(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(days=1))
    agent = create_agent_definition(
        organization=organization,
        workspace=workspace,
        agent_type="voice",
    )
    type(agent).no_workspace_objects.filter(id=agent.id).update(
        created_at=now - timedelta(hours=6),
        updated_at=now - timedelta(hours=6),
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
    assert decision.campaign["campaign_key"] == "voice_run_test_call"
    assert "onboarding=create-test-call" in decision.target_url


@pytest.mark.django_db
def test_voice_review_campaign_is_eligible_after_call_completed(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(days=1))
    _, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
        completed_at=now - timedelta(hours=1),
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
    assert decision.campaign["campaign_key"] == "voice_review_call"
    assert "campaign_key=voice_review_call" in decision.target_url
    assert str(call.id) in decision.target_url


@pytest.mark.django_db
def test_voice_success_criteria_campaign_is_eligible_after_review(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(days=2))
    _, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
        completed_at=now - timedelta(days=2),
    )
    _record_voice_review(
        user=user,
        organization=organization,
        workspace=workspace,
        call=call,
        occurred_at=now - timedelta(days=2),
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
    assert decision.campaign["campaign_key"] == "voice_add_success_criteria"
    assert "onboarding=success-criteria" in decision.target_url


@pytest.mark.django_db
def test_voice_retry_wait_window_starts_after_success_criteria(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(days=3))
    agent, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
        completed_at=now - timedelta(days=2),
        status="failed",
    )
    type(agent).no_workspace_objects.filter(id=agent.id).update(
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(days=3),
    )
    _record_voice_review(
        user=user,
        organization=organization,
        workspace=workspace,
        call=call,
        occurred_at=now - timedelta(days=2),
    )
    criteria_at = now - timedelta(hours=1)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="voice_success_criteria_added",
        source="test",
        product_path="voice",
        activation_stage="add_voice_success_criteria",
        occurred_at=criteria_at,
        metadata={"call_execution_id": str(call.id)},
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

    assert activation_state["stage"] == "run_voice_test_call"
    assert decision.campaign["campaign_key"] == "voice_run_test_call"
    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "wait_window_open"
    assert decision.eligible_at == criteria_at + timedelta(minutes=240)


@pytest.mark.django_db
def test_voice_completed_target_event_suppresses_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(hours=2))
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="voice_agent_created",
        source="test",
        product_path="voice",
        activation_stage="create_voice_agent",
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
        campaign_key="voice_create_agent",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "target_event_complete"


@pytest.mark.django_db
def test_voice_preference_toggle_suppresses_voice_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_voice_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_group"] == "voice"
    assert decision.suppression_reason == "user_unsubscribed"
