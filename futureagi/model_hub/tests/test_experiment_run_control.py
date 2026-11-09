"""Scoped API + real Temporal + persisted cells; only row generation is a STANDIN."""

import asyncio
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from rest_framework.test import APIRequestFactory, force_authenticate
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.services import experiment_run_control as control_service
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
    # ON arm: the original F06 assertions run with the default-OFF gate opened.
    # OFF refusals are in test_experiment_run_control_gate.py.
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True


def seed(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Run control fixture",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    experiment = ExperimentsTable.objects.create(
        name="Run control", dataset=dataset, snapshot_dataset=dataset, user=user
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(3)]
    variants = []
    for name in ("Selected", "Sibling"):
        run = ExperimentDatasetTable.objects.create(name=name, experiment=experiment)
        config = ExperimentPromptConfig.objects.create(
            experiment_dataset=run, name=name, model="loopback-standin"
        )
        column = Column.objects.create(
            name=name, dataset=dataset, data_type="text", source="experiment"
        )
        run.columns.add(column)
        for row in rows:
            Cell.objects.create(
                dataset=dataset,
                row=row,
                column=column,
                value="",
                status=CellStatus.RUNNING.value,
            )
        variants.append((run, config, column))
    return experiment, rows, variants


def call(view, method, experiment_id, user, workspace, data=None):
    factory = APIRequestFactory()
    request = getattr(factory, method)(
        "/model-hub/experiments/v2/", data or {}, format="json"
    )
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = workspace
    return view.as_view()(request, experiment_id=experiment_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view,method",
    [
        (ExperimentStopV2View, "post"),
        (ExperimentResumeV2View, "post"),
        (ExperimentRunControlView, "get"),
    ],
)
def test_other_workspace_and_organization_refused_before_temporal(
    user, workspace, organization, monkeypatch, view, method
):
    from accounts.models import Organization, Workspace

    other_org = Organization.objects.create(name="Other synthetic organization")
    other_workspace = Workspace.objects.create(
        name="Other scope", organization=organization, created_by=user, is_default=False
    )
    experiment, _, variants = seed(organization, other_workspace, user)
    response = call(view, method, experiment.id, user, workspace)
    assert response.status_code == 404
    experiment.dataset.organization = other_org
    experiment.dataset.save(update_fields=["organization"])
    response = call(view, method, experiment.id, user, other_workspace)
    assert response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data",
    [
        {"run_ids": []},
        {"run_ids": None},
        {"run_ids": ["not-a-uuid"]},
        {"unknown": True},
    ],
)
def test_invalid_selection_never_becomes_whole_experiment(
    user, workspace, organization, data
):
    experiment, _, _ = seed(organization, workspace, user)
    assert (
        call(
            ExperimentStopV2View, "post", experiment.id, user, workspace, data
        ).status_code
        == 400
    )


@pytest.mark.django_db
@pytest.mark.parametrize("view", [ExperimentStopV2View, ExperimentResumeV2View])
def test_mixed_selection_refused_before_any_signal(user, workspace, organization, view):
    experiment, _, variants = seed(organization, workspace, user)
    response = call(
        view,
        "post",
        experiment.id,
        user,
        workspace,
        {"run_ids": [str(variants[0][0].id), str(uuid4())]},
    )
    assert response.status_code == 404


