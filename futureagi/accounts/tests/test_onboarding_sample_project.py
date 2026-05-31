import pytest

from accounts.models import OnboardingActivationEvent, OnboardingSampleProject
from accounts.services.onboarding.sample_manifest import (
    DEFAULT_SAMPLE_MANIFEST_ID,
    DEFAULT_SAMPLE_MANIFEST_VERSION,
    get_sample_manifest,
)
from accounts.services.onboarding.sample_project import (
    create_or_get_sample_project,
    ensure_sample_project_ready,
    get_sample_project_state,
    hide_sample_project,
)
from accounts.services.onboarding.signal_resolver import collect_onboarding_signals
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


def test_sample_project_manifest_is_config_driven():
    manifest = get_sample_manifest()

    assert manifest["manifest_id"] == DEFAULT_SAMPLE_MANIFEST_ID
    assert manifest["artifacts"]["sample_trace"]["metadata"]["quality_issue"] == (
        "policy_mismatch"
    )
    assert manifest["artifacts"]["spans"]["retrieval"]["parent"] == "root"
    assert manifest["artifacts"]["spans"]["evaluator"]["timing_ms"]["latency"] == 200


@pytest.mark.django_db
def test_sample_project_create_is_idempotent_and_opens_trace_route(
    organization,
    workspace,
    user,
):
    first = create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="waiting_for_first_trace",
    )
    second = create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="waiting_for_first_trace",
    )

    assert first["status"] == "ready_for_observe"
    assert second["status"] == "ready_for_observe"
    assert first["entry_route"] == second["entry_route"]
    assert first["entry_route"].startswith("/dashboard/observe/")
    assert "sample=true" in first["entry_route"]
    assert (
        OnboardingSampleProject.no_workspace_objects.filter(
            workspace=workspace,
            manifest_id=DEFAULT_SAMPLE_MANIFEST_ID,
            manifest_version=DEFAULT_SAMPLE_MANIFEST_VERSION,
        ).count()
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
        ObservationSpan.no_workspace_objects.filter(
            project__workspace=workspace,
            metadata__is_sample=True,
        ).count()
        == 4
    )


@pytest.mark.django_db
def test_sample_project_ready_provision_does_not_record_open_event(
    organization,
    workspace,
    user,
):
    state = ensure_sample_project_ready(
        user,
        organization,
        workspace,
        is_enabled=True,
        can_create=True,
    )

    assert state["status"] == "ready_for_observe"
    assert state["created"] is True
    assert state["entry_route"].startswith("/dashboard/observe/")
    sample_project = OnboardingSampleProject.no_workspace_objects.get(
        workspace=workspace,
        manifest_id=DEFAULT_SAMPLE_MANIFEST_ID,
        manifest_version=DEFAULT_SAMPLE_MANIFEST_VERSION,
    )
    assert sample_project.first_opened_by is None
    assert sample_project.last_opened_by is None
    assert sample_project.last_opened_at is None
    assert (
        OnboardingActivationEvent.no_workspace_objects.filter(
            workspace=workspace,
            event_name__in=[
                "onboarding_sample_project_opened",
                "sample_trace_available",
            ],
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_sample_project_does_not_count_as_real_activation(
    organization,
    workspace,
    user,
):
    create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="waiting_for_first_trace",
    )

    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert signals.observe_project_exists is False
    assert signals.trace_exists is False
    assert signals.first_loop_completed is False
    assert signals.first_observe_id is None
    assert signals.first_trace_id is None
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        workspace=workspace,
        event_name="onboarding_sample_project_opened",
        is_sample=True,
    ).exists()
    assert OnboardingActivationEvent.no_workspace_objects.filter(
        workspace=workspace,
        event_name="sample_trace_available",
        is_sample=True,
    ).exists()


@pytest.mark.django_db
def test_sample_project_state_reports_missing_span_as_repairable(
    organization,
    workspace,
    user,
):
    create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="waiting_for_first_trace",
    )
    sample_project = OnboardingSampleProject.no_workspace_objects.get(
        workspace=workspace
    )
    span_id = sample_project.artifact_refs["spans"]["evaluator"]
    ObservationSpan.no_workspace_objects.get(id=span_id).delete()

    state = get_sample_project_state(user, organization, workspace)

    assert state["status"] == "ready_for_observe"
    assert state["is_repairable"] is True
    assert "span:evaluator" in state["missing_artifacts"]
    assert state["entry_route"]


@pytest.mark.django_db
def test_hidden_sample_project_suppresses_sample_cta(
    organization,
    workspace,
    user,
):
    create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="waiting_for_first_trace",
    )

    state = hide_sample_project(
        user,
        organization,
        workspace,
        source="test",
        reason="user_dismissed",
    )

    assert state["status"] == "hidden"
    assert state["available"] is False
    assert state["is_hidden"] is True
    assert state["blocked_reason"] == "sample_hidden"
