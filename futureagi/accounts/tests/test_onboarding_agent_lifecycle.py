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
    create_agent_graph,
    create_agent_graph_node,
    create_graph_execution,
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
        "onboarding_lifecycle_email_dry_run": True,
        "onboarding_email_welcome_enabled": True,
        "onboarding_email_first_action_recovery_enabled": True,
        "onboarding_email_first_signal_enabled": True,
        "onboarding_email_next_loop_enabled": True,
        "onboarding_email_sample_bridge_enabled": True,
        "onboarding_email_daily_digest_enabled": True,
        "onboarding_email_prompt_enabled": True,
        "onboarding_email_agent_enabled": True,
        "onboarding_email_agent": True,
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
        selected_goal="build_ai_agent",
        primary_path="agent",
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


def _select_agent_goal(user, organization, workspace, selected_at):
    return OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="build_ai_agent",
        primary_path="agent",
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
def test_agent_create_campaign_is_eligible_after_wait(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_agent_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_key"] == "agent_create_first"
    assert "campaign_key=agent_create_first" in decision.target_url


@pytest.mark.django_db
def test_agent_run_campaign_is_eligible_after_agent_created(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_agent_goal(user, organization, workspace, now - timedelta(days=1))
    graph, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_agent_graph_node(graph_version=version)
    type(graph).no_workspace_objects.filter(id=graph.id).update(
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
    assert decision.campaign["campaign_key"] == "agent_run_first_scenario"
    assert "onboarding=run-scenario" in decision.target_url


@pytest.mark.django_db
def test_agent_completed_target_event_suppresses_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_agent_goal(user, organization, workspace, now - timedelta(hours=2))
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="agent_created",
        source="test",
        product_path="agent",
        activation_stage="create_agent",
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
        campaign_key="agent_create_first",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "target_event_complete"


@pytest.mark.django_db
def test_agent_preference_toggle_suppresses_agent_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_agent_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_group"] == "agent"
    assert decision.suppression_reason == "user_unsubscribed"


@pytest.mark.django_db
def test_agent_review_campaign_uses_run_completion_start(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_agent_goal(user, organization, workspace, now - timedelta(days=1))
    _graph, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    execution = create_graph_execution(
        graph_version=version,
        completed_at=now - timedelta(hours=3),
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="graph_execution_completed",
        source="test",
        product_path="agent",
        activation_stage="run_agent_scenario",
        metadata={"graph_execution_id": str(execution.id)},
        occurred_at=now - timedelta(hours=3),
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
    assert decision.campaign["campaign_key"] == "agent_review_failed_scenario"
