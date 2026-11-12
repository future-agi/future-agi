"""Real Temporal: Stop and Resume by run ID drive only the named run's workflow.

Same harness as test_stop_resume_api_preserves_cells_and_sibling (a loopback
Temporal server, the real ProcessPromptV2Workflow and the run-control
activities); only row generation is a stand-in. The IDs sent are the results
catalog's run IDs, as the new experiment page sends them.
"""

import asyncio
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.develop_dataset import Row
from model_hub.services import experiment_run_control as control_service
from model_hub.tests.test_experiment_run_control import PersistedRowStandin, call
from model_hub.tests.test_experiment_run_control_run_ids import native_experiment
from model_hub.views.experiment_run_control import (
    ExperimentResumeV2View,
    ExperimentRunControlView,
)
from model_hub.views.experiments import ExperimentStopV2View
from tfc.temporal.experiments.run_control_activities import (
    bind_experiment_run_activity,
    persist_experiment_run_state_activity,
)
from tfc.temporal.experiments.types import ProcessPromptV2WorkflowInput
from tfc.temporal.experiments.workflows import ProcessPromptV2Workflow


@pytest.fixture(autouse=True)
def run_control_on(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True


def native_rows(organization, workspace, user, prompt_version):
    world, targets = native_experiment(organization, workspace, user, prompt_version)
    rows = [
        Row.objects.create(dataset=world["dataset"], order=100 + index)
        for index in range(3)
    ]
    columns = [target.variant.columns.get() for target in targets]
    return world, targets, rows, columns


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stop_and_resume_by_run_id_drive_only_the_named_workflow(
    user, workspace, organization, prompt_version, monkeypatch
):
    world, targets, rows, columns = await sync_to_async(native_rows)(
        organization, workspace, user, prompt_version
    )
    experiment = world["experiment"]
    first, second = targets
    provider = PersistedRowStandin(
        rows,
        [
            (target.variant, target.config, column)
            for target, column in zip(targets, columns)
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:

        async def client():
            return await Client.connect(env.client.service_client.config.target_host)

        monkeypatch.setattr(control_service, "get_client", client)
        queue = f"run-control-ids-{uuid4()}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflow_runner=UnsandboxedWorkflowRunner(),
            workflows=[ProcessPromptV2Workflow],
            activities=[
                bind_experiment_run_activity,
                persist_experiment_run_state_activity,
                provider.setup,
                provider.row,
                provider.check,
            ],
        ):
            try:
                handles = []
                for target in targets:
                    handles.append(
                        await env.client.start_workflow(
                            ProcessPromptV2Workflow.run,
                            ProcessPromptV2WorkflowInput(
                                experiment_id=str(experiment.id),
                                dataset_id=str(experiment.dataset_id),
                                experiment_prompt_config_id=str(target.config.id),
                                max_concurrent_rows=1,
                            ),
                            id=f"fixture-{target.config.id}",
                            task_queue=queue,
                        )
                    )
                await asyncio.wait_for(provider.entered.wait(), 20)

                async def request(view, data=None, method="post"):
                    return await sync_to_async(call)(
                        view, method, experiment.id, user, workspace, data
                    )

                named = {"run_ids": [str(first.run.id)]}
                response = await request(ExperimentStopV2View, named)
                assert response.status_code == 202, response.data
                assert response.data["result"]["runs"] == [
                    {
                        "run_id": str(first.run.id),
                        "name": first.variant.name,
                        "status": "STOPPING",
                    }
                ]
                # The sibling run is not named and finishes every row.
                await handles[1].result()
                provider.release.set()
                for _ in range(100):
                    result = (
                        await request(ExperimentRunControlView, method="get")
                    ).data["result"]
                    states = {run["run_id"]: run["status"] for run in result["runs"]}
                    if states[str(first.variant.id)] == "STOPPED":
                        break
                    await asyncio.sleep(0.05)
                assert states[str(first.variant.id)] == "STOPPED"
                assert states[str(second.variant.id)] == "COMPLETED"
                stopped_column = str(columns[0].id)
                assert [
                    row for column, row in provider.writes if column == stopped_column
                ] == [str(rows[0].id), str(rows[1].id)]
                response = await request(ExperimentResumeV2View, named)
                assert response.status_code == 202, response.data
                assert [run["run_id"] for run in response.data["result"]["runs"]] == [
                    str(first.run.id)
                ]
                await handles[0].result()
                assert len(provider.writes) == len(set(provider.writes)) == 6
            finally:
                provider.release.set()
