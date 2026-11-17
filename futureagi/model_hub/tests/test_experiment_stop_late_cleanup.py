"""A rerun asked for after a Stop runs, even when the stopped run's own
cleanup lands late; and the Stop's rerun reach reports what it cannot know.

- The stopped workflow's own cleanup used to write Cancelled whenever it
  finished. When it landed after a new rerun request (which sets the
  experiment Running) and before that rerun's first step, the first step
  found the experiment Cancelled and refused the new rerun. The cleanup now
  never writes over Queued or Running.
- The rerun named by the experiment's records is not known stopped when its
  records cannot be read, or when the service answers its describe with an
  error other than not-found.
- Only the rerun workflow asks its first step to refuse a stopped
  experiment; the experiment's own workflow never does (adding evaluations
  after a Stop must still run them).

Real cleanup, first step and cancel helper; only the workflow service is a
stand-in.
"""

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.tests.test_experiment_stop_rerun_race import (
    WorkflowService,
    admitted_rerun,
    main_id,
)
from model_hub.views.experiments import ExperimentStopV2View
from tfc.temporal.experiments import activities
from tfc.temporal.experiments import client as experiments_client

pytestmark = pytest.mark.django_db

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"


@pytest.fixture(autouse=True)
def run_control_off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


@pytest.fixture(autouse=True)
def worker_connections(monkeypatch):
    # The worker steps manage their own connections; the test owns this one.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)


@pytest.fixture
def experiment(organization, workspace, user):
    experiment, _, _ = seed(organization, workspace, user)
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    experiment.refresh_from_db()
    return experiment


def status_of(experiment):
    return ExperimentsTable.no_workspace_objects.get(pk=experiment.pk).status


# A rerun asked for after a Stop ------------------------------------------------


@pytest.mark.parametrize("asked", [StatusType.RUNNING.value, StatusType.QUEUED.value])
def test_a_rerun_asked_for_after_a_stop_runs_despite_a_late_cleanup(
    experiment, user, workspace, monkeypatch, asked
):
    monkeypatch.setattr(
        CANCEL,
        lambda _id: {
            "main_cancelled": True,
            "rerun_cancelled": 0,
            "main_confirmed": True,
            "reruns_confirmed": True,
        },
    )
    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200, response.data
    assert status_of(experiment) == StatusType.CANCELLED.value

    # A new rerun is asked for: the request sets the experiment Running (the
    # rerun views) or Queued (a staged submission) before its workflow starts.
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(status=asked)
    # The stopped workflow's own cleanup lands now.
    result = activities._stop_experiment_cleanup_sync(
        str(experiment.id), "Experiment stopped by user", manage_connections=False
    )
    assert result["status"] == "COMPLETED"
    assert status_of(experiment) == asked

    # The new rerun's first step: it runs.
    step = activities._mark_experiment_running_sync(
        str(experiment.id), refuse_if_stopped=True
    )
    assert step["status"] == "READY"
    assert status_of(experiment) == StatusType.RUNNING.value


def test_the_cleanup_still_writes_cancelled_after_the_stop(experiment):
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.CANCELLED.value
    )
    activities._stop_experiment_cleanup_sync(
        str(experiment.id), "Experiment stopped by user", manage_connections=False
    )
    assert status_of(experiment) == StatusType.CANCELLED.value


# What the Stop's reach cannot know ------------------------------------------------


def cancel_with(monkeypatch, service, experiment):
    monkeypatch.setattr(
        experiments_client, "get_client", AsyncMock(return_value=service)
    )
    return experiments_client.cancel_all_experiment_workflows(str(experiment.id))


def test_unreadable_records_leave_the_reruns_not_known_stopped(experiment, monkeypatch):
    admitted_rerun(experiment)
    service = WorkflowService({main_id(experiment): "absent"})
    with patch(
        "model_hub.services.experiment_submit.state_of",
        side_effect=RuntimeError("records unavailable"),
    ):
        result = cancel_with(monkeypatch, service, experiment)
    assert result["reruns_confirmed"] is False
    assert result["main_confirmed"] is True


class FailingDescribe(WorkflowService):
    """The rerun's describe fails with a service error other than not-found."""

    def get_workflow_handle(self, workflow_id, run_id=None):
        from temporalio.service import RPCError, RPCStatusCode

        handle = super().get_workflow_handle(workflow_id, run_id=run_id)
        if workflow_id == self.failing:

            async def describe(**kwargs):
                self.described.append(workflow_id)
                raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

            handle = SimpleNamespace(describe=describe, cancel=handle.cancel)
        return handle


def test_a_rerun_describe_error_other_than_not_found_is_not_known_stopped(
    experiment, monkeypatch
):
    rerun = admitted_rerun(experiment)
    service = FailingDescribe({main_id(experiment): "absent"})
    service.failing = rerun

    result = cancel_with(monkeypatch, service, experiment)

    assert rerun in service.described
    assert service.cancelled == []
    assert result["reruns_confirmed"] is False


# Which workflow asks its first step to refuse ----------------------------------------


def mark_inputs(workflow_class):
    """Keyword arguments of every MarkExperimentRunningInput(...) in run()."""
    tree = ast.parse(inspect.getsource(workflow_class))
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "MarkExperimentRunningInput"
        ):
            found.append(
                {
                    kw.arg: ast.literal_eval(kw.value)
                    for kw in node.keywords
                    if kw.arg == "refuse_if_stopped"
                }
            )
    return found


def test_only_the_rerun_workflow_asks_to_refuse_a_stopped_experiment():
    from tfc.temporal.experiments.workflows import (
        RerunCellsV2Workflow,
        RunExperimentV2Workflow,
    )

    assert mark_inputs(RerunCellsV2Workflow) == [{"refuse_if_stopped": True}]
    main = mark_inputs(RunExperimentV2Workflow)
    assert main and all(kwargs == {} for kwargs in main)
