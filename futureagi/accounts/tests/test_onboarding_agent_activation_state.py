from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import (
    create_agent_definition,
    create_agent_graph,
    create_agent_scenario,
    create_agent_version,
    create_call_execution,
    create_graph_execution,
    create_run_test,
    create_simulate_eval_config,
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
        "onboarding_lifecycle_email_dry_run": False,
        "onboarding_email_welcome_enabled": False,
        "onboarding_email_first_action_recovery_enabled": False,
        "onboarding_email_first_signal_enabled": False,
        "onboarding_email_next_loop_enabled": False,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
        "onboarding_email_prompt_enabled": False,
        "onboarding_email_agent_enabled": False,
        "onboarding_email_agent": False,
        "onboarding_home_enabled": True,
        "onboarding_observe_mvp_enabled": True,
        "onboarding_sample_project_enabled": False,
        "onboarding_lifecycle_dry_run_enabled": False,
        "onboarding_lifecycle_send_enabled": False,
        "daily_quality_home_enabled": False,
        "activation_state_debug_enabled": False,
    }
    flags.update(overrides)
    return flags


def _context(user, organization, workspace, *, can_write=True):
    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role="Owner" if can_write else "Viewer",
        workspace_role="workspace_admin" if can_write else "workspace_viewer",
        organization_level=15 if can_write else 1,
        workspace_level=8 if can_write else 1,
        selected_goal="build_ai_agent",
        primary_path="agent",
        persona="developer",
        source="test",
        email_context=None,
        permissions={
            "role": "Owner" if can_write else "Viewer",
            "can_read": True,
            "can_write": can_write,
            "can_manage_workspace": can_write,
            "missing_permissions": [] if can_write else ["workspace:write"],
            "request_access_href": "/dashboard/settings/user-management",
            "permission_limited": not can_write,
        },
        warnings=[],
    )


def _agent_state(user, organization, workspace, *, flags=None, signals=None):
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
def test_agent_path_without_agent_returns_create_agent(
    organization,
    workspace,
    user,
):
    payload = _agent_state(
        user,
        organization,
        workspace,
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "create_agent"
    assert payload["recommended_action"]["id"] == "create_agent"
    assert (
        payload["recommended_action"]["href"] == "/dashboard/agents?onboarding=create"
    )
    assert payload["agent"]["has_agent"] is False


@pytest.mark.django_db
def test_graph_agent_without_run_returns_run_scenario(organization, workspace, user):
    graph, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "run_agent_scenario"
    assert payload["recommended_action"]["id"] == "run_agent_scenario"
    assert payload["agent"]["agent_id"] == str(graph.id)
    assert payload["agent"]["agent_version_id"] == str(version.id)
    assert payload["recommended_action"]["href"].endswith("onboarding=run-scenario")


@pytest.mark.django_db
def test_graph_execution_returns_review_agent_trace(organization, workspace, user):
    graph, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    execution = create_graph_execution(graph_version=version)

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "review_agent_trace"
    assert payload["recommended_action"]["id"] == "review_agent_trace"
    assert payload["agent"]["graph_execution_id"] == str(execution.id)
    assert payload["agent"]["has_run"] is True
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/agents/playground/{graph.id}/executions?onboarding=review-run"
    )


@pytest.mark.django_db
def test_reviewed_graph_without_eval_returns_save_agent_eval(
    organization,
    workspace,
    user,
):
    _, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    execution = create_graph_execution(graph_version=version)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="agent_trace_reviewed",
        source="test",
        product_path="agent",
        activation_stage="review_agent_trace",
        metadata={"graph_execution_id": str(execution.id)},
    )

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "save_agent_eval"
    assert payload["recommended_action"]["id"] == "save_agent_eval"
    assert payload["agent"]["has_review"] is True


@pytest.mark.django_db
def test_agent_eval_event_activates_graph_path(organization, workspace, user):
    _, version = create_agent_graph(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    execution = create_graph_execution(graph_version=version)
    for event_name, stage in [
        ("agent_trace_reviewed", "review_agent_trace"),
        ("agent_scenario_saved_as_eval", "save_agent_eval"),
    ]:
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name=event_name,
            source="test",
            product_path="agent",
            activation_stage=stage,
            metadata={"graph_execution_id": str(execution.id)},
        )

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True
    assert payload["recommended_action"]["id"] == "open_agent_quality"
    assert payload["agent"]["has_eval_coverage"] is True


@pytest.mark.django_db
def test_simulation_agent_execution_returns_review_route(
    organization,
    workspace,
    user,
):
    agent = create_agent_definition(organization=organization, workspace=workspace)
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
    execution = create_test_execution(run_test=run_test)
    call = create_call_execution(test_execution=execution, scenario=scenario)

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "review_agent_trace"
    assert payload["agent"]["agent_source"] == "simulate"
    assert payload["agent"]["execution_id"] == str(execution.id)
    assert payload["agent"]["call_execution_id"] == str(call.id)
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/simulate/test/{run_test.id}/{execution.id}/"
        "call-details?from=onboarding"
    )


@pytest.mark.django_db
def test_reviewed_simulation_with_eval_config_activates(
    organization,
    workspace,
    user,
):
    agent = create_agent_definition(organization=organization, workspace=workspace)
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
    execution = create_test_execution(run_test=run_test)
    create_call_execution(test_execution=execution, scenario=scenario)
    create_simulate_eval_config(
        run_test=run_test,
        organization=organization,
        workspace=workspace,
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="agent_tool_call_reviewed",
        source="test",
        product_path="agent",
        activation_stage="review_agent_trace",
        metadata={"execution_id": str(execution.id)},
    )

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["agent"]["has_eval_coverage"] is True
    assert payload["agent"]["has_review"] is True


@pytest.mark.django_db
def test_sample_only_agent_events_do_not_activate(organization, workspace, user):
    now = timezone.now()
    for event_name in [
        "agent_created",
        "agent_prototype_run_completed",
        "agent_trace_reviewed",
        "agent_eval_created",
    ]:
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name=event_name,
            source="test",
            product_path="agent",
            activation_stage="create_agent",
            is_sample=True,
            occurred_at=now - timedelta(minutes=1),
        )

    payload = _agent_state(user, organization, workspace)

    assert payload["stage"] == "create_agent"
    assert payload["is_activated"] is False
    assert payload["agent"]["is_sample"] is True
    assert payload["agent"]["has_agent"] is False


@pytest.mark.django_db
def test_agent_path_flag_off_returns_selected_path_unavailable(
    organization,
    workspace,
    user,
):
    payload = _agent_state(
        user,
        organization,
        workspace,
        flags=_flags(onboarding_agent_path=False),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "selected_path_unavailable"
    assert payload["recommended_action"]["id"] == "choose_available_path"
