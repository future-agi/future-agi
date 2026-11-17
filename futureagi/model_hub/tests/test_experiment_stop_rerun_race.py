"""A Stop also stops a rerun that started just before it.

The Stop finds running reruns with the workflow service's search, which lags:
a rerun started seconds before the Stop can be missed. The Stop then reported
every workflow cancelled and ended the rerun's runs, though the rerun itself
kept going: its first step set the experiment back to Running, and its rows
were sent and charged after a Stop the page reported done.

Now:
- the rerun the experiment's own records name (its admitted workflow ID,
  rerun-experiment-cells-<experiment>-<admission> for a native rerun) is
  reached by that ID, whether or not the search sees it; if it cannot be
  reached, the Stop does not report the reruns stopped;
- a rerun's first step never turns a stopped experiment back to Running: it
  sends nothing and ends as a cancelled rerun does. A rerun asked for after
  a Stop still runs (the request sets the experiment Running first);
- the Stop leaves the experiment Cancelled even when a rerun's first step
  marks it Running between the Stop's read and its write.

The Stop goes through the real Stop view and the real cancel helper; only the
workflow service is a stand-in. The rerun workflow is the real one, with its
activities replaced.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_submit import STATE_KEY
from model_hub.tests.test_experiment_run_control import call, seed
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


class WorkflowService:
    """The workflow service as the Stop sees it. `answers` gives each
    workflow's state by ID (a status name, "absent" or "unreachable");
    `search` is what the search for running reruns returns."""

    def __init__(self, answers, search=()):
        self.answers = answers
        self.search = list(search)
        self.cancelled = []
        self.described = []

    def get_workflow_handle(self, workflow_id, run_id=None):
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.service import RPCError, RPCStatusCode

        async def describe(**kwargs):
            self.described.append(workflow_id)
            answer = self.answers.get(workflow_id, "absent")
            if answer == "absent":
                raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
            if answer == "unreachable":
                raise RuntimeError("connection refused")
            return SimpleNamespace(status=getattr(WorkflowExecutionStatus, answer))

        async def cancel():
            self.cancelled.append(workflow_id)

        return SimpleNamespace(describe=describe, cancel=cancel)

    def list_workflows(self, query):
        found = [SimpleNamespace(id=workflow_id) for workflow_id in self.search]

        class Search:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not found:
                    raise StopAsyncIteration
                return found.pop(0)

        return Search()


@pytest.fixture
def experiment(organization, workspace, user):
    experiment, _, _ = seed(organization, workspace, user)
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    experiment.refresh_from_db()
    return experiment


def admitted_rerun(experiment):
    """The experiment's records name an admitted native rerun, as
    admit_submission leaves them."""
    admission_id = str(uuid.uuid4())
    workflow_id = f"rerun-experiment-cells-{experiment.id}-{admission_id}"
    snapshot = Dataset.no_workspace_objects.get(pk=experiment.snapshot_dataset_id)
    snapshot.dataset_config = {
        **(snapshot.dataset_config or {}),
        STATE_KEY: {
            "admission_id": admission_id,
            "state": "accepted",
            "workflow_id": workflow_id,
            "execution_plan": {"operation": "subset"},
            "workflow_args": {"native_runs": {}},
        },
    }
    snapshot.save(update_fields=["dataset_config"])
    return workflow_id


def main_id(experiment):
    return f"experiment-{experiment.id}"


def cancel_with(monkeypatch, service, experiment):
    monkeypatch.setattr(
        experiments_client, "get_client", AsyncMock(return_value=service)
    )
    return experiments_client.cancel_all_experiment_workflows(str(experiment.id))


# The Stop reaches the rerun by its ID -------------------------------------------


def test_a_rerun_the_search_does_not_see_yet_is_cancelled_by_its_id(
    experiment, monkeypatch
):
    rerun = admitted_rerun(experiment)
    service = WorkflowService({main_id(experiment): "absent", rerun: "RUNNING"})

    result = cancel_with(monkeypatch, service, experiment)

    assert service.cancelled == [rerun]
    assert result["rerun_cancelled"] == 1
    assert result["reruns_confirmed"] is True
    assert result["main_confirmed"] is True


def test_a_rerun_that_cannot_be_reached_is_not_reported_stopped(
    experiment, monkeypatch
):
    rerun = admitted_rerun(experiment)
    service = WorkflowService({main_id(experiment): "absent", rerun: "unreachable"})

    result = cancel_with(monkeypatch, service, experiment)

    assert service.cancelled == []
    # Not known stopped: the Stop then ends nothing itself (as when the
    # search fails).
    assert result["reruns_confirmed"] is False


def test_a_rerun_the_search_finds_is_cancelled_once(experiment, monkeypatch):
    rerun = admitted_rerun(experiment)
    service = WorkflowService(
        {main_id(experiment): "absent", rerun: "RUNNING"}, search=[rerun]
    )

    result = cancel_with(monkeypatch, service, experiment)

    assert service.cancelled == [rerun]
    assert result["rerun_cancelled"] == 1
    assert result["reruns_confirmed"] is True


def test_a_rerun_already_finished_is_left_alone(experiment, monkeypatch):
    rerun = admitted_rerun(experiment)
    service = WorkflowService({main_id(experiment): "absent", rerun: "COMPLETED"})

    result = cancel_with(monkeypatch, service, experiment)

    assert service.cancelled == []
    assert result["rerun_cancelled"] == 0
    assert result["reruns_confirmed"] is True


def test_with_no_rerun_on_record_only_the_search_is_used(experiment, monkeypatch):
    service = WorkflowService({main_id(experiment): "RUNNING"})

    result = cancel_with(monkeypatch, service, experiment)

    assert service.described == [main_id(experiment)]
    assert service.cancelled == [main_id(experiment)]
    assert result == {
        "main_cancelled": True,
        "rerun_cancelled": 0,
        "main_confirmed": True,
        "reruns_confirmed": True,
    }


def test_the_stop_reports_the_rerun_it_reached(
    experiment, user, workspace, monkeypatch
):
    rerun = admitted_rerun(experiment)
    service = WorkflowService({main_id(experiment): "absent", rerun: "RUNNING"})
    monkeypatch.setattr(
        experiments_client, "get_client", AsyncMock(return_value=service)
    )

    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)

    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": False,
        "reruns": 1,
    }
    assert service.cancelled == [rerun]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


# The rerun never undoes a Stop -----------------------------------------------------


def mark_rerun_running(experiment):
    """The rerun's first step, as its activity runs it."""
    return activities._mark_experiment_running_sync(
        str(experiment.id), refuse_if_stopped=True
    )


