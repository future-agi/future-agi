import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models import OnboardingActivationEvent, OnboardingSampleProject
from accounts.services.onboarding.goals import save_onboarding_goal
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
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    sample = result["sample_project"]
    activation_state = result["activation_state"]
    assert sample["status"] == "ready_for_observe"
    assert sample["entry_route"].startswith("/dashboard/observe/")
    assert activation_state["is_activated"] is False
    assert activation_state["stage"] == "connect_observability"
    assert activation_state["sample_project"]["status"] == "ready_for_observe"
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        workspace=workspace,
        event_name="onboarding_sample_project_opened",
        is_sample=True,
    ).exists()


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
