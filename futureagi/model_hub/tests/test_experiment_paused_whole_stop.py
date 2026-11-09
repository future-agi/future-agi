"""Whole-experiment cancellation must reach a cooperatively paused parent."""

from uuid import uuid4

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiments import ExperimentStopV2View


@pytest.mark.django_db
def test_whole_stop_cancels_parent_when_cooperative_pause_marked_cancelled(
    user, workspace, organization, monkeypatch
):
    experiment, _, variants = seed(organization, workspace, user)
    experiment.status = StatusType.CANCELLED.value
    experiment.save(update_fields=["status"])
    run, config, _ = variants[0]
    ExperimentRunControl.objects.create(
        experiment_dataset=run,
        config_id=config.id,
        workflow_id=f"control-{config.id}",
        workflow_run_id=uuid4(),
        phase="STOPPED",
    )
    cancelled = []

    def cancel(experiment_id):
        cancelled.append(experiment_id)
        return {"main_cancelled": True, "rerun_cancelled": 1}

    monkeypatch.setattr(
        "tfc.temporal.experiments.cancel_all_experiment_workflows", cancel
    )
    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200
    assert cancelled == [str(experiment.id)], "paused parent was not cancelled"
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": 1,
    }
