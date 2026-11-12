"""Stop stops a running experiment whatever the run-control setting.

The new experiment page's Stop first names the active runs (selected-run
control). With EXPERIMENT_RUN_CONTROL_ENABLED off that request answers 404
``run_control_not_enabled`` and changes nothing; the page then sends the
empty-body stop, which cancels the experiment's workflows. These tests pin
the server half of that sequence: the refusal is side-effect free, the
empty-body stop cancels the main and rerun workflows and marks the
experiment cancelled in both settings, it never answers 404 for a running
experiment the caller may control, and with run control on the run-id stop
keeps its control path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowExecutionStatus

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiments import ExperimentStopV2View

CODE = "run_control_not_enabled"
CONTROL = "model_hub.views.experiment_run_control.run_control"
COOPERATIVE = "model_hub.services.experiment_run_control.get_client"
CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"
EXPERIMENT_CLIENT = "tfc.temporal.experiments.client.get_client"
ACTIVE = [StatusType.RUNNING.value, StatusType.QUEUED.value]


def running(organization, workspace, user, status):
    experiment, _, variants = seed(organization, workspace, user)
    experiment.status = status
    experiment.save(update_fields=["status"])
    return experiment, variants


def selected(variants):
    return {"run_ids": [str(variants[0][0].id)]}


def state(experiment):
    experiment.refresh_from_db()
    return (
        experiment.status,
        sorted(
            ExperimentRunControl.objects.filter(
                experiment_dataset__experiment=experiment
            ).values_list("id", "phase")
        ),
        sorted(
            Cell.objects.filter(dataset=experiment.dataset).values_list(
                "id", "status", "value"
            )
        ),
    )


def record_cancels(monkeypatch):
    cancelled = []

    def cancel(experiment_id):
        cancelled.append(experiment_id)
        return {"main_cancelled": True, "rerun_cancelled": 1}

    monkeypatch.setattr(CANCEL, cancel)
    return cancelled


def refuse_cooperative(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("the empty-body stop entered selected-run control")

    monkeypatch.setattr(COOPERATIVE, refuse)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ACTIVE)
def test_off_the_page_sequence_stops_a_running_experiment_once(
    user, workspace, organization, settings, monkeypatch, status
):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False
    experiment, variants = running(organization, workspace, user, status)
    cancelled = record_cancels(monkeypatch)
    refuse_cooperative(monkeypatch)
    before = state(experiment)

    # First request: the run-id stop. Refused with the code the page reads,
    # before any lookup, workflow call or write.
    first = call(
        ExperimentStopV2View,
        "post",
        experiment.id,
        user,
        workspace,
        selected(variants),
    )
    assert first.status_code == 404, first.data
    assert first.data["code"] == CODE, first.data
    assert cancelled == []
    assert state(experiment) == before

    # Second request, sent only on that code: the empty-body stop.
    second = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert second.status_code == 200, second.data
    assert second.data["result"]["message"] == "Experiment stopped."
    assert second.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": 1,
    }
    assert cancelled == [str(experiment.id)]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


@pytest.mark.django_db
@pytest.mark.parametrize("enabled", [False, True])
def test_empty_body_stop_cancels_the_main_and_rerun_workflows(
    user, workspace, organization, settings, monkeypatch, enabled
):
    """The cancel reaches Temporal for the experiment's own workflow and every
    running rerun workflow, whatever the setting."""
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = enabled
    experiment, _ = running(organization, workspace, user, StatusType.RUNNING.value)
    refuse_cooperative(monkeypatch)
    rerun_id = f"rerun-experiment-cells-{experiment.id}-attempt"
    cancelled = []
    queries = []

    def handle(workflow_id, **kwargs):
        async def cancel(**kwargs):
            cancelled.append(workflow_id)

        return SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
            ),
            cancel=cancel,
        )

    def list_workflows(query=None, **kwargs):
        queries.append(query)

        async def listed():
            yield SimpleNamespace(id=rerun_id)

        return listed()

    client = SimpleNamespace(
        get_workflow_handle=Mock(side_effect=handle),
        list_workflows=list_workflows,
    )
    monkeypatch.setattr(EXPERIMENT_CLIENT, AsyncMock(return_value=client))

    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)

    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": 1,
    }
    assert cancelled == [f"experiment-{experiment.id}", rerun_id]
    assert len(queries) == 1
    assert f"rerun-experiment-cells-{experiment.id}-" in queries[0]
    assert "Running" in queries[0]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


@pytest.mark.django_db
@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("enabled", [False, True])
def test_stop_never_answers_404_for_a_running_experiment_the_caller_controls(
    user, workspace, organization, settings, monkeypatch, enabled, status
):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = enabled
    experiment, _ = running(organization, workspace, user, status)
    cancelled = record_cancels(monkeypatch)
    refuse_cooperative(monkeypatch)

    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)

    assert response.status_code != 404, response.data
    assert response.status_code == 200, response.data
    assert cancelled == [str(experiment.id)]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


@pytest.mark.django_db
def test_on_the_run_id_stop_keeps_the_control_path(
    user, workspace, organization, settings, monkeypatch
):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True
    experiment, variants = running(
        organization, workspace, user, StatusType.RUNNING.value
    )
    calls = []

    def control(request, experiment_id, run_ids=None, *, stopped=None):
        calls.append((str(experiment_id), run_ids, stopped))
        return {"experiment_id": str(experiment_id), "runs": []}

    monkeypatch.setattr(CONTROL, control)
    monkeypatch.setattr(CANCEL, lambda *args: pytest.fail("whole-experiment cancel"))

    response = call(
        ExperimentStopV2View,
        "post",
        experiment.id,
        user,
        workspace,
        selected(variants),
    )

    assert response.status_code == 202, response.data
    assert calls == [(str(experiment.id), [variants[0][0].id], True)]
