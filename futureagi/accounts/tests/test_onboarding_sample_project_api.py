import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models import OnboardingActivationEvent, OnboardingSampleProject
from accounts.services.onboarding.goals import save_onboarding_goal
from accounts.tests.onboarding_model_factories import create_observe_project
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.mark.django_db
def test_sample_project_requires_auth(api_client):
    response = api_client.post(
        "/accounts/sample-project/",
        {"path": "observe"},
        format="json",
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_sample_project": True,
    }
)
def test_sample_project_endpoint_creates_sample_and_returns_activation_state(
    auth_client,
    organization,
    workspace,
    user,
):
    save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    response = auth_client.post(
        "/accounts/sample-project/",
        {
            "path": "observe",
            "source": "onboarding_home",
            "reason": "waiting_for_first_trace",
            "campaign_key": "observe_sample_bridge",
            "email_key": "observe_sample_bridge_v1",
            "send_log_id": "00000000-0000-0000-0000-000000000322",
            "target_stage": "waiting_for_first_trace_sample_available",
            "target_event": "onboarding_sample_project_opened",
            "quick_start_goal": "explore_sample_data",
            "quick_start_id": "sample_preview",
            "quick_start_primary_path": "sample",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    sample = result["sample_project"]
    activation_state = result["activation_state"]
    assert sample["status"] == "ready_for_observe"
    assert sample["entry_route"].startswith("/dashboard/observe/")
    assert set(sample["artifact_refs"]["optional_paths"]) == {
        "prompt",
        "evals",
        "agent",
        "voice",
        "gateway",
    }
    assert (
        sample["artifact_refs"]["optional_paths"]["prompt"]["preview"]["headline"]
        == "An edit improved one case but quietly broke another"
    )
    assert activation_state["is_activated"] is False
    assert activation_state["stage"] == "connect_observability"
    assert activation_state["sample_project"]["status"] == "ready_for_observe"
    assert (
        activation_state["sample_project"]["artifact_refs"]["optional_paths"][
            "gateway"
        ]["artifact_type"]
        == "gateway_request_log"
    )
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="onboarding_sample_project_opened",
        is_sample=True,
    )
    assert event.metadata["campaign_key"] == "observe_sample_bridge"
    assert event.metadata["email_key"] == "observe_sample_bridge_v1"
    assert event.metadata["send_log_id"] == "00000000-0000-0000-0000-000000000322"
    assert event.metadata["target_event"] == "onboarding_sample_project_opened"
    assert event.metadata["quick_start_goal"] == "explore_sample_data"
    assert event.metadata["quick_start_id"] == "sample_preview"
    assert event.metadata["quick_start_primary_path"] == "sample"


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_sample_project": True,
    }
)
def test_sample_project_endpoint_is_idempotent(auth_client, workspace):
    payload = {"path": "observe", "source": "onboarding_home"}

    first = auth_client.post("/accounts/sample-project/", payload, format="json")
    second = auth_client.post("/accounts/sample-project/", payload, format="json")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert (
        OnboardingSampleProject.no_workspace_objects.filter(workspace=workspace).count()
        == 1
    )
    assert (
        Project.no_workspace_objects.filter(
            workspace=workspace,
            metadata__is_sample=True,
        ).count()
        == 1
    )
    assert (
        Trace.no_workspace_objects.filter(
            project__workspace=workspace,
            metadata__is_sample=True,
        ).count()
        == 1
    )
    assert (
        first.json()["result"]["sample_project"]["entry_route"]
        == second.json()["result"]["sample_project"]["entry_route"]
    )


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_sample_project": True,
    }
)
def test_sample_project_hide_endpoint_suppresses_sample_state(auth_client, workspace):
    auth_client.post("/accounts/sample-project/", {"path": "observe"}, format="json")

    response = auth_client.post(
        "/accounts/sample-project/hide/",
        {"source": "onboarding_home", "reason": "user_dismissed"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["sample_project"]["status"] == "hidden"
    assert result["sample_project"]["is_hidden"] is True
    assert result["activation_state"]["sample_project"]["status"] == "hidden"
    assert (
        result["activation_state"]["fallback_action"]["id"]
        == "open_observe_setup_fallback"
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_sample_project": False})
def test_sample_project_endpoint_returns_unavailable_when_flag_off(auth_client):
    before_count = OnboardingSampleProject.no_workspace_objects.count()

    response = auth_client.post(
        "/accounts/sample-project/",
        {"path": "observe"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["sample_project"]["status"] == "unavailable"
    assert OnboardingSampleProject.no_workspace_objects.count() == before_count


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_sample_project": False,
        "onboarding_observe_mvp_enabled": True,
    }
)
def test_test_trace_endpoint_creates_trace_and_returns_activation_state(
    auth_client,
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    save_onboarding_goal(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
    )

    response = auth_client.post(
        "/accounts/test-trace/",
        {
            "path": "observe",
            "project_id": str(project.id),
            "source": "onboarding_home",
            "reason": "waiting_for_first_trace",
            "quick_start_goal": "monitor_production_ai_app",
            "quick_start_id": "observe",
            "quick_start_primary_path": "observe",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["created"] is True
    assert result["project_id"] == str(project.id)
    trace = Trace.no_workspace_objects.get(id=result["trace_id"])
    assert trace.project_id == project.id
    assert trace.input is None
    assert trace.output is None
    assert trace.metadata["onboarding_test_trace"] is True
    assert trace.metadata["is_sample"] is False
    assert trace.tags == ["onboarding", "test-trace"]
    activation_state = result["activation_state"]
    assert activation_state["stage"] == "review_first_trace"
    assert activation_state["is_activated"] is False
    assert activation_state["signals"]["first_trace_id"] == str(trace.id)
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="trace_received",
        is_sample=False,
    )
    assert event.product_path == "observe"
    assert event.activation_stage == "waiting_for_first_trace"
    assert event.metadata["source_id"] == str(trace.id)
    assert event.metadata["project_id"] == str(project.id)
    assert event.metadata["quick_start_goal"] == "monitor_production_ai_app"


@pytest.mark.django_db
@override_settings(
    ONBOARDING_FEATURE_FLAGS={
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_sample_project": False,
        "onboarding_observe_mvp_enabled": True,
    }
)
def test_test_trace_endpoint_is_idempotent(auth_client, organization, workspace, user):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    payload = {
        "path": "observe",
        "project_id": str(project.id),
        "source": "onboarding_home",
        "reason": "waiting_for_first_trace",
    }

    first = auth_client.post("/accounts/test-trace/", payload, format="json")
    second = auth_client.post("/accounts/test-trace/", payload, format="json")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["result"]["created"] is True
    assert second.json()["result"]["created"] is False
    assert first.json()["result"]["trace_id"] == second.json()["result"]["trace_id"]
    assert (
        Trace.no_workspace_objects.filter(
            project=project,
            metadata__onboarding_test_trace=True,
        ).count()
        == 1
    )
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            workspace=workspace,
            event_name="trace_received",
            is_sample=False,
        ).count()
        == 1
    )
