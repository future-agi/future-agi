"""A report run more often than its detail lists still counts every run
(OBS-SW-146): the run pager's "of N" and the delete warning read run_count,
and the oldest listed run's detail points at the older ones."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.insights import InsightConfig, InsightRun

CONFIGS = "/tracer/insights/configs/"
RUNS = "/tracer/insights/runs/"
LISTED = 50


@pytest.fixture(autouse=True)
def insight_enabled(monkeypatch):
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    # Insights is Enterprise-only: run it as a licensed install does.
    monkeypatch.setattr(capability_service, "_configured", True)
    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(allowed=True, feature_id=feature_id),
    )


def report_with_runs(project, count):
    """A report and its runs, oldest first, an hour apart."""
    config = InsightConfig.objects.create(
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        name="Weekly failures",
        unit="sessions",
        lens="failures",
        question="Where is the agent failing?",
    )
    start = timezone.now() - timedelta(hours=count + 1)
    runs = []
    for i in range(count):
        run = InsightRun.objects.create(
            config=config,
            project=project,
            organization=project.organization,
            workspace=project.workspace,
            unit="sessions",
            status="completed",
        )
        InsightRun.objects.filter(id=run.id).update(
            created_at=start + timedelta(hours=i)
        )
        runs.append(run)
    return config, runs


@pytest.mark.django_db
def test_a_report_counts_every_run_beyond_the_ones_it_lists(
    auth_client, observe_project
):
    config, runs = report_with_runs(observe_project, LISTED + 5)
    response = auth_client.get(f"{CONFIGS}{config.id}/")
    assert response.status_code == 200, response.content
    item = response.json()["result"]
    assert item["run_count"] == LISTED + 5
    assert len(item["runs"]) == LISTED
    assert item["runs"][0]["id"] == str(runs[-1].id)
    # The oldest listed run's detail opens the next older one.
    oldest_listed = item["runs"][-1]["id"]
    assert oldest_listed == str(runs[5].id)
    detail = auth_client.get(f"{RUNS}{oldest_listed}/").json()["result"]
    assert detail["previous_run_id"] == str(runs[4].id)


@pytest.mark.django_db
def test_run_count_leaves_out_deleted_runs(auth_client, observe_project):
    config, runs = report_with_runs(observe_project, 3)
    InsightRun.objects.filter(id=runs[0].id).update(
        deleted=True, deleted_at=timezone.now()
    )
    item = auth_client.get(f"{CONFIGS}{config.id}/").json()["result"]
    assert item["run_count"] == 2
    assert [r["id"] for r in item["runs"]] == [str(runs[2].id), str(runs[1].id)]
