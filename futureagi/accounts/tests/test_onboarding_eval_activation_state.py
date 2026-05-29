import pytest

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)
from accounts.tests.onboarding_model_factories import (
    create_eval_dataset,
    create_user_eval_metric,
)
from model_hub.models.choices import DatasetSourceChoices


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
        selected_goal="evaluate_quality",
        primary_path="evals",
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


def _eval_state(user, organization, workspace, *, flags=None, signals=None):
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


def _record_eval_run(
    *,
    user,
    organization,
    workspace,
    metric,
    failure_count=0,
):
    return record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="eval_run_completed",
        source="test",
        product_path="evals",
        activation_stage="run_eval",
        metadata={
            "run_id": "run-1",
            "eval_id": str(metric.template.id),
            "eval_template_id": str(metric.template.id),
            "scorer_id": str(metric.id),
            "failure_count": failure_count,
        },
    )


@pytest.mark.django_db
def test_eval_path_without_source_returns_create_dataset(
    organization,
    workspace,
    user,
):
    payload = _eval_state(
        user,
        organization,
        workspace,
        signals=OnboardingSignals(first_checks={}),
    )

    assert payload["stage"] == "create_eval_dataset"
    assert payload["recommended_action"]["id"] == "create_eval_dataset"
    assert payload["recommended_action"]["href"].endswith("step=data")
    assert payload["eval"]["has_source"] is False


@pytest.mark.django_db
def test_eval_source_without_scorer_returns_add_scorer(
    organization,
    workspace,
    user,
):
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "add_eval_scorer"
    assert payload["recommended_action"]["id"] == "add_eval_scorer"
    assert payload["eval"]["source_id"] == str(dataset.id)
    assert "step=scorer" in payload["recommended_action"]["href"]


@pytest.mark.django_db
def test_eval_scorer_without_run_returns_run_eval(organization, workspace, user):
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    metric = create_user_eval_metric(
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        user=user,
    )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "run_eval"
    assert payload["recommended_action"]["id"] == "run_eval"
    assert payload["eval"]["scorer_id"] == str(metric.id)
    assert str(metric.template.id) in payload["recommended_action"]["href"]
    assert "step=run" in payload["recommended_action"]["href"]


@pytest.mark.django_db
def test_eval_run_requires_failure_or_summary_review(
    organization,
    workspace,
    user,
):
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    metric = create_user_eval_metric(
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        user=user,
    )
    _record_eval_run(
        user=user,
        organization=organization,
        workspace=workspace,
        metric=metric,
        failure_count=2,
    )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "review_eval_failures"
    assert payload["is_activated"] is False
    assert payload["recommended_action"]["id"] == "review_eval_failures"
    assert payload["eval"]["has_completed_run"] is True
    assert payload["eval"]["has_review"] is False


@pytest.mark.django_db
def test_eval_review_prompts_source_fix_before_activation_for_failures(
    organization,
    workspace,
    user,
):
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    metric = create_user_eval_metric(
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        user=user,
    )
    _record_eval_run(
        user=user,
        organization=organization,
        workspace=workspace,
        metric=metric,
        failure_count=2,
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="eval_failures_reviewed",
        source="test",
        product_path="evals",
        activation_stage="review_eval_failures",
        metadata={
            "run_id": "run-1",
            "eval_id": str(metric.template.id),
            "eval_template_id": str(metric.template.id),
            "scorer_id": str(metric.id),
        },
    )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "eval_next_loop"
    assert payload["is_activated"] is False
    assert payload["recommended_action"]["id"] == "fix_eval_source"
    assert f"/dashboard/develop/{dataset.id}" in payload["recommended_action"]["href"]
    assert payload["eval"]["has_review"] is True
    assert payload["eval"]["has_failures"] is True


@pytest.mark.django_db
def test_eval_failure_action_switches_to_results_review(
    organization,
    workspace,
    user,
):
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    metric = create_user_eval_metric(
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        user=user,
    )
    _record_eval_run(
        user=user,
        organization=organization,
        workspace=workspace,
        metric=metric,
        failure_count=1,
    )
    for event_name, stage in [
        ("eval_failures_reviewed", "review_eval_failures"),
        ("eval_failure_action_created", "eval_next_loop"),
    ]:
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name=event_name,
            source="test",
            product_path="evals",
            activation_stage=stage,
            metadata={
                "run_id": "run-1",
                "eval_id": str(metric.template.id),
                "eval_template_id": str(metric.template.id),
                "scorer_id": str(metric.id),
            },
        )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["recommended_action"]["id"] == "open_eval_usage"
    assert payload["eval"]["has_failure_action"] is True


@pytest.mark.django_db
def test_sample_eval_source_does_not_count_as_real_activation(
    organization,
    workspace,
    user,
):
    create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.DEMO.value,
    )

    payload = _eval_state(user, organization, workspace)

    assert payload["stage"] == "create_eval_dataset"
    assert payload["is_activated"] is False
    assert payload["eval"]["has_source"] is False
    assert payload["eval"]["is_sample"] is True
    assert payload["eval"]["sample_source_count"] == 1


@pytest.mark.django_db
def test_eval_path_flag_off_returns_selected_path_unavailable(
    organization,
    workspace,
    user,
):
    payload = _eval_state(
        user,
        organization,
        workspace,
        flags=_flags(onboarding_eval_path=False),
    )

    assert payload["stage"] == "selected_path_unavailable"
    assert payload["recommended_action"]["id"] == "choose_available_path"
