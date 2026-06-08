import uuid

import pytest
from django.core.exceptions import ValidationError

from accounts.models import OnboardingActivationEvent, OnboardingGoal
from accounts.models.workspace import Workspace
from accounts.services.onboarding.goals import (
    OnboardingGoalConflict,
    get_active_goal,
    goal_to_primary_path,
    normalize_goal,
    normalize_primary_path,
    resolve_goal_for_context,
    save_onboarding_goal,
)


@pytest.mark.django_db
def test_normalize_canonical_goal():
    assert normalize_goal("monitor_production_ai_app") == "monitor_production_ai_app"


@pytest.mark.django_db
def test_normalize_accepted_alias():
    assert normalize_goal("test_and_improve_prompts") == "improve_prompts"


@pytest.mark.django_db
def test_normalize_legacy_setup_goal_label():
    assert normalize_goal("Monitor LLMs and Agents") == "monitor_production_ai_app"


@pytest.mark.django_db
def test_normalize_sample_preview_setup_goal_label():
    assert normalize_goal("Explore with sample data") == "explore_sample_data"
    assert normalize_goal("Preview sample trace") == "explore_sample_data"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("goal_label", "canonical_goal"),
    [
        ("Monitor a production AI app", "monitor_production_ai_app"),
        ("Connect your agent", "monitor_production_ai_app"),
        ("Test and improve prompts", "improve_prompts"),
        ("Test prompts or agent prompts", "improve_prompts"),
        ("Build or prototype an AI agent", "build_ai_agent"),
        ("Prototype agent", "build_ai_agent"),
        ("Route LLM traffic safely", "control_model_traffic"),
        ("Set up gateway", "control_model_traffic"),
        ("Evaluate quality on data or traces", "evaluate_quality"),
        ("Test AI using simulation", "evaluate_quality"),
        ("Connect a voice AI agent", "connect_voice_ai_agent"),
    ],
)
def test_normalize_product_loop_setup_goal_labels(goal_label, canonical_goal):
    assert normalize_goal(goal_label) == canonical_goal


@pytest.mark.django_db
def test_unknown_goal_rejected():
    with pytest.raises(ValidationError):
        normalize_goal("unknown_goal")


@pytest.mark.django_db
def test_primary_path_normalizes_alias():
    assert normalize_primary_path("observability") == "observe"


@pytest.mark.django_db
def test_goal_to_primary_path():
    assert goal_to_primary_path("monitor_production_ai_app") == "observe"


@pytest.mark.django_db
def test_path_mismatch_rejected_without_persisting(organization, workspace, user):
    with pytest.raises(ValidationError):
        save_onboarding_goal(
            user=user,
            organization=organization,
            workspace=workspace,
            goal="monitor_production_ai_app",
            primary_path="prompt",
        )

    assert get_active_goal(organization=organization, workspace=workspace) is None


@pytest.mark.django_db
def test_first_goal_save_creates_active_goal_and_event(organization, workspace, user):
    result = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        source="goal_picker",
    )

    assert result.created is True
    assert result.changed is False
    assert result.goal.is_active is True
    assert result.goal.primary_path == "observe"
    assert result.event_name == "onboarding_goal_selected"
    assert OnboardingGoal.no_workspace_objects.count() == 1
    event = OnboardingActivationEvent.no_workspace_objects.get()
    assert event.event_name == "onboarding_goal_selected"
    assert event.product_path == "observe"
    assert event.metadata["new_goal"] == "monitor_production_ai_app"


@pytest.mark.django_db
def test_repeated_same_goal_returns_existing_without_duplicate_event(
    organization,
    workspace,
    user,
):
    first = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )
    second = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    assert second.created is False
    assert second.changed is False
    assert second.goal.id == first.goal.id
    assert OnboardingGoal.no_workspace_objects.count() == 1
    assert OnboardingActivationEvent.no_workspace_objects.count() == 1


@pytest.mark.django_db
def test_goal_change_deactivates_previous_goal_and_records_event(
    organization,
    workspace,
    user,
):
    first = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )
    second = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="improve_prompts",
        reason="path_change",
    )

    first.goal.refresh_from_db()
    assert first.goal.is_active is False
    assert second.goal.is_active is True
    assert second.goal.primary_path == "prompt"
    assert second.event_name == "onboarding_goal_changed"
    assert OnboardingGoal.no_workspace_objects.filter(is_active=True).count() == 1
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        event_name="onboarding_goal_changed"
    ).exists()


@pytest.mark.django_db
def test_workspace_mismatch_rejected(organization, workspace, user):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Goal Mismatch Workspace",
        organization=organization,
        created_by=user,
    )
    other_org = type(organization).objects.create(name="Goal Other Org")

    with pytest.raises(ValidationError):
        save_onboarding_goal(
            user=user,
            organization=other_org,
            workspace=other_workspace,
            goal="monitor_production_ai_app",
        )


@pytest.mark.django_db
def test_workspace_isolation(organization, workspace, user):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Goal Other Workspace",
        organization=organization,
        created_by=user,
    )
    save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    assert get_active_goal(organization=organization, workspace=other_workspace) is None


@pytest.mark.django_db
def test_legacy_fallback_read_maps_unambiguous_goal(organization, workspace, user):
    user.goals = ["test_and_improve_prompts"]
    user.save(update_fields=["goals"])

    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert goal_context["goal"] == "improve_prompts"
    assert goal_context["primary_path"] == "prompt"
    assert goal_context["source"] == "legacy_user_goals"


@pytest.mark.django_db
def test_legacy_fallback_uses_first_supported_goal(organization, workspace, user):
    user.goals = ["test_and_improve_prompts", "monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert goal_context["goal"] == "improve_prompts"
    assert goal_context["primary_path"] == "prompt"


@pytest.mark.django_db
def test_legacy_setup_goals_can_skip_second_goal_picker(
    organization,
    workspace,
    user,
):
    user.goals = ["Monitor LLMs and Agents", "Run Evaluations"]
    user.save(update_fields=["goals"])

    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert goal_context["goal"] == "monitor_production_ai_app"
    assert goal_context["primary_path"] == "observe"


@pytest.mark.django_db
def test_no_explicit_goal_uses_configured_default_first_run_goal(
    organization,
    workspace,
    user,
):
    user.goals = []
    user.config = {}
    user.save(update_fields=["goals", "config"])

    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert goal_context["goal"] == "monitor_production_ai_app"
    assert goal_context["primary_path"] == "observe"
    assert goal_context["goal_id"] is None
    assert goal_context["source"] == "default_first_run_goal"


@pytest.mark.django_db
def test_default_first_run_goal_waits_for_workspace_context(organization, user):
    user.goals = []
    user.config = {}
    user.save(update_fields=["goals", "config"])

    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=None,
    )

    assert goal_context["goal"] is None
    assert goal_context["primary_path"] is None
    assert goal_context["source"] == "none"


@pytest.mark.django_db
def test_stale_known_goal_rejected(organization, workspace, user):
    save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    with pytest.raises(OnboardingGoalConflict):
        save_onboarding_goal(
            user=user,
            organization=organization,
            workspace=workspace,
            goal="improve_prompts",
            known_goal_id=str(uuid.uuid4()),
        )
