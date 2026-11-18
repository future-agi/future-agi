"""An Insights run whose Temporal workflow was lost (a Temporal restart) is
marked failed by the periodic sweep, so the report offers Run again instead
of running forever. Reading a report never asks Temporal or changes a run.
Temporal is a stand-in here."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from tfc.temporal.eval_tasks.client import WF_ABSENT, WF_CLOSED, WF_PROGRESSING
from tfc.temporal.insights.activities import _sweep_lost_runs as sweep
from tracer.models.insights import InsightConfig, InsightRun
from tracer.services import insight_lost_runs as lost
from tracer.tests.test_insights_api import CONFIGS, RUNS, population
from tracer.tests.test_insights_api import insight_enabled as insight_enabled


@pytest.fixture
def workflow(monkeypatch):
    """Temporal's answer about a report's workflow, and who asked."""
    state = {"answer": WF_ABSENT, "asked": []}

    def describe(workflow_id):
        state["asked"].append(workflow_id)
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(
        "tfc.temporal.insights.client.describe_insight_run_sync", describe
    )
    return state


@pytest.fixture
def stuck_run(observe_project, organization, workspace, user):
    """A run in its grouping stage that hasn't changed for ten minutes."""
    config = InsightConfig.objects.create(
        project=observe_project,
        organization=organization,
        workspace=workspace,
        created_by=user,
        name="Where is the agent failing?",
        unit="sessions",
        lens="failures",
        question="Where is the agent failing?",
    )
    run = InsightRun.objects.create(
        config=config,
        project=observe_project,
        organization=organization,
        workspace=workspace,
        created_by=user,
        unit="sessions",
        status="running",
        stage="grouping",
        sample_size=10,
    )
    InsightRun.objects.filter(id=run.id).update(
        workflow_id=f"insight-run-{run.id}",
        updated_at=timezone.now() - timedelta(minutes=10),
    )
    return run


def _detail(auth_client, run):
    response = auth_client.get(f"{RUNS}{run.id}/")
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]


@pytest.mark.django_db
@pytest.mark.parametrize("answer", [WF_ABSENT, WF_CLOSED])
def test_a_run_whose_workflow_is_gone_is_marked_failed_once(
    auth_client, stuck_run, workflow, answer
):
    workflow["answer"] = answer
    # Reading is read-only: the run is still running until the sweep.
    assert _detail(auth_client, stuck_run)["status"] == "running"
    assert workflow["asked"] == []
    sweep()
    result = _detail(auth_client, stuck_run)
    assert (result["status"], result["error"]) == ("failed", lost.LOST_ERROR)
    assert result["finished_at"] is not None
    assert workflow["asked"] == [f"insight-run-{stuck_run.id}"]
    # A failed run isn't asked about again.
    sweep()
    assert _detail(auth_client, stuck_run)["status"] == "failed"
    assert len(workflow["asked"]) == 1


@pytest.mark.django_db
def test_a_run_whose_workflow_still_runs_is_left_alone_and_asked_about_once(
    auth_client, stuck_run, workflow
):
    workflow["answer"] = WF_PROGRESSING
    sweep()
    assert _detail(auth_client, stuck_run)["status"] == "running"
    # The answer is kept for a few seconds: back-to-back sweeps ask once.
    sweep()
    assert _detail(auth_client, stuck_run)["status"] == "running"
    assert len(workflow["asked"]) == 1
    assert InsightRun.objects.get(id=stuck_run.id).error == ""


@pytest.mark.django_db
def test_a_run_that_changed_lately_or_an_unreachable_temporal_changes_nothing(
    auth_client, stuck_run, workflow
):
    InsightRun.objects.filter(id=stuck_run.id).update(updated_at=timezone.now())
    sweep()
    assert _detail(auth_client, stuck_run)["status"] == "running"
    assert workflow["asked"] == []

    InsightRun.objects.filter(id=stuck_run.id).update(
        updated_at=timezone.now() - timedelta(minutes=10)
    )
    workflow["answer"] = RuntimeError("Temporal is unreachable")
    sweep()
    assert _detail(auth_client, stuck_run)["status"] == "running"
    assert len(workflow["asked"]) == 1


@pytest.mark.django_db
def test_the_report_and_its_run_list_show_a_lost_run_as_failed(
    auth_client, observe_project, stuck_run, workflow
):
    sweep()
    config = auth_client.get(f"{CONFIGS}{stuck_run.config_id}/").json()["result"]
    assert config["latest_run"]["status"] == "failed"
    assert [r["status"] for r in config["runs"]] == ["failed"]
    listed = auth_client.get(CONFIGS, {"project_id": str(observe_project.id)}).json()
    assert listed["result"]["configs"][0]["latest_run"]["status"] == "failed"


@pytest.mark.django_db
def test_run_again_starts_a_new_run_instead_of_joining_a_lost_one(
    auth_client, observe_project, stuck_run, workflow, insight_enabled
):
    response = auth_client.post(
        RUNS,
        {
            "project_id": str(observe_project.id),
            "config_id": str(stuck_run.config_id),
            "population": population(5),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["result"]["run_id"] != str(stuck_run.id)
    assert InsightRun.objects.get(id=stuck_run.id).status == "failed"
