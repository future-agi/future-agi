"""Visible-project admission must also hold for stored metric jobs."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tracer.models.derived_metric import DerivedMetric, DerivedMetricRun
from tracer.services.derived_metrics import service
from tracer.services.simulation_project import ensure_simulation_project
from tracer.tests.test_derived_metric_api import ROOT, RUNS, body, run_body

pytestmark = pytest.mark.django_db


@pytest.fixture
def hidden_metric_project(project):
    agent = AgentDefinition.objects.create(
        agent_name="Metric admission regression",
        description="Hidden project admission",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )
    return ensure_simulation_project(agent)


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
