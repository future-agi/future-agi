import pytest

from accounts.models import OnboardingActivationEvent
from accounts.services.onboarding.activation_events import record_event
from accounts.tests.onboarding_model_factories import (
    create_custom_eval,
    create_observe_project,
    create_trace,
)
from tracer.models.dashboard import Dashboard
from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
)
from tracer.models.saved_view import SavedView


def _record_trace_reviewed(*, organization, workspace, user, trace):
    return record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_full_page",
        product_path="observe",
        activation_stage="review_first_trace",
        metadata={
            "artifact_type": "trace",
            "artifact_id": str(trace.id),
            "project_id": str(trace.project_id),
        },
        idempotency_key=f"trace_reviewed:{trace.id}",
    )


def _first_loop_events(workspace):
    return OnboardingActivationEvent.no_workspace_objects.filter(
        workspace=workspace,
        event_name="first_quality_loop_completed",
        product_path="observe",
        is_sample=False,
    )


@pytest.mark.django_db
def test_custom_eval_creation_records_observe_first_loop_completion(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    _record_trace_reviewed(
        organization=organization,
        workspace=workspace,
        user=user,
        trace=trace,
    )

    custom_eval = create_custom_eval(
        organization=organization,
        workspace=workspace,
        project=project,
    )

    event = _first_loop_events(workspace).get()
    assert event.activation_stage == "activated"
    assert event.source == "observe_evaluator_created"
    assert event.metadata == {
        "artifact_type": "custom_eval_config",
        "artifact_id": str(custom_eval.id),
        "project_id": str(project.id),
        "completion_source": "observe_evaluator_created",
    }


@pytest.mark.django_db
def test_observe_loop_completion_is_idempotent_across_improvement_artifacts(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    _record_trace_reviewed(
        organization=organization,
        workspace=workspace,
        user=user,
        trace=trace,
    )

    create_custom_eval(organization=organization, workspace=workspace, project=project)
    UserAlertMonitor.no_workspace_objects.create(
        name="First observe alert",
        metric_type=MonitorMetricTypeChoices.COUNT_OF_ERRORS,
        organization=organization,
        workspace=workspace,
        created_by=user,
        project=project,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN,
        threshold_type=ThresholdCalculationMethodChoices.STATIC,
        critical_threshold_value=1,
    )

    assert _first_loop_events(workspace).count() == 1


@pytest.mark.django_db
def test_dashboard_creation_records_observe_first_loop_completion(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    _record_trace_reviewed(
        organization=organization,
        workspace=workspace,
        user=user,
        trace=trace,
    )

    dashboard = Dashboard.no_workspace_objects.create(
        workspace=workspace,
        name="Observe quality",
        created_by=user,
    )

    event = _first_loop_events(workspace).get()
    assert event.source == "observe_dashboard_created"
    assert event.metadata == {
        "artifact_type": "dashboard",
        "artifact_id": str(dashboard.id),
        "project_id": None,
        "completion_source": "observe_dashboard_created",
    }


@pytest.mark.django_db
def test_saved_view_creation_records_observe_first_loop_completion(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    trace = create_trace(project=project)
    _record_trace_reviewed(
        organization=organization,
        workspace=workspace,
        user=user,
        trace=trace,
    )

    saved_view = SavedView.no_workspace_objects.create(
        workspace=workspace,
        project=project,
        created_by=user,
        name="Slow traces",
        tab_type="traces",
        config={"filters": []},
    )

    event = _first_loop_events(workspace).get()
    assert event.source == "observe_saved_view_created"
    assert event.metadata == {
        "artifact_type": "saved_view",
        "artifact_id": str(saved_view.id),
        "project_id": str(project.id),
        "completion_source": "observe_saved_view_created",
    }


@pytest.mark.django_db
def test_observe_improvement_does_not_complete_loop_before_trace_review(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
    )

    create_custom_eval(organization=organization, workspace=workspace, project=project)

    assert _first_loop_events(workspace).count() == 0


@pytest.mark.django_db
def test_sample_observe_project_improvement_does_not_complete_real_loop(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
        source="demo",
        metadata={"is_sample": True},
    )
    trace = create_trace(project=project)
    _record_trace_reviewed(
        organization=organization,
        workspace=workspace,
        user=user,
        trace=trace,
    )

    create_custom_eval(organization=organization, workspace=workspace, project=project)

    assert _first_loop_events(workspace).count() == 0
