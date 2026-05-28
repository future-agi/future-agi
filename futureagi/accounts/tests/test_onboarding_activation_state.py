import pytest

from accounts.models import OnboardingLifecycleEvaluationLog
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.flow_config import (
    configured_default_goal_id,
    configured_goal_primary_paths,
    configured_stage,
)
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import (
    create_custom_eval,
    create_observe_project,
    create_trace,
)


def _flags(**overrides):
    flags = {
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
        "onboarding_sample_project": False,
        "onboarding_daily_quality_home": False,
        "onboarding_lifecycle_email_dry_run": False,
        "onboarding_email_welcome_enabled": False,
        "onboarding_email_first_action_recovery_enabled": False,
        "onboarding_email_first_signal_enabled": False,
        "onboarding_email_next_loop_enabled": False,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
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


def _context(
    user, organization, workspace, *, goal="monitor_production_ai_app", can_write=True
):
    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role="Owner" if can_write else "Viewer",
        workspace_role="workspace_admin" if can_write else "workspace_viewer",
        organization_level=15 if can_write else 1,
        workspace_level=8 if can_write else 1,
        selected_goal=goal,
        primary_path="observe" if goal == "monitor_production_ai_app" else None,
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


@pytest.mark.django_db
def test_flag_off_returns_feature_disabled(organization, workspace, user):
    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(
            onboarding_activation_state_api=False, onboarding_home_enabled=False
        ),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "feature_disabled"
    assert payload["recommended_action"]["id"] == "open_get_started"


@pytest.mark.django_db
def test_missing_workspace_returns_workspace_missing(organization, user):
    payload = resolve_activation_state(
        context=_context(user, organization, None),
        flags=_flags(),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "workspace_missing"


@pytest.mark.django_db
def test_no_goal_returns_choose_goal(organization, workspace, user):
    payload = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=_flags(),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "choose_goal"
    assert payload["fallback_action"]["id"] == "open_observe_setup_fallback"
    assert payload["fallback_action"]["href"] == (
        "/dashboard/observe?setup=true&source=onboarding"
    )


@pytest.mark.django_db
def test_observe_path_no_setup_returns_connect_observability(
    organization,
    workspace,
    user,
):
    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "connect_observability"
    assert payload["stage_copy"]["title"] == "Connect observability"
    assert payload["available_goals"][0]["goal"] == "monitor_production_ai_app"
    assert payload["recommended_action"]["id"] == "create_observe_project"
    assert payload["fallback_action"]["id"] == "open_observe_setup_fallback"
    assert payload["fallback_action"]["href"] == (
        "/dashboard/observe?setup=true&source=onboarding"
    )


def test_activation_flow_config_drives_goal_and_stage_wiring():
    assert configured_default_goal_id() == "monitor_production_ai_app"
    assert configured_goal_primary_paths()["monitor_production_ai_app"] == "observe"
    assert configured_stage("connect_observability")["recommended_action"] == (
        "create_observe_project"
    )


@pytest.mark.django_db
def test_observe_project_without_trace_waits_for_trace(organization, workspace, user):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=signals,
    )

    assert payload["stage"] == "waiting_for_first_trace"
    assert payload["recommended_action"]["id"] == "send_first_trace"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/observe/{project.id}/llm-tracing"
    )
    assert payload["fallback_action"]["id"] == "open_observe_dashboard_fallback"
    assert payload["fallback_action"]["href"] == f"/dashboard/observe/{project.id}"


@pytest.mark.django_db
def test_observe_project_waiting_trace_uses_route_focus_when_enabled(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(onboarding_observe_route_modes=True),
        signals=signals,
    )

    assert payload["stage"] == "waiting_for_first_trace"
    assert payload["recommended_action"]["id"] == "send_first_trace"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/observe/{project.id}/llm-tracing?"
        "source=onboarding&onboarding=send-first-trace"
    )


@pytest.mark.django_db
def test_sample_flag_adds_sample_waiting_stage(organization, workspace, user):
    create_observe_project(organization=organization, workspace=workspace, user=user)
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(
            onboarding_sample_project=True,
            onboarding_sample_project_enabled=True,
        ),
        signals=signals,
    )

    assert payload["stage"] == "waiting_for_first_trace_sample_available"
    assert payload["fallback_action"]["id"] == "open_sample_trace"


