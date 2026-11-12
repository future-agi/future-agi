"""The classic Stop action must not silently become a resumable pause."""

from uuid import uuid4

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiments import ExperimentStopV2View


@pytest.mark.django_db
@pytest.mark.parametrize("status", [StatusType.RUNNING.value, StatusType.QUEUED.value])
@pytest.mark.parametrize("bound_runs", [1, 2])
def test_empty_body_still_cancels_all_work_with_control_bindings(
    user, workspace, organization, monkeypatch, status, bound_runs
):
    experiment, _, variants = seed(organization, workspace, user)
    experiment.status = status
    experiment.save(update_fields=["status"])
    for run, config, _ in variants[:bound_runs]:
        ExperimentRunControl.objects.create(
            experiment_dataset=run,
            config_id=config.id,
            workflow_id=f"control-{config.id}",
            workflow_run_id=uuid4(),
        )
    cancelled = []

    def cancel(experiment_id):
        cancelled.append(experiment_id)
        return {"main_cancelled": True, "rerun_cancelled": True}

    def cooperative(*args, **kwargs):
        pytest.fail("empty-body classic Stop entered cooperative control")

    monkeypatch.setattr(
        "tfc.temporal.experiments.cancel_all_experiment_workflows", cancel
    )
    monkeypatch.setattr(
        "model_hub.services.experiment_run_control._execute", cooperative
    )
    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": True,
    }
    assert cancelled == [str(experiment.id)]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value
    assert (
        call(ExperimentStopV2View, "post", experiment.id, user, workspace).status_code
        == 200
    )
    assert cancelled == [str(experiment.id)]
