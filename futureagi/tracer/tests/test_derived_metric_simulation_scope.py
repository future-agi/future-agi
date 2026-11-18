"""Visible-project admission must also hold for stored metric jobs."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    MetricProjectRefresh,
)
from tracer.models.metric_pg_revision import MetricPgResponseRevision
from tracer.models.project import Project
from tracer.services.derived_metrics import admission, service
from tracer.services.simulation_project import ensure_simulation_project
from tracer.tests.test_derived_metric_api import ROOT, RUNS, body, run_body

# A rollout 404 must not mask the project-authorization boundary under test.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("enabled_metrics")]


@pytest.fixture
def metric_agent(project):
    return AgentDefinition.objects.create(
        agent_name="Metric admission regression",
        description="Hidden project admission",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )


@pytest.fixture
def hidden_metric_project(metric_agent):
    return ensure_simulation_project(metric_agent)


@pytest.mark.parametrize("action", ["", "validate/", "evaluators/"])
def test_metric_authoring_refuses_hidden_project_before_reading(
    auth_client, hidden_metric_project, action
):
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client"
    ) as client:
        if action == "evaluators/":
            response = auth_client.get(
                ROOT + action, {"project_id": str(hidden_metric_project.id)}
            )
        else:
            response = auth_client.post(
                ROOT + action, body(hidden_metric_project), format="json"
            )
    assert response.status_code == 404, response.data
    client.assert_not_called()
    assert not DerivedMetric.objects.filter(project=hidden_metric_project).exists()


def test_stored_hidden_metric_is_refused_by_reads_and_worker(
    auth_client, hidden_metric_project, workspace, user
):
    # Simulation never creates this row. A preexisting/inconsistent row must not
    # turn a foreign-key join into admission, especially after a job is queued.
    metric = service.save_definition(
        body(hidden_metric_project), hidden_metric_project, workspace, user
    )
    now = timezone.now()
    run = DerivedMetricRun.objects.create(
        definition=metric.versions.get(version=1),
        requested_by=user,
        window_start=now - timedelta(hours=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
    )
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client"
    ) as client:
        for path in (
            f"{ROOT}{metric.id}/",
            f"{RUNS}{run.id}/results/",
            f"{ROOT}{metric.id}/preview/",
        ):
            response = (
                auth_client.get(path)
                if not path.endswith("preview/")
                else auth_client.post(path, run_body(), format="json")
            )
            assert response.status_code == 404, response.data
        client.assert_not_called()
    with patch("tracer.services.derived_metrics.service._compute") as compute:
        service.execute_run(run.id)
    run.refresh_from_db()
    assert run.status == DerivedMetricRun.Status.FAILED
    assert "no longer authorized" in run.error
    compute.assert_not_called()


@pytest.mark.parametrize("hooks_enabled", [False, True], ids=["hooks-off", "hooks-on"])
def test_pg_response_hooks_do_not_enroll_a_simulation_project(
    project, metric_agent, settings, hooks_enabled, record_property
):
    settings.FI_SESSIONS_PG_REVISIONS_ENABLED = hooks_enabled
    before = MetricPgResponseRevision.objects.filter(project_id=project.pk).first()
    revision_before = before.revision if before else 0
    project.save()
    visible = MetricPgResponseRevision.objects.filter(project_id=project.pk).first()
    if hooks_enabled:
        assert visible is not None
        assert visible.revision > revision_before
    else:
        assert (visible.revision if visible else 0) == revision_before

    hidden = ensure_simulation_project(metric_agent)
    assert ensure_simulation_project(metric_agent).pk == hidden.pk
    assert Project.with_simulation.filter(pk=hidden.pk).exists()
    assert not Project.objects.filter(pk=hidden.pk).exists()
    count = MetricPgResponseRevision.objects.filter(project_id=hidden.pk).count()
    record_property("hooks_enabled", hooks_enabled)
    record_property(
        "visible_revision_advanced",
        bool(visible and visible.revision > revision_before),
    )
    record_property("hidden_revision_rows", count)
    assert count == 0, (
        "enabled PG response hooks enrolled the hidden simulation project"
    )


@pytest.fixture
def stored_project_states(project, hidden_metric_project, workspace, user):
    # Deliberately seed inconsistent hidden stored work, just as the legacy
    # execute_run case does. The scheduler cannot rely on HTTP authoring alone.
    for scope in (hidden_metric_project, project):
        service.save_definition(body(scope), scope, workspace, user)
    now = timezone.now()
    MetricProjectRefresh.objects.filter(project=hidden_metric_project).update(
        created_at=now - timedelta(days=2)
    )
    MetricProjectRefresh.objects.filter(project=project).update(
        created_at=now - timedelta(days=1)
    )
    return project, hidden_metric_project, now


def test_continuous_eligibility_excludes_stored_hidden_project(
    stored_project_states, record_property
):
    visible, hidden, now = stored_project_states
    eligible = set(
        admission.eligible_projects(now).values_list("project_id", flat=True)
    )
    record_property("visible_eligible", visible.pk in eligible)
    record_property("hidden_eligible", hidden.pk in eligible)
    assert visible.pk in eligible
    assert hidden.pk not in eligible, "continuous scheduler admits hidden stored work"


@pytest.mark.parametrize("enabled", [False, True], ids=["metrics-off", "metrics-on"])
def test_continuous_dispatch_does_not_lease_or_enqueue_stored_hidden_project(
    stored_project_states, settings, enabled, record_property
):
    visible, hidden, now = stored_project_states
    settings.METRICS_ENABLED = int(enabled)
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    payloads = []
    # The real PG eligibility, advisory lock, lease and enqueue boundary run.
    # No worker/CH execution is needed to prove unauthorized scheduler admission.
    count = admission.dispatch(payloads.append, clock=lambda: now)
    hidden_state = MetricProjectRefresh.objects.get(project=hidden)
    visible_state = MetricProjectRefresh.objects.get(project=visible)
    record_property("hidden_lease_epoch", hidden_state.fencing_epoch)
    record_property(
        "hidden_enqueued", any(p["project_id"] == str(hidden.pk) for p in payloads)
    )
    record_property(
        "visible_enqueued", any(p["project_id"] == str(visible.pk) for p in payloads)
    )
    record_property("dispatched", count)
    assert hidden_state.fencing_epoch == 0, "scheduler leased hidden simulation work"
    assert hidden_state.lease_token is None
    assert hidden_state.last_started_at is None
    # Rollout-off still schedules source-retention work for visible projects;
    # it must never compute fresh metrics or admit hidden simulation projects.
    assert count == 1
    assert [p["project_id"] for p in payloads] == [str(visible.pk)]
    assert visible_state.fencing_epoch == 1
    assert [p["purpose"] for p in payloads] == ["incremental" if enabled else "expiry"]
