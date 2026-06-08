import pytest

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
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
        "onboarding_gateway_path": True,
        "onboarding_gateway_route_modes": True,
        "onboarding_eval_path": True,
        "onboarding_eval_route_modes": True,
        "onboarding_voice_path": True,
        "onboarding_voice_route_modes": True,
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
        "onboarding_email_gateway_enabled": False,
        "onboarding_email_gateway": False,
        "onboarding_email_eval": False,
        "onboarding_eval_notifications": False,
        "onboarding_email_voice": False,
        "onboarding_voice_notifications": False,
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
        selected_goal="connect_voice_ai_agent",
        primary_path="voice",
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


def _voice_state(user, organization, workspace, *, flags=None, signals=None):
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


def _voice_call_setup(
    *,
    organization,
    workspace,
    status="completed",
    call_metadata=None,
):
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
    execution_status = "failed" if status == "failed" else "completed"
    execution = create_test_execution(
        run_test=run_test,
        status=execution_status,
    )
    call = create_call_execution(
        test_execution=execution,
        scenario=scenario,
        status=status,
        simulation_call_type="voice",
        metadata=call_metadata,
        recording_available=True,
        response_time_ms=640,
        user_interruption_count=1,
        ai_interruption_count=0,
    )
    return agent, run_test, execution, call


def _record_voice_review(*, user, organization, workspace, call):
    return record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="voice_call_reviewed",
        source="test",
        product_path="voice",
        activation_stage="review_voice_call",
        metadata={"call_execution_id": str(call.id)},
    )


@pytest.mark.django_db
def test_voice_path_without_agent_returns_create_agent(organization, workspace, user):
    payload = _voice_state(
        user,
        organization,
        workspace,
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "create_voice_agent"
    assert payload["recommended_action"]["id"] == "create_voice_agent"
    assert payload["recommended_action"]["href"].startswith(
        "/dashboard/simulate/agent-definitions/create-new-agent-definition?"
        "source=onboarding&onboarding=create-voice-agent"
    )
    assert payload["voice"]["has_agent"] is False


@pytest.mark.django_db
def test_voice_agent_without_call_returns_run_test_call(organization, workspace, user):
    agent = create_agent_definition(
        organization=organization,
        workspace=workspace,
        agent_type="voice",
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "run_voice_test_call"
    assert payload["recommended_action"]["id"] == "run_voice_test_call"
    assert payload["voice"]["agent_id"] == str(agent.id)
    assert (
        "/dashboard/simulate/test?onboarding=create-test-call"
        in (payload["recommended_action"]["href"])
    )
    assert "agent_definition_id" in payload["recommended_action"]["href"]


@pytest.mark.django_db
def test_completed_voice_call_requires_review(organization, workspace, user):
    _, run_test, execution, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "review_voice_call"
    assert payload["is_activated"] is False
    assert payload["recommended_action"]["id"] == "review_voice_call"
    assert payload["voice"]["call_execution_id"] == str(call.id)
    assert payload["voice"]["has_completed_call"] is True
    expected_query = f"from=onboarding&onboarding=review-voice-call&call_id={call.id}"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/simulate/test/{run_test.id}/{execution.id}/"
        f"call-details?{expected_query}"
    )


@pytest.mark.django_db
def test_reviewed_voice_call_requires_success_criteria(
    organization,
    workspace,
    user,
):
    _, run_test, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
    )
    _record_voice_review(
        user=user,
        organization=organization,
        workspace=workspace,
        call=call,
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "add_voice_success_criteria"
    assert payload["recommended_action"]["id"] == "add_voice_success_criteria"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/simulate/test/{run_test.id}/runs?"
        f"onboarding=success-criteria&call_id={call.id}"
    )
    assert payload["voice"]["has_review"] is True
    assert payload["voice"]["has_success_criteria"] is False


@pytest.mark.django_db
def test_reviewed_call_with_success_criteria_activates(
    organization,
    workspace,
    user,
):
    _, run_test, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
    )
    _record_voice_review(
        user=user,
        organization=organization,
        workspace=workspace,
        call=call,
    )
    create_simulate_eval_config(
        run_test=run_test,
        organization=organization,
        workspace=workspace,
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True
    assert payload["recommended_action"]["id"] == "voice_monitor_calls"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/simulate/test/{run_test.id}/call-logs?onboarding=monitor-calls"
    )
    assert payload["voice"]["has_success_criteria"] is True
    assert payload["value_signal"] == {
        "kind": "voice_quality_loop",
        "headline": "Voice call reviewed",
        "summary": "1 call reviewed · 1 success check ready",
        "metrics": [
            {"label": "Calls reviewed", "value": "1"},
            {"label": "Success checks ready", "value": "1"},
            {"label": "Response time", "value": "640 ms"},
            {"label": "Interruptions caught", "value": "1"},
        ],
    }


@pytest.mark.django_db
def test_failed_voice_call_review_with_criteria_routes_to_rerun(
    organization,
    workspace,
    user,
):
    _, run_test, _, call = _voice_call_setup(
        organization=organization,
        workspace=workspace,
        status="failed",
    )
    _record_voice_review(
        user=user,
        organization=organization,
        workspace=workspace,
        call=call,
    )
    create_simulate_eval_config(
        run_test=run_test,
        organization=organization,
        workspace=workspace,
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "run_voice_test_call"
    assert payload["is_activated"] is False
    assert payload["voice"]["call_failed"] is True
    assert payload["voice"]["has_completed_call"] is False
    assert payload["recommended_action"]["href"].startswith(
        f"/dashboard/simulate/test/{run_test.id}/runs?onboarding=run-test-call"
    )


@pytest.mark.django_db
def test_sample_only_voice_events_do_not_activate(organization, workspace, user):
    for event_name in [
        "voice_agent_created",
        "voice_test_call_completed",
        "voice_call_reviewed",
        "voice_success_criteria_added",
    ]:
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name=event_name,
            source="test",
            product_path="voice",
            activation_stage="create_voice_agent",
            is_sample=True,
        )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "create_voice_agent"
    assert payload["is_activated"] is False
    assert payload["voice"]["is_sample"] is True
    assert payload["voice"]["has_call"] is False


@pytest.mark.django_db
def test_sample_voice_call_does_not_count_as_real_call(
    organization,
    workspace,
    user,
):
    agent, _, _, _ = _voice_call_setup(
        organization=organization,
        workspace=workspace,
        call_metadata={"is_sample": True},
    )

    payload = _voice_state(user, organization, workspace)

    assert payload["stage"] == "run_voice_test_call"
    assert payload["voice"]["agent_id"] == str(agent.id)
    assert payload["voice"]["has_agent"] is True
    assert payload["voice"]["has_call"] is False
    assert payload["voice"]["sample_call_count"] == 1


@pytest.mark.django_db
def test_voice_path_flag_off_returns_selected_path_unavailable(
    organization,
    workspace,
    user,
):
    payload = _voice_state(
        user,
        organization,
        workspace,
        flags=_flags(onboarding_voice_path=False),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "selected_path_unavailable"
    assert payload["recommended_action"]["id"] == "choose_available_path"
