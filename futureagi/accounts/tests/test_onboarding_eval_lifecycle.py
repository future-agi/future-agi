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
    create_eval_dataset,
    create_user_eval_metric,
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
        "onboarding_email_eval": True,
        "onboarding_eval_notifications": True,
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
        selected_goal="evaluate_quality",
        primary_path="evals",
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


def _select_eval_goal(user, organization, workspace, selected_at):
    return OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="evaluate_quality",
        primary_path="evals",
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


def _record_eval_run(*, user, organization, workspace, metric, occurred_at):
    return record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="eval_run_completed",
        source="test",
        product_path="evals",
        activation_stage="run_eval",
        occurred_at=occurred_at,
        metadata={
            "run_id": "run-1",
            "eval_id": str(metric.template.id),
            "eval_template_id": str(metric.template.id),
            "scorer_id": str(metric.id),
            "failure_count": 1,
        },
    )


@pytest.mark.django_db
def test_eval_source_campaign_is_eligible_after_wait(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_key"] == "eval_create_source"
    assert "campaign_key=eval_create_source" in decision.target_url


@pytest.mark.django_db
def test_eval_scorer_campaign_is_eligible_after_source_created(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(days=1))
    dataset = create_eval_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    type(dataset).no_workspace_objects.filter(id=dataset.id).update(
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
    assert decision.campaign["campaign_key"] == "eval_add_scorer"
    assert "step=scorer" in decision.target_url


@pytest.mark.django_db
def test_eval_run_campaign_is_eligible_after_scorer_created(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(days=1))
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
    type(metric).no_workspace_objects.filter(id=metric.id).update(
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
    assert decision.campaign["campaign_key"] == "eval_run_first"
    assert "step=run" in decision.target_url


@pytest.mark.django_db
def test_eval_review_campaign_is_eligible_after_run_completed(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(days=1))
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
        occurred_at=now - timedelta(hours=1),
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
    assert decision.campaign["campaign_key"] == "eval_review_failures"
    assert "campaign_key=eval_review_failures" in decision.target_url


@pytest.mark.django_db
def test_eval_fix_source_campaign_is_eligible_after_review(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(days=3))
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
        occurred_at=now - timedelta(days=2),
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="eval_failures_reviewed",
        source="test",
        product_path="evals",
        activation_stage="review_eval_failures",
        occurred_at=now - timedelta(days=2),
        metadata={"run_id": "run-1", "eval_id": str(metric.template.id)},
    )
    flags = _flags(onboarding_daily_quality_home=True)
    activation_state = _activation_state(user, organization, workspace, flags=flags)

    decision = evaluate_lifecycle_decision(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
    )

    assert activation_state["stage"] == "eval_next_loop"
    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert decision.campaign["campaign_key"] == "eval_fix_source"
    assert "step=fix-eval-failure" in decision.target_url


@pytest.mark.django_db
def test_eval_completed_target_event_suppresses_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(hours=2))
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="eval_dataset_created",
        source="test",
        product_path="evals",
        activation_stage="create_eval_dataset",
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
        campaign_key="eval_create_source",
    )

    assert decision.status == OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
    assert decision.suppression_reason == "target_event_complete"


@pytest.mark.django_db
def test_eval_preference_toggle_suppresses_eval_campaign(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    _select_eval_goal(user, organization, workspace, now - timedelta(hours=2))
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
    assert decision.campaign["campaign_group"] == "eval"
    assert decision.suppression_reason == "user_unsubscribed"
