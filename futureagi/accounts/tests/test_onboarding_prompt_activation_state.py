import pytest

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import (
    create_observe_project,
    create_prompt_eval_config,
    create_prompt_template,
    create_prompt_version,
    create_trace,
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
        "onboarding_lifecycle_email_dry_run": False,
        "onboarding_email_welcome_enabled": False,
        "onboarding_email_first_action_recovery_enabled": False,
        "onboarding_email_first_signal_enabled": False,
        "onboarding_email_next_loop_enabled": False,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
        "onboarding_email_prompt_enabled": False,
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
        selected_goal="improve_prompts",
        primary_path="prompt",
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


def _prompt_state(user, organization, workspace, *, flags=None, signals=None):
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
def test_prompt_path_without_template_returns_create_prompt(
    organization,
    workspace,
    user,
):
    payload = _prompt_state(
        user,
        organization,
        workspace,
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "start_prompt"
    assert payload["recommended_action"]["id"] == "create_prompt"
    assert payload["recommended_action"]["href"].endswith("action=create-prompt")
    assert payload["prompt"]["has_real_prompt"] is False


@pytest.mark.django_db
def test_prompt_template_without_run_returns_run_test(organization, workspace, user):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(template=template, output=[])

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "run_prompt_test"
    assert payload["recommended_action"]["id"] == "run_prompt_test"
    assert payload["prompt"]["prompt_id"] == str(template.id)
    assert "onboarding=run-test" in payload["recommended_action"]["href"]


@pytest.mark.django_db
def test_prompt_run_without_committed_version_returns_save_baseline(
    organization,
    workspace,
    user,
):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(template=template, output=["hello"])

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "save_prompt_version"
    assert payload["recommended_action"]["id"] == "save_prompt_version"


@pytest.mark.django_db
def test_single_prompt_version_without_comparison_returns_second_version_bridge(
    organization,
    workspace,
    user,
):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["hello"],
    )

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "create_second_prompt_version"
    assert payload["recommended_action"]["id"] == "create_second_prompt_version"
    assert "onboarding=compare" in payload["recommended_action"]["href"]
    assert payload["prompt"]["has_committed_version"] is True
    assert payload["prompt"]["has_comparable_versions"] is False


@pytest.mark.django_db
def test_comparable_prompt_versions_without_comparison_returns_compare_versions(
    organization,
    workspace,
    user,
):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["hello"],
    )
    create_prompt_version(
        template=template,
        version="v2",
        is_draft=False,
        commit_message="Safer answer",
        output=["hello again"],
    )

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "compare_prompt_versions"
    assert payload["recommended_action"]["id"] == "compare_prompt_versions"
    assert payload["prompt"]["has_comparable_versions"] is True
    assert payload["signals"]["prompt_comparable_versions_exist"] is True


@pytest.mark.django_db
def test_prompt_comparison_without_next_loop_returns_prompt_next_loop(
    organization,
    workspace,
    user,
):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["hello"],
    )
    create_prompt_version(
        template=template,
        version="v2",
        is_draft=False,
        commit_message="Safer answer",
        output=["hello again"],
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="prompt_comparison_completed",
        source="test",
        product_path="prompt",
        activation_stage="compare_prompt_versions",
    )

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "prompt_next_loop"
    assert payload["recommended_action"]["id"] == "add_prompt_failure_example"


@pytest.mark.django_db
def test_prompt_first_loop_activates_prompt_path(organization, workspace, user):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["hello"],
    )
    create_prompt_version(
        template=template,
        version="v2",
        is_draft=False,
        commit_message="Safer answer",
        output=["hello again"],
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="prompt_comparison_completed",
        source="test",
        product_path="prompt",
        activation_stage="compare_prompt_versions",
    )
    create_prompt_eval_config(
        organization=organization,
        workspace=workspace,
        template=template,
    )

    payload = _prompt_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True
    assert payload["recommended_action"]["id"] == "open_prompt_metrics"
    assert payload["fallback_action"]["id"] == "open_prompt_workbench"
    assert payload["prompt"]["has_next_loop_action"] is True
    assert payload["signals"]["prompt_first_loop_completed"] is True
    assert payload["signals"]["prompt_run_exists"] is True
    assert payload["value_signal"] == {
        "kind": "prompt_quality_loop",
        "headline": "Prompt comparison complete",
        "summary": "2 versions compared · 1 quality check ready",
        "metrics": [
            {
                "label": "Versions compared",
                "value": "2",
            },
            {
                "label": "Quality checks ready",
                "value": "1",
            },
        ],
    }


@pytest.mark.django_db
def test_observe_activation_does_not_activate_prompt_path(
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
        source="test",
        product_path="observe",
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    signals = OnboardingSignals(
        **{
            **signals.__dict__,
            "first_loop_completed": True,
        }
    )

    payload = _prompt_state(user, organization, workspace, signals=signals)

    assert payload["stage"] == "start_prompt"
    assert payload["is_activated"] is False
