"""Reads stay read-only; a scheduled sweep settles vanished workflows."""

import asyncio
from datetime import timedelta

import pytest
from django.utils import timezone
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from tfc.temporal.common import registry
from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow
from tfc.temporal.eval_tasks.client import WF_ABSENT, WF_CLOSED, WF_PROGRESSING
from tfc.temporal.insights.activities import _sweep_lost_runs
from tfc.temporal.schedules.manager import _build_schedule_for_config
from tfc.temporal.schedules.tracer import TRACER_SCHEDULES
from tracer.models.insights import InsightRun
from tracer.services import insight_lost_runs as lost
from tracer.tests.conftest import observe_project as observe_project
from tracer.tests.test_insights_api import CONFIGS, RUNS, population
from tracer.tests.test_insights_api import insight_enabled as insight_enabled
from tracer.tests.test_insights_lost_runs import stuck_run as stuck_run
from tracer.tests.test_insights_lost_runs import workflow as workflow


@pytest.mark.django_db
def test_insights_gets_neither_describe_nor_settle_runs(
    auth_client, observe_project, stuck_run, workflow,
):
    before = InsightRun.objects.get(id=stuck_run.id).updated_at
    for path, params in (
        (f"{RUNS}{stuck_run.id}/", {}),
        (f"{CONFIGS}{stuck_run.config_id}/", {}),
        (CONFIGS, {"project_id": str(observe_project.id)}),
    ):
        response = auth_client.get(path, params)
        assert response.status_code == 200, response.json()
    assert workflow["asked"] == []
    stuck_run.refresh_from_db()
    assert stuck_run.status == "running"
    assert stuck_run.updated_at == before


@pytest.mark.django_db
@pytest.mark.parametrize("answer", [WF_ABSENT, WF_CLOSED])
def test_sweep_fails_lost_runs_once_and_run_again_can_start(
    auth_client, observe_project, stuck_run, workflow, answer,
):
    workflow["answer"] = answer
    _sweep_lost_runs()
    stuck_run.refresh_from_db()
    assert (stuck_run.status, stuck_run.error) == ("failed", lost.LOST_ERROR)
    finished = stuck_run.finished_at
    _sweep_lost_runs()
    stuck_run.refresh_from_db()
    assert stuck_run.finished_at == finished
    assert workflow["asked"] == [f"insight-run-{stuck_run.id}"]
    response = auth_client.post(RUNS, {
        "project_id": str(observe_project.id),
        "config_id": str(stuck_run.config_id), "population": population(5),
    }, format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["run_id"] != str(stuck_run.id)


@pytest.mark.django_db
@pytest.mark.parametrize("answer", [WF_PROGRESSING, RuntimeError("unreachable")])
def test_sweep_does_not_confuse_running_or_unreachable_workflows_with_lost_ones(
    stuck_run, workflow, answer,
):
    workflow["answer"] = answer
    _sweep_lost_runs()
    stuck_run.refresh_from_db()
    assert stuck_run.status == "running"
    assert len(workflow["asked"]) == 1


@pytest.mark.django_db
def test_sweep_ignores_recent_and_deleted_runs(stuck_run, workflow):
    InsightRun.objects.filter(id=stuck_run.id).update(updated_at=timezone.now())
    _sweep_lost_runs()
    assert workflow["asked"] == []
    InsightRun.objects.filter(id=stuck_run.id).update(
        deleted=True, updated_at=timezone.now() - timedelta(minutes=10),
    )
    _sweep_lost_runs()
    assert workflow["asked"] == []


@pytest.mark.django_db
def test_sweep_does_not_fail_a_run_that_progressed_during_describe(
    stuck_run, monkeypatch,
):
    def progressed(workflow_id):
        InsightRun.objects.filter(id=stuck_run.id).update(updated_at=timezone.now())
        return WF_CLOSED

    monkeypatch.setattr(
        "tfc.temporal.insights.client.describe_insight_run_sync", progressed
    )
    _sweep_lost_runs()
    stuck_run.refresh_from_db()
    assert stuck_run.status == "running"


@pytest.mark.django_db(transaction=True)
def test_scheduled_sweep_runs_through_the_task_runner(stuck_run, workflow, monkeypatch):
    config = next(
        s for s in TRACER_SCHEDULES if s.schedule_id == "sweep-lost-insight-runs"
    )
    activities = {
        activity._Definition.must_from_callable(fn).name: fn
        for fn in registry.get_activities_for_queue(config.queue)
    }
    schedule = _build_schedule_for_config(config)
    env = ActivityEnvironment()

    async def execute(name, payload, **options):
        return await env.run(activities[name], payload)

    monkeypatch.setattr(
        "tfc.temporal.drop_in.workflow.workflow.execute_activity", execute
    )
    result = asyncio.run(TaskRunnerWorkflow().run(schedule.action.args[0]))
    assert result.status == "completed"
    assert result.result == {"result": None, "status": "completed"}
    stuck_run.refresh_from_db()
    assert (stuck_run.status, stuck_run.error) == ("failed", lost.LOST_ERROR)
    assert workflow["asked"] == [f"insight-run-{stuck_run.id}"]
    assert schedule.action.args[0].time_limit == 300
    assert schedule.action.args[0].max_retries == 0


def test_lost_run_sweep_is_scheduled_on_the_insights_worker():
    schedule = next(
        s for s in TRACER_SCHEDULES if s.schedule_id == "sweep-lost-insight-runs"
    )
    assert schedule.activity_name == "sweep_lost_insight_runs"
    assert schedule.queue == "tasks_xl"
    assert schedule.interval_seconds == 60
