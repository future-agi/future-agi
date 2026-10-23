# ruff: noqa: F811
"""Two authorized saved versions, exact windows, and honest delta uncertainty."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from tracer.models.derived_metric import DerivedMetricRun, DerivedMetricVersion
from tracer.services.derived_metrics.comparison import compare_runs
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.service import execute_run
from tracer.tests.test_derived_metric_api import (  # noqa: F401
    ROOT,
    RUNS,
    body,
    run_body,
    saved,
)
from tracer.tests.test_derived_metric_clickhouse import (  # noqa: F401
    metric_ch,
    seed_span,
)
from tracer.tests.test_derived_metric_uncertainty import insert_units


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [
        "sampled_baseline",
        "sampled_candidate",
        "missing_design",
        "missing_snapshot",
        "partial_snapshot",
        "foreign_scope",
        "invalid_probability",
        "null_probability",
        "nonfinite_probability",
        "no_snapshot_id",
        "census",
    ],
)
def test_legacy_zero_width_intervals_require_frozen_census_proof(
    auth_client, saved, workspace, observe_project, metric_ch, case
):
    # Reproduce rows already persisted by the old estimator. Marginal intervals
    # are identical for every case; only the frozen sampling facts can establish
    # a census. These are real PostgreSQL runs and real ClickHouse result/input
    # rows, not patched results or authorization.
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    original = DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1)
    now = timezone.now()
    runs = []
    for index in range(2):
        definition = DerivedMetricVersion.objects.create(
            metric=original.metric,
            version=index + 2,
            formula="sum(resolved)",
            inputs=original.inputs,
            selection_unit="session",
            weighting="inverse_probability",
            filters=[],
            definition_hash=str(index) * 64,
        )
        run = DerivedMetricRun.objects.create(
            definition=definition,
            window_start=now - timedelta(days=1),
            window_end=now,
            as_of=now,
            fingerprint=uuid4().hex,
            snapshot_id=uuid4(),
            result_id=uuid4(),
            status=DerivedMetricRun.Status.COMPLETE,
        )
        probability, design, invalid, size = 1.0, "closed", 0, 50
        if (index == 0 and case == "sampled_baseline") or (
            index == 1 and case == "sampled_candidate"
        ):
            probability = 0.005
        if index == 1:
            if case == "missing_design":
                design = ""
            if case == "missing_snapshot":
                size = 0
            if case == "partial_snapshot":
                size = 49
            if case == "invalid_probability":
                invalid = 1
            if case == "null_probability":
                probability = None
            if case == "nonfinite_probability":
                probability = float("nan")
            if case == "no_snapshot_id":
                run.snapshot_id = None
                run.save(update_fields=["snapshot_id"])
                size = 0
        frozen_scope = (
            Scope(uuid4(), uuid4(), uuid4())
            if index == 1 and case == "foreign_scope"
            else scope
        )
        if size:
            insert_units(
                metric_ch,
                frozen_scope,
                run.snapshot_id,
                [
                    (
                        str(unit),
                        "",
                        {"resolved": 0.0, "escalated": 0.0},
                        probability,
                        "h1",
                        design,
                        10_000 if probability == 0.005 else 50,
                        invalid,
                    )
                    for unit in range(size)
                ],
            )
        metric_ch.execute(
            "INSERT INTO derived_metric_results (org_id, workspace_id, project_id, metric_id, "
            "metric_version, run_id, result_id, rollup, value, numerator, status, unit_count, valid_count, "
            "std_error, ci_low, ci_high, ci_status, ci_method) VALUES",
            [
                (
                    scope.organization,
                    scope.workspace,
                    scope.project,
                    original.metric_id,
                    definition.version,
                    run.id,
                    run.result_id,
                    "overall",
                    0.0,
                    0.0,
                    "ok",
                    50,
                    50,
                    0.0,
                    0.0,
                    0.0,
                    "ok",
                    "stratified_wor_fpc",
                )
            ],
        )
        runs.append(run)
    with (
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
        patch(
            "tracer.services.derived_metrics.comparison.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        response = auth_client.get(
            f"{RUNS}{runs[1].id}/compare/", {"baseline_run_id": str(runs[0].id)}
        )
    assert response.status_code == 200, response.data
    assert response.data["delta"] == 0
    interval = response.data["delta_uncertainty"]
    if case == "census":
        assert interval["status"] == "ok" and interval["method"] == "census"
        assert interval["ci_low"] == interval["ci_high"] == interval["std_error"] == 0
    else:
        assert interval["status"] == "not_estimable"
        assert interval["reason"] == "paired_covariance_unavailable"
        assert interval["method"] == ""
        assert (
            interval["ci_low"] is interval["ci_high"] is interval["std_error"] is None
        )


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
