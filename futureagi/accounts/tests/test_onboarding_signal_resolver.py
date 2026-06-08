import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.workspace import Workspace
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.signal_resolver import collect_onboarding_signals
from accounts.tests.onboarding_model_factories import (
    create_custom_eval,
    create_observe_project,
    create_trace,
)


@pytest.mark.django_db
def test_collect_signals_reads_observe_setup_without_trace_payload(
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

    with CaptureQueriesContext(connection) as queries:
        signals = collect_onboarding_signals(
            user=user,
            organization=organization,
            workspace=workspace,
        )

    assert signals.observe_project_exists is True
    assert signals.trace_exists is True
    assert signals.first_trace_id == str(trace.id)
    selected_sql = "\n".join(query["sql"].lower() for query in queries)
    assert '"tracer_trace"."input"' not in selected_sql
    assert '"tracer_trace"."output"' not in selected_sql


@pytest.mark.django_db
def test_collect_signals_is_workspace_scoped(organization, workspace, user):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Signal Workspace",
        organization=organization,
        created_by=user,
    )
    project = create_observe_project(
        organization=organization,
        workspace=other_workspace,
        user=user,
    )
    create_trace(project=project)

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.observe_project_exists is False
    assert signals.trace_exists is False


@pytest.mark.django_db
def test_demo_observe_project_does_not_count_as_real_setup(
    organization,
    workspace,
    user,
):
    create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
        source="demo",
    )

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.observe_project_exists is False
    assert signals.observe_projects == 0
    assert signals.first_observe_id is None


@pytest.mark.django_db
def test_demo_observe_trace_does_not_count_as_real_trace(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
        source="demo",
    )
    create_trace(project=project)

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.observe_project_exists is False
    assert signals.trace_exists is False
    assert signals.first_trace_id is None


@pytest.mark.django_db
def test_demo_observe_project_cannot_complete_real_activation(
    organization,
    workspace,
    user,
):
    project = create_observe_project(
        organization=organization,
        workspace=workspace,
        user=user,
        source="demo",
    )
    create_trace(project=project)
    create_custom_eval(organization=organization, workspace=workspace, project=project)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="demo_project",
        product_path="observe",
        is_sample=False,
    )

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.trace_reviewed is True
    assert signals.observe_project_exists is False
    assert signals.trace_exists is False
    assert signals.first_loop_completed is False


@pytest.mark.django_db
def test_sample_review_does_not_complete_real_loop(organization, workspace, user):
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="sample_project",
        product_path="observe",
        is_sample=True,
    )

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.sample_trace_reviewed is True
    assert signals.trace_reviewed is False
    assert signals.first_loop_completed is False
