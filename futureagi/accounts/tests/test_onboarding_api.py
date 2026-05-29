import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models import OnboardingActivationEvent, OnboardingGoal
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.goals import save_onboarding_goal


@pytest.mark.django_db
def test_activation_state_requires_auth(api_client):
    response = api_client.get("/accounts/activation-state/")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": False})
def test_activation_state_flag_off_returns_renderable_payload(auth_client):
    response = auth_client.get("/accounts/activation-state/")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["stage"] == "feature_disabled"
    assert payload["recommended_action"]["id"] == "open_get_started"


@pytest.mark.django_db
@override_settings(CLOUD_DEPLOYMENT="")
def test_self_host_defaults_enable_core_onboarding_flags(
    organization,
    workspace,
    user,
):
    flags = get_onboarding_flags(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert flags["onboarding_activation_state_api"] is True
    assert flags["onboarding_goal_picker"] is True
    assert flags["onboarding_path_cards"] is True
    assert flags["onboarding_sample_project"] is True
    assert flags["onboarding_prompt_path"] is True
    assert flags["onboarding_prompt_route_modes"] is True
    assert flags["onboarding_agent_path"] is True
    assert flags["onboarding_agent_route_modes"] is True
    assert flags["onboarding_gateway_path"] is True
    assert flags["onboarding_gateway_route_modes"] is True
    assert flags["onboarding_eval_path"] is True
    assert flags["onboarding_eval_route_modes"] is True
    assert flags["onboarding_voice_path"] is True
    assert flags["onboarding_voice_route_modes"] is True
    assert flags["onboarding_lifecycle_send_enabled"] is False


@pytest.mark.django_db
@override_settings(CLOUD_DEPLOYMENT="US")
def test_cloud_defaults_keep_core_first_run_onboarding_enabled(
    monkeypatch,
    organization,
    workspace,
    user,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.feature_flags.posthog_tracker.get_feature_flags",
        lambda *args, **kwargs: {},
    )

    flags = get_onboarding_flags(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert flags["onboarding_activation_state_api"] is True
    assert flags["onboarding_goal_picker"] is True
    assert flags["onboarding_path_cards"] is True
    assert flags["onboarding_observe_route_modes"] is True
    assert flags["onboarding_prompt_path"] is True
    assert flags["onboarding_agent_path"] is True
    assert flags["onboarding_gateway_path"] is True
    assert flags["onboarding_eval_path"] is True
    assert flags["onboarding_voice_path"] is True
    assert flags["onboarding_lifecycle_send_enabled"] is False


@pytest.mark.django_db
@override_settings(CLOUD_DEPLOYMENT="US")
def test_cloud_flags_fetch_optional_onboarding_flags_in_one_batch(
    monkeypatch,
    organization,
    workspace,
    user,
):
    captured = {}

    def fake_get_feature_flags(flag_names, user_id, groups=None):
        captured["flag_names"] = tuple(flag_names)
        captured["user_id"] = str(user_id)
        captured["groups"] = groups
        return {
            "onboarding_email_prompt_enabled": True,
        }

    monkeypatch.setattr(
        "accounts.services.onboarding.feature_flags.posthog_tracker.get_feature_flags",
        fake_get_feature_flags,
    )

    flags = get_onboarding_flags(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert flags["onboarding_activation_state_api"] is True
    assert flags["onboarding_prompt_path"] is True
    assert flags["onboarding_email_prompt_enabled"] is True
    assert "onboarding_activation_state_api" not in captured["flag_names"]
    assert "onboarding_sample_project" not in captured["flag_names"]
    assert "onboarding_prompt_path" not in captured["flag_names"]
    assert "onboarding_agent_path" not in captured["flag_names"]
    assert "onboarding_gateway_path" not in captured["flag_names"]
    assert "onboarding_eval_path" not in captured["flag_names"]
    assert "onboarding_voice_path" not in captured["flag_names"]
    assert "onboarding_email_prompt_enabled" in captured["flag_names"]
    assert captured["user_id"] == str(user.id)
    assert captured["groups"] == {
        "organization": str(organization.id),
        "workspace": str(workspace.id),
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("goal", "expected_stage"),
    [
        ("monitor_production_ai_app", "connect_observability"),
        ("improve_prompts", "start_prompt"),
        ("build_ai_agent", "create_agent"),
        ("control_model_traffic", "configure_gateway_provider"),
        ("evaluate_quality", "create_eval_dataset"),
        ("connect_voice_ai_agent", "create_voice_agent"),
        ("explore_sample_data", "open_sample_project"),
    ],
)
@override_settings(CLOUD_DEPLOYMENT="")
def test_self_host_goal_save_routes_each_product_loop_to_guidance(
    auth_client,
    goal,
    expected_stage,
):
    response = auth_client.post(
        "/accounts/onboarding/goal/",
        {"goal": goal, "reason": "first_selection"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["goal"] == goal
    assert payload["stage"] == expected_stage
    assert payload["stage"] != "selected_path_unavailable"


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
        "lifecycle",
        "email_eligibility",
        "permissions",
        "feature_flags",
        "route_availability",
        "warnings",
    ]:
        assert key in payload


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
    }
)
def test_activation_state_defaults_no_goal_users_to_observe_aha(
    auth_client,
    workspace,
    user,
):
    user.goals = []
    user.config = {}
    user.role = "developer"
    user.save(update_fields=["goals", "config", "role"])

    response = auth_client.get("/accounts/activation-state/?source=setup_org")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["goal"] == "monitor_production_ai_app"
    assert payload["primary_path"] == "observe"
    assert payload["stage"] == "connect_observability"
    assert payload["recommended_action"]["id"] == "create_observe_project"
    assert payload["recommended_action"]["href"] == (
        "/dashboard/observe?setup=true&source=onboarding"
    )
    assert not OnboardingGoal.no_workspace_objects.filter(
        workspace=workspace,
        is_active=True,
    ).exists()


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
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_state_returns_lifecycle_email_context(auth_client, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.get(
        "/accounts/activation-state/"
        "?source=onboarding_email"
        "&campaign_key=observe_waiting_for_first_trace"
        "&email_key=observe_waiting_for_first_trace_v1"
        "&send_log_id=send-123"
        "&email_status=stale"
        "&target_stage=activated"
        "&target_event=first_quality_loop_completed"
        "&target_route=/dashboard/observe/observe-1"
        "&link_issued_at=2026-05-26T15:00:00Z"
        "&stale_reason=target_complete"
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["stage"] == "connect_observability"
    assert payload["email_context"] == {
        "campaign_key": "observe_waiting_for_first_trace",
        "email_key": "observe_waiting_for_first_trace_v1",
        "send_log_id": "send-123",
        "email_status": "stale",
        "link_issued_at": "2026-05-26T15:00:00Z",
        "target_stage": "activated",
        "target_event": "first_quality_loop_completed",
        "target_route": "/dashboard/observe/observe-1",
        "context_status": "stale",
        "stale_reason": "target_complete",
        "resolved_href": "/dashboard/observe?setup=true&source=onboarding",
    }
    assert "email_context_stale" in payload["warnings"]


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_state_does_not_echo_unsafe_email_target_route(auth_client, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.get(
        "/accounts/activation-state/"
        "?source=onboarding_email"
        "&campaign_key=observe_waiting_for_first_trace"
        "&email_key=observe_waiting_for_first_trace_v1"
        "&send_log_id=send-123"
        "&email_status=current"
        "&target_stage=connect_observability"
        "&target_event=observe_project_created"
        "&target_route=https://example.invalid/unsafe"
    )

    assert response.status_code == status.HTTP_200_OK
    email_context = response.json()["result"]["email_context"]
    assert email_context["target_route"] is None
    assert email_context["context_status"] == "route_unavailable"
    assert email_context["stale_reason"] == "route_unavailable"
    assert email_context["resolved_href"].startswith("/dashboard/")


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
    assert payload["stage"] == "start_prompt"
    assert payload["recommended_action"]["id"] == "create_prompt"
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
        allow_observe_loop_completion=True,
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
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": False})
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


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_event_persists_lifecycle_email_attribution(
    auth_client,
    workspace,
):
    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "daily_quality_item_reviewed",
            "primary_path": "observe",
            "stage": "daily_review",
            "source": "daily_quality_home",
            "artifact_type": "trace",
            "artifact_id": "trace-1",
            "project_id": "project-1",
            "campaign_key": "daily_quality_open_actions",
            "email_key": "daily_quality_open_actions_v1",
            "send_log_id": "00000000-0000-0000-0000-000000000321",
            "email_status": "current",
            "target_stage": "daily_review",
            "target_event": "daily_quality_item_reviewed",
            "link_issued_at": "2026-05-29T08:00:00Z",
            "context_status": "current",
            "metadata": {"source_id": "trace-1"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="daily_quality_item_reviewed",
    )
    assert event.metadata == {
        "artifact_type": "trace",
        "artifact_id": "trace-1",
        "project_id": "project-1",
        "campaign_key": "daily_quality_open_actions",
        "email_key": "daily_quality_open_actions_v1",
        "send_log_id": "00000000-0000-0000-0000-000000000321",
        "email_status": "current",
        "target_stage": "daily_review",
        "target_event": "daily_quality_item_reviewed",
        "link_issued_at": "2026-05-29T08:00:00Z",
        "context_status": "current",
        "source_id": "trace-1",
    }
