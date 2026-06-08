from unittest import mock

import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models import OnboardingActivationEvent
from accounts.tests.onboarding_model_factories import (
    create_observe_project,
    create_trace,
)


def _value_signal_reader(aggregate):
    """Build a fake CHSpanReader-as-context-manager whose per_trace_aggregate
    returns `aggregate` keyed by the requested trace id (mirrors the real
    `.get(str(trace_id), {})` access). Used to mock `get_reader` so the
    activation-event handler never touches a real ClickHouse cluster."""
    reader = mock.MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    reader.per_trace_aggregate.side_effect = lambda trace_ids: {
        str(trace_ids[0]): aggregate
    }
    return reader


@pytest.mark.django_db
def test_onboarding_activation_event_requires_auth(api_client):
    response = api_client.post(
        "/accounts/activation-events/",
        {"event_name": "trace_detail_opened"},
        format="json",
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_trace_detail_open_records_trace_review_and_returns_next_state(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "trace_detail_opened",
            "primary_path": "observe",
            "stage": "review_first_trace",
            "source": "trace_full_page",
            "artifact_type": "trace",
            "artifact_id": str(trace.id),
            "project_id": str(project.id),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["event_name"] == "trace_reviewed"
    assert result["activation_state"]["stage"] == "create_trace_evaluator"
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        event_name="trace_reviewed",
        workspace=workspace,
        is_sample=False,
        metadata__artifact_id=str(trace.id),
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_trace_detail_open_is_idempotent(auth_client, organization, workspace, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    payload = {
        "event_name": "trace_detail_opened",
        "primary_path": "observe",
        "stage": "review_first_trace",
        "source": "trace_full_page",
        "artifact_type": "trace",
        "artifact_id": str(trace.id),
        "project_id": str(project.id),
    }

    first = auth_client.post("/accounts/activation-events/", payload, format="json")
    second = auth_client.post("/accounts/activation-events/", payload, format="json")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            event_name="trace_reviewed",
            workspace=workspace,
            metadata__artifact_id=str(trace.id),
        ).count()
        == 1
    )
    assert first.json()["result"]["event_id"] == second.json()["result"]["event_id"]


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_sample_trace_review_does_not_advance_real_observe_stage(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "trace_detail_opened",
            "primary_path": "observe",
            "stage": "review_first_trace",
            "source": "sample_trace_full_page",
            "artifact_type": "trace",
            "artifact_id": str(trace.id),
            "project_id": str(project.id),
            "is_sample": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["activation_state"]["stage"] == (
        "review_first_trace"
    )
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        event_name="trace_reviewed",
        workspace=workspace,
        is_sample=True,
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_activation_event_response_uses_quick_start_context_from_body(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "onboarding_eval_route_focus_viewed",
            "primary_path": "evals",
            "stage": "create_eval_dataset",
            "source": "eval_create_onboarding",
            "artifact_type": "eval",
            "artifact_id": "eval-1",
            "metadata": {
                "quick_start_goal": "evaluate_quality",
                "quick_start_id": "evals",
                "quick_start_primary_path": "evals",
            },
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    activation_state = response.json()["result"]["activation_state"]
    assert activation_state["goal"] == "evaluate_quality"
    assert activation_state["primary_path"] == "evals"
    assert activation_state["eval"] is not None


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_observe_setup_route_focus_records_event(auth_client, workspace, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "onboarding_observe_route_focus_viewed",
            "primary_path": "observe",
            "stage": "connect_observability",
            "source": "observe_setup_onboarding",
            "artifact_type": "observe_setup",
            "artifact_id": "observe-setup",
            "metadata": {
                "route_mode": "setup-observe",
                "setup": True,
                "setup_language": "python",
                "setup_provider": "anthropic",
            },
            "idempotency_key": "observe:setup:focus",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["event_name"] == "onboarding_observe_route_focus_viewed"
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="onboarding_observe_route_focus_viewed",
    )
    assert event.activation_stage == "connect_observability"
    assert event.product_path == "observe"
    assert event.metadata == {
        "artifact_id": "observe-setup",
        "artifact_type": "observe_setup",
        "project_id": None,
        "route_mode": "setup-observe",
        "setup": True,
        "setup_language": "python",
        "setup_provider": "anthropic",
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_observe_package_selection_records_event(auth_client, workspace, user):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "onboarding_observe_package_selected",
            "primary_path": "observe",
            "stage": "connect_observability",
            "source": "onboarding_home",
            "artifact_type": "observe_setup",
            "artifact_id": "observe-package",
            "metadata": {
                "action_id": "create_observe_project",
                "setup_language": "python",
                "setup_provider": "anthropic",
            },
            "idempotency_key": "observe:package:selected:anthropic:python",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["event_name"] == "onboarding_observe_package_selected"
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="onboarding_observe_package_selected",
    )
    assert event.activation_stage == "connect_observability"
    assert event.product_path == "observe"
    assert event.metadata == {
        "action_id": "create_observe_project",
        "artifact_id": "observe-package",
        "artifact_type": "observe_setup",
        "project_id": None,
        "setup_language": "python",
        "setup_provider": "anthropic",
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_observe_project_route_focus_records_event(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "onboarding_observe_route_focus_viewed",
            "primary_path": "observe",
            "stage": "waiting_for_first_trace",
            "source": "observe_project_onboarding",
            "artifact_type": "observe_project",
            "artifact_id": str(project.id),
            "project_id": str(project.id),
            "metadata": {
                "project_id": str(project.id),
                "route_mode": "send-first-trace",
            },
            "idempotency_key": "observe:project:focus",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["event_name"] == "onboarding_observe_route_focus_viewed"
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="onboarding_observe_route_focus_viewed",
    )
    assert event.activation_stage == "waiting_for_first_trace"
    assert event.metadata == {
        "artifact_id": str(project.id),
        "artifact_type": "observe_project",
        "project_id": str(project.id),
        "route_mode": "send-first-trace",
    }


@pytest.mark.django_db
def test_first_quality_loop_completion_requires_primary_path(auth_client, workspace):
    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "first_quality_loop_completed",
            "stage": "activated",
            "source": "manual_completion",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            workspace=workspace,
            event_name="first_quality_loop_completed",
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_direct_observe_first_quality_loop_completion_is_rejected(
    auth_client,
    workspace,
):
    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "first_quality_loop_completed",
            "primary_path": "observe",
            "stage": "activated",
            "source": "manual_completion",
            "artifact_type": "eval",
            "artifact_id": "eval-1",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            workspace=workspace,
            event_name="first_quality_loop_completed",
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_prompt_first_quality_loop_completion_api_remains_supported(
    auth_client,
    workspace,
):
    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "first_quality_loop_completed",
            "primary_path": "prompt",
            "stage": "activated",
            "source": "prompt_metrics",
            "metadata": {"template_id": "prompt-1"},
            "idempotency_key": "prompt:first-quality-loop:prompt-1",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    event = OnboardingActivationEvent.no_workspace_objects.get(
        workspace=workspace,
        event_name="first_quality_loop_completed",
    )
    assert event.product_path == "prompt"
    assert event.activation_stage == "activated"
    assert event.metadata == {"template_id": "prompt-1"}


@pytest.mark.django_db
def test_activation_event_rejects_unknown_event(auth_client):
    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "unknown_event",
            "primary_path": "observe",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationEvent.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_trace_review_rejects_trace_outside_workspace(
    auth_client,
    organization,
    workspace,
    user,
):
    other_project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    other_trace = create_trace(project=other_project)

    response = auth_client.post(
        "/accounts/activation-events/",
        {
            "event_name": "trace_detail_opened",
            "primary_path": "observe",
            "stage": "review_first_trace",
            "source": "trace_full_page",
            "artifact_type": "trace",
            "artifact_id": str(other_trace.id),
            "project_id": "00000000-0000-0000-0000-000000000000",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            event_name="trace_reviewed",
        ).count()
        == 0
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_trace_review_stamps_value_signal_from_clickhouse(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    aggregate = {
        "span_count": 3,
        "prompt_tokens": 900,
        "completion_tokens": 340,
        "total_tokens": 1240,
        "cost": 0.0300,
        "start_time": None,
        "end_time": None,
        "latency_ms": 1200,
    }

    # Mock the reader at its source module so the handler never reaches a real
    # ClickHouse cluster; the v2 package re-exports it lazily inside the view.
    with mock.patch(
        "tracer.services.clickhouse.v2.get_reader",
        return_value=_value_signal_reader(aggregate),
    ):
        response = auth_client.post(
            "/accounts/activation-events/",
            {
                "event_name": "trace_detail_opened",
                "primary_path": "observe",
                "stage": "review_first_trace",
                "source": "trace_full_page",
                "artifact_type": "trace",
                "artifact_id": str(trace.id),
                "project_id": str(project.id),
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    event = OnboardingActivationEvent.no_workspace_objects.get(
        event_name="trace_reviewed",
        workspace=workspace,
        is_sample=False,
        metadata__artifact_id=str(trace.id),
    )
    assert event.metadata["signal"] == {
        "latency_ms": 1200,
        "cost": 0.03,
        "total_tokens": 1240,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_trace_review_records_event_when_clickhouse_raises(
    auth_client,
    organization,
    workspace,
    user,
):
    user.goals = ["monitor_production_ai_app"]
    user.save(update_fields=["goals"])
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)

    with mock.patch(
        "tracer.services.clickhouse.v2.get_reader",
        side_effect=RuntimeError("clickhouse unavailable"),
    ):
        response = auth_client.post(
            "/accounts/activation-events/",
            {
                "event_name": "trace_detail_opened",
                "primary_path": "observe",
                "stage": "review_first_trace",
                "source": "trace_full_page",
                "artifact_type": "trace",
                "artifact_id": str(trace.id),
                "project_id": str(project.id),
            },
            format="json",
        )

    # Recording must succeed even though the value signal lookup failed.
    assert response.status_code == status.HTTP_200_OK
    event = OnboardingActivationEvent.no_workspace_objects.get(
        event_name="trace_reviewed",
        workspace=workspace,
        is_sample=False,
        metadata__artifact_id=str(trace.id),
    )
    assert "signal" not in event.metadata