def test_a_reruns_first_step_after_a_stop_leaves_the_experiment_cancelled(
    experiment,
):
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.CANCELLED.value
    )

    result = mark_rerun_running(experiment)

    assert result["status"] == "CANCELLED"
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


def test_a_rerun_asked_for_after_a_stop_still_runs(experiment):
    # Every rerun request sets the experiment Running (or Queued) before its
    # workflow starts, so a Stop that came earlier does not block it.
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.QUEUED.value
    )

    result = mark_rerun_running(experiment)

    assert result["status"] == "READY"
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value


def test_the_activity_carries_the_reruns_refusal(monkeypatch):
    from tfc.temporal.experiments.types import MarkExperimentRunningInput

    seen = {}

    def mark(experiment_id, refuse_if_stopped=False):
        seen.update(experiment_id=experiment_id, refuse_if_stopped=refuse_if_stopped)
        return {
            "experiment_id": experiment_id,
            "dataset_id": "snapshot",
            "eval_template_ids": [],
            "status": "CANCELLED",
        }

    monkeypatch.setattr(activities, "_mark_experiment_running_sync", mark)

    result = asyncio.run(
        activities.mark_experiment_running_activity(
            MarkExperimentRunningInput(experiment_id="stopped", refuse_if_stopped=True)
        )
    )

    assert seen == {"experiment_id": "stopped", "refuse_if_stopped": True}
    assert result.status == "CANCELLED"


def test_adding_evaluations_after_a_stop_still_runs_them(experiment):
    # Adding evaluations starts the experiment's own workflow without setting
    # the experiment Running first; its first step still does, as before.
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.CANCELLED.value
    )

    result = activities._mark_experiment_running_sync(str(experiment.id))

    assert result["status"] == "READY"
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value


def test_the_stop_wins_when_a_rerun_marks_running_while_it_runs(
    experiment, user, workspace, monkeypatch
):
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.QUEUED.value
    )

    def cancel(_experiment_id):
        # The rerun's first step lands between the Stop's read (Queued) and
        # its write.
        activities._mark_experiment_running_sync(str(experiment.id))
        return {
            "main_cancelled": False,
            "rerun_cancelled": 0,
            "main_confirmed": True,
            "reruns_confirmed": True,
        }

    monkeypatch.setattr(CANCEL, cancel)

    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)

    assert response.status_code == 200, response.data
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


def test_a_rerun_whose_first_step_finds_the_experiment_stopped_sends_nothing():
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    experiment_id, dataset_id, config_id, row_id = [str(uuid.uuid4()) for _ in range(4)]
    steps, children = [], []

    async def execute(name, value, **kwargs):
        steps.append(name)
        if name == "mark_experiment_running_activity":
            # The rerun asks its first step to refuse a stopped experiment.
            assert value.refuse_if_stopped is True
            return {"experiment_id": experiment_id, "status": "CANCELLED"}
        return {"status": "COMPLETED"}

    async def start_child(function, value, **kwargs):
        children.append(value)
        raise AssertionError("no row is sent after a Stop")

    rerun = RerunCellsV2WorkflowInput(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        prompt_config_ids=[config_id],
        row_ids=[row_id],
    )
    with (
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_activity",
            side_effect=execute,
        ),
        patch(
            "tfc.temporal.experiments.workflows.workflow.start_child_workflow",
            side_effect=start_child,
        ),
    ):
        result = asyncio.run(RerunCellsV2Workflow().run(rerun))

    assert result.status == "CANCELLED"
    assert result.total_rows_processed == 0
    assert children == []
    # Nothing after its first step but the Stop's own cleanup.
    assert steps == [
        "mark_experiment_running_activity",
        "stop_experiment_cleanup_activity",
    ]
