# ruff: noqa: F811
"""Two authorized saved versions, exact windows, and honest delta uncertainty."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from tracer.models.derived_metric import DerivedMetricRun
from tracer.services.derived_metrics.comparison import compare_runs
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.service import execute_run
from tracer.tests.test_derived_metric_api import ROOT, RUNS, body, run_body, saved  # noqa: F401
from tracer.tests.test_derived_metric_clickhouse import metric_ch, seed_span  # noqa: F401


@pytest.mark.django_db
def test_compare_saved_versions_same_window_and_scope(
    auth_client, saved, observe_project, workspace, user, metric_ch
):
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    now = timezone.now()
    for index in range(4):
        seed_span(
            metric_ch,
            scope,
            f"unit-{index}",
            uuid4(),
            uuid4(),
            now - timedelta(hours=1),
            escalated=index % 2,
        )
    request = run_body()
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        first = auth_client.post(f"{ROOT}{saved['id']}/runs/", request, format="json")
        assert first.status_code == 202
        first_id = first.data["id"]
        execute_run(first_id)
        changed = auth_client.put(
            f"{ROOT}{saved['id']}/",
            {
                **body(observe_project),
                "formula": "mean(resolved + 0 * escalated)",
                "expected_version": 1,
            },
            format="json",
        )
        assert changed.status_code == 200, changed.data
        second = auth_client.post(
            f"{ROOT}{saved['id']}/runs/",
            {**request, "idempotency_key": str(uuid4())},
            format="json",
        )
        assert second.status_code == 202
        second_id = second.data["id"]
        execute_run(second_id)
        response = auth_client.get(
            f"{RUNS}{second_id}/compare/", {"baseline_run_id": first_id}
        )
        assert response.status_code == 200, response.data
        assert (
            response.data["baseline_version"] == 1
            and response.data["candidate_version"] == 2
        )
        assert response.data["delta"] == 0.5
        assert (
            response.data["delta_uncertainty"]["reason"]
            == "paired_covariance_unavailable"
        )
        assert response.data["delta_uncertainty"]["ci_low"] is None
        same = auth_client.get(
            f"{RUNS}{first_id}/compare/", {"baseline_run_id": first_id}
        )
        assert same.status_code == 200
        assert (
            same.data["delta"]
            == same.data["delta_uncertainty"]["ci_low"]
            == same.data["delta_uncertainty"]["ci_high"]
            == 0
        )
        baseline = DerivedMetricRun.objects.select_related("definition").get(
            pk=first_id
        )
        candidate = DerivedMetricRun.objects.select_related("definition").get(
            pk=second_id
        )
        candidate.definition.filters = [{"field": "model", "value": "other"}]
        with pytest.raises(MetricInputError, match="definition filters"):
            compare_runs(baseline, candidate)
        DerivedMetricRun.objects.filter(pk=second_id).update(
            window_end=now + timedelta(hours=1)
        )
        assert (
            auth_client.get(
                f"{RUNS}{second_id}/compare/", {"baseline_run_id": first_id}
            ).status_code
            == 400
        )
        other = Workspace.objects.create(
            name="Different", organization=user.organization, created_by=user
        )
        observe_project.workspace = other
        observe_project.save()
        assert (
            auth_client.get(
                f"{RUNS}{first_id}/compare/", {"baseline_run_id": second_id}
            ).status_code
            == 404
        )
