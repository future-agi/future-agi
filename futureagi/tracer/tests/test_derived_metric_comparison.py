# ruff: noqa: F811 -- imported shared fixture
"""B18 retired comparison routes; snapshot capture is not implemented here.

The former twelve positive-route cases depended on the removed product API.
Keep explicit negative coverage with real saved records, including another
workspace, so neither rollout activation nor a valid run can restore that API.
"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import Resolver404, resolve
from django.utils import timezone

from accounts.models.workspace import Workspace
from tracer.models.derived_metric import DerivedMetricRun, DerivedMetricVersion
from tracer.tests.test_derived_metric_api import RUNS, saved  # noqa: F401


@pytest.mark.django_db
@pytest.mark.parametrize("enabled", [0, 1])
@pytest.mark.parametrize("foreign_workspace", [False, True])
@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_retired_comparison_api_stays_unavailable(
    auth_client,
    saved,
    observe_project,
    user,
    settings,
    enabled,
    foreign_workspace,
    method,
):
    now = timezone.now()
    run = DerivedMetricRun.objects.create(
        definition=DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1),
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
        snapshot_id=uuid4(),
        result_id=uuid4(),
        status=DerivedMetricRun.Status.COMPLETE,
    )
    if foreign_workspace:
        observe_project.workspace = Workspace.objects.create(
            name="Other test workspace", organization=user.organization, created_by=user
        )
        observe_project.save(update_fields=["workspace"])
    settings.METRICS_ENABLED = enabled
    with patch("tracer.services.derived_metrics.comparison.compare_runs") as compare:
        response = getattr(auth_client, method)(
            f"{RUNS}{run.id}/compare/",
            {"baseline_run_id": str(run.id)},
            format="json",
        )
    assert response.status_code == 404
    compare.assert_not_called()


def test_comparison_action_is_absent_from_the_router():
    with pytest.raises(Resolver404):
        resolve(f"{RUNS}{uuid4()}/compare/")
