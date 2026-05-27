import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models import OnboardingActivationEvent, OnboardingGoal
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.goals import save_onboarding_goal


@pytest.mark.django_db
def test_activation_state_requires_auth(api_client):
    response = api_client.get("/accounts/activation-state/")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={})
def test_activation_state_flag_off_returns_renderable_payload(auth_client):
    response = auth_client.get("/accounts/activation-state/")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["stage"] == "feature_disabled"
    assert payload["recommended_action"]["id"] == "open_get_started"


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
    }
)
def test_activation_state_flag_on_returns_full_shape(auth_client, user):
    user.goals = ["monitor_production_ai_app"]
    user.role = "developer"
    user.save(update_fields=["goals", "role"])

    response = auth_client.get("/accounts/activation-state/")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["stage"] == "connect_observability"
    for key in [
        "schema_version",
        "workspace_id",
        "organization_id",
        "recommended_action",
        "fallback_action",
        "progress",
        "signals",
        "available_paths",
        "sample_project",
        "email_eligibility",
        "permissions",
        "feature_flags",
        "route_availability",
        "warnings",
    ]:
        assert key in payload


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_state_unknown_query_param_does_not_crash(auth_client, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.get("/accounts/activation-state/?unexpected=value")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["stage"] == "connect_observability"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_state_stale_email_query_reflects_current_state(auth_client, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.get(
        "/accounts/activation-state/?target_stage=activated&target_event=first_quality_loop_completed"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["stage"] == "connect_observability"


@pytest.mark.django_db
def test_onboarding_goal_requires_auth(api_client):
    response = api_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "monitor_production_ai_app"},
        format="json",
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_onboarding_goal_first_save_returns_updated_activation_state(
    auth_client,
    workspace,
):
    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "monitor_production_ai_app"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["goal"] == "monitor_production_ai_app"
    assert payload["primary_path"] == "observe"
    assert payload["stage"] == "connect_observability"
    assert OnboardingGoal.no_workspace_objects.filter(
        workspace=workspace,
        is_active=True,
    ).exists()
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        event_name="onboarding_goal_selected",
        workspace=workspace,
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_onboarding_goal_change_returns_new_path(
    auth_client, organization, workspace, user
):
    save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "improve_prompts", "reason": "path_change"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["goal"] == "improve_prompts"
    assert payload["primary_path"] == "prompt"
    assert payload["stage"] == "selected_path_unavailable"
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        event_name="onboarding_goal_changed",
        workspace=workspace,
    ).exists()


@pytest.mark.django_db
def test_onboarding_goal_invalid_goal_does_not_replace_active_goal(
    auth_client,
    organization,
    workspace,
    user,
):
    active = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    ).goal

    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "unknown_goal"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingGoal.no_workspace_objects.get(is_active=True).id == active.id


@pytest.mark.django_db
def test_onboarding_goal_invalid_path_does_not_replace_active_goal(
    auth_client,
    organization,
    workspace,
    user,
):
    active = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    ).goal

    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "monitor_production_ai_app", "primary_path": "prompt"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingGoal.no_workspace_objects.get(is_active=True).id == active.id


@pytest.mark.django_db
def test_onboarding_goal_stale_known_goal_returns_conflict(
    auth_client,
    organization,
    workspace,
    user,
):
    active = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    ).goal

    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {
            "goal": "improve_prompts",
            "known_goal_id": "00000000-0000-0000-0000-000000000000",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    result = response.json()["result"]
    assert result["reason"] == "known_goal_mismatch"
    assert result["current_goal_id"] == str(active.id)
    assert OnboardingGoal.no_workspace_objects.get(is_active=True).id == active.id


@pytest.mark.django_db
def test_onboarding_goal_stale_expected_stage_returns_conflict(
    auth_client,
    organization,
    workspace,
    user,
):
    active = save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    ).goal
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        source="activation_resolver",
        product_path="observe",
    )

    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {
            "goal": "improve_prompts",
            "known_goal_id": str(active.id),
            "expected_stage": "connect_observability",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["result"]["reason"] == "stage_changed"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={})
def test_onboarding_goal_save_succeeds_when_activation_api_flag_off(auth_client):
    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": "monitor_production_ai_app"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["goal"] == "monitor_production_ai_app"
    assert payload["stage"] == "feature_disabled"