class PersistedRowStandin:
    def __init__(self, rows, variants):
        self.rows = [str(row.id) for row in rows]
        self.columns = {
            str(config.id): str(column.id) for _, config, column in variants
        }
        self.selected = str(variants[0][2].id)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.writes = []

    @activity.defn(name="setup_prompt_v2_activity")
    async def setup(self, data: dict) -> dict:
        return {
            "status": "READY",
            "column_id": self.columns[data["experiment_prompt_config_id"]],
            "row_ids": self.rows,
            "model_name": "loopback-standin",
            "messages": [],
        }

    @activity.defn(name="process_row_activity")
    async def row(self, data: dict) -> dict:
        if data["column_id"] == self.selected and data["row_id"] == self.rows[1]:
            self.entered.set()
            await self.release.wait()
        await sync_to_async(
            Cell.objects.filter(
                row_id=data["row_id"], column_id=data["column_id"]
            ).update
        )(value="standin-result", status=CellStatus.PASS.value)
        self.writes.append((data["column_id"], data["row_id"]))
        return {"status": "COMPLETED"}

    @activity.defn(name="check_column_status_activity")
    async def check(self, data: dict) -> dict:
        return {"status": "COMPLETED"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stop_resume_api_preserves_cells_and_sibling(
    user, workspace, organization, monkeypatch
):
    experiment, rows, variants = await sync_to_async(seed)(
        organization, workspace, user
    )
    provider = PersistedRowStandin(rows, variants)
    async with await WorkflowEnvironment.start_local() as env:
        # Real clients connect to the same loopback Temporal server on each loop.
        async def client():
            return await Client.connect(env.client.service_client.config.target_host)

        monkeypatch.setattr(control_service, "get_client", client)
        queue = f"run-control-{uuid4()}"
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
            handles = []
            for _, config, _ in variants:
                handles.append(
                    await env.client.start_workflow(
                        ProcessPromptV2Workflow.run,
                        ProcessPromptV2WorkflowInput(
                            experiment_id=str(experiment.id),
                            dataset_id=str(experiment.dataset_id),
                            experiment_prompt_config_id=str(config.id),
                            max_concurrent_rows=1,
                        ),
                        id=f"fixture-{config.id}",
                        task_queue=queue,
                    )
                )
            await asyncio.wait_for(provider.entered.wait(), 20)

            async def request(view, data=None, method="post"):
                return await sync_to_async(call)(
                    view, method, experiment.id, user, workspace, data
                )

            selected = {"run_ids": [str(variants[0][0].id)]}
            response = await request(ExperimentStopV2View, selected)
            assert response.status_code == 202, response.data
            assert response.data["result"]["runs"][0]["status"] == "STOPPING"
            assert (await request(ExperimentStopV2View, selected)).status_code == 202
            await handles[1].result()
            provider.release.set()
            for _ in range(100):
                result = (await request(ExperimentRunControlView, method="get")).data[
                    "result"
                ]
                states = {run["run_id"]: run["status"] for run in result["runs"]}
                if states[str(variants[0][0].id)] == "STOPPED":
                    break
                await asyncio.sleep(0.05)
            assert states[str(variants[0][0].id)] == "STOPPED"
            assert states[str(variants[1][0].id)] == "COMPLETED"
            values = await sync_to_async(list)(
                Cell.objects.filter(column=variants[0][2])
                .order_by("row__order")
                .values_list("value", flat=True)
            )
            assert values == ["standin-result", "standin-result", ""]
            await sync_to_async(experiment.refresh_from_db)()
            assert experiment.status == StatusType.CANCELLED.value
            assert (await request(ExperimentResumeV2View, {})).status_code == 202
            assert (await request(ExperimentResumeV2View, selected)).status_code == 202
            await handles[0].result()
            assert len(provider.writes) == len(set(provider.writes)) == 6
            values = await sync_to_async(list)(
                Cell.objects.filter(column=variants[0][2]).values_list(
                    "value", flat=True
                )
            )
            assert values == ["standin-result"] * 3


@pytest.mark.django_db
def test_legacy_whole_stop_preserves_response_and_is_idempotent(
    user, workspace, organization, monkeypatch
):
    experiment, _, _ = seed(organization, workspace, user)
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    cancelled = []

    def cancel(experiment_id):
        cancelled.append(experiment_id)
        return {"main_cancelled": True, "rerun_cancelled": False}

    monkeypatch.setattr(
        "tfc.temporal.experiments.cancel_all_experiment_workflows", cancel
    )
    response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": False,
    }
    assert (
        call(ExperimentStopV2View, "post", experiment.id, user, workspace).status_code
        == 200
    )
    assert cancelled == [str(experiment.id)]
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value