@pytest.mark.django_db
def test_trace_without_review_returns_review_first_trace(organization, workspace, user):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=signals,
    )

    assert payload["stage"] == "review_first_trace"
    assert payload["fallback_action"]["id"] == "open_observe_dashboard_fallback"
    assert payload["fallback_action"]["href"] == f"/dashboard/observe/{project.id}"


@pytest.mark.django_db
def test_trace_review_without_improvement_returns_create_evaluator(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=signals,
    )

    assert payload["stage"] == "create_trace_evaluator"
    assert payload["recommended_action"]["id"] == "create_trace_evaluator"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/observe/{project.id}/llm-tracing"
    )
    assert payload["fallback_action"]["id"] == "open_observe_dashboard_fallback"
    assert payload["fallback_action"]["href"] == f"/dashboard/observe/{project.id}"


@pytest.mark.django_db
def test_create_evaluator_uses_route_focus_when_enabled(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(onboarding_observe_route_modes=True),
        signals=signals,
    )

    assert payload["stage"] == "create_trace_evaluator"
    assert payload["recommended_action"]["id"] == "create_trace_evaluator"
    assert payload["recommended_action"]["href"] == (
        f"/dashboard/observe/{project.id}/llm-tracing?"
        "source=onboarding&onboarding=create-evaluator"
    )


@pytest.mark.django_db
def test_evaluator_after_trace_review_activates(organization, workspace, user):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    create_custom_eval(organization=organization, workspace=workspace, project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=signals,
    )

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True
    assert payload["stage_copy"] == {
        "eyebrow": "Aha moment reached",
        "title": "Your first quality loop is live",
        "description": (
            "Review daily quality next and keep improving the loop from real signals."
        ),
    }


@pytest.mark.django_db
def test_eval_loop_on_observe_project_after_trace_review_activates(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        source="eval_review_onboarding",
        product_path="evals",
        metadata={
            "source_id": str(project.id),
            "source_type": "trace_project",
            "run_id": "run-1",
        },
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(),
        signals=signals,
    )

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True


@pytest.mark.django_db
def test_daily_flag_moves_activated_workspace_to_daily_review(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_trace(project=project)
    create_custom_eval(organization=organization, workspace=workspace, project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    payload = resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=_flags(
            onboarding_daily_quality_home=True,
            daily_quality_home_enabled=True,
        ),
        signals=signals,
    )

    assert payload["stage"] == "daily_review"


@pytest.mark.django_db
def test_permission_limited_user_does_not_receive_write_action(
    organization,
    workspace,
    user,
):
    payload = resolve_activation_state(
        context=_context(user, organization, workspace, can_write=False),
        flags=_flags(),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "permission_limited"
    assert payload["recommended_action"]["kind"] == "request_access"
    assert payload["fallback_action"]["id"] == "open_observe_dashboard_fallback"


@pytest.mark.django_db
def test_unavailable_goal_returns_selected_path_unavailable(
    organization,
    workspace,
    user,
):
    context = _context(user, organization, workspace, goal="improve_prompts")
    context = OnboardingContext(
        **{
            **context.__dict__,
            "primary_path": "prompt",
        }
    )

    payload = resolve_activation_state(
        context=context,
        flags=_flags(),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "selected_path_unavailable"
    assert payload["fallback_action"]["id"] == "open_observe_setup_fallback"


@pytest.mark.django_db
def test_activation_state_includes_lifecycle_preview_without_writing_logs(
    organization,
    workspace,
    user,
):
    payload = resolve_activation_state(
        context=_context(user, organization, workspace, goal=None),
        flags=_flags(
            onboarding_lifecycle_email_dry_run=True,
            onboarding_lifecycle_dry_run_enabled=True,
            onboarding_email_welcome_enabled=True,
        ),
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["lifecycle"]["dry_run_enabled"] is True
    assert payload["lifecycle"]["send_enabled"] is False
    assert payload["lifecycle"]["next_campaign_key"] == "welcome_choose_goal"
    assert payload["email_eligibility"]["dry_run_only"] is True
    assert not OnboardingLifecycleEvaluationLog.no_workspace_objects.exists()
