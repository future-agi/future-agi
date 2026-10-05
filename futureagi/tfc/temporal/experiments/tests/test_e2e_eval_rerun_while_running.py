"""TH-5259: adding an eval through PUT must preserve in-flight prompt outputs."""

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import CellStatus, OwnerChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentPromptConfig
from model_hub.services.column_service import create_experiment_column
from model_hub.views.experiment_runner import ExperimentRunner
from tfc.temporal.experiments import (
    get_activities,
    get_workflows,
    start_experiment_v2_workflow,
)

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_experiment_e2e")]


class _StubEvalInstance:
    cost = {}
    token_usage = {}

    def run(self, **kwargs):
        return SimpleNamespace(
            eval_results=[{"failure": False, "reason": "stub ok", "metrics": []}]
        )


class _CellRecorder(threading.Thread):
    def __init__(self, dataset_id):
        super().__init__(daemon=True)
        self.dataset_id = dataset_id
        self.stop_flag = threading.Event()
        self.timeline = []
        self.errors = []
        self.last = {}
        self.started = time.monotonic()

    def run(self):
        try:
            while not self.stop_flag.is_set():
                try:
                    for cell in Cell.objects.filter(
                        dataset_id=self.dataset_id, deleted=False
                    ).values(
                        "id",
                        "column__name",
                        "row__order",
                        "status",
                        "value",
                        "value_infos",
                    ):
                        infos = cell["value_infos"] or {}
                        if isinstance(infos, str):
                            infos = json.loads(infos)
                        state = (cell["status"], cell["value"], infos.get("reason", ""))
                        if self.last.get(cell["id"]) != state:
                            self.last[cell["id"]] = state
                            self.timeline.append(
                                (
                                    round(time.monotonic() - self.started, 2),
                                    cell["column__name"],
                                    cell["row__order"],
                                    *state,
                                )
                            )
                except Exception as exc:
                    self.errors.append(str(exc))
                finally:
                    close_old_connections()
                self.stop_flag.wait(0.05)
        finally:
            close_old_connections()


def _make_epc(experiment, prompt_template, prompt_version, model, order):
    edt = ExperimentDatasetTable.objects.create(name=model, experiment=experiment)
    ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        name=model,
        model=model,
        model_display_name=model,
        model_config={},
        model_params={},
        configuration={},
        output_format="string",
        order=order,
        messages=None,
    )
    column, _ = create_experiment_column(
        dataset=experiment.snapshot_dataset,
        source_id=edt.id,
        name=model,
        output_format="string",
        response_format=None,
        status=StatusType.NOT_STARTED.value,
    )
    edt.columns.add(column)
    return column


def _make_template(required_keys):
    return EvalTemplate.no_workspace_objects.create(
        name=f"TH5259-{uuid.uuid4().hex}",
        organization=None,
        owner=OwnerChoices.SYSTEM.value,
        config={
            "required_keys": required_keys,
            "output": "Pass/Fail",
            "eval_type_id": "AgentEvaluator",
            "config": {},
        },
        choices=["Passed", "Failed"],
        output_type_normalized="pass_fail",
        visible_ui=True,
    )


async def _wait_until(check, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"Timed out waiting for {description}")


@pytest.mark.django_db(transaction=True)
async def test_put_add_eval_preserves_inflight_run(
    workflow_environment,
    experiment,
    snapshot_dataset,
    prompt_template,
    prompt_version,
    user,
    workspace,
    organization,
):
    release = threading.Event()
    test_loop = asyncio.get_running_loop()
    client = workflow_environment.client

    def process_row(
        row_id,
        column_id,
        dataset_id,
        experiment_id,
        messages,
        model,
        model_config,
        output_format=None,
        run_prompt_config=None,
    ):
        close_old_connections()
        try:
            if model == "slow-model" and not release.wait(timeout=60):
                raise TimeoutError("Slow model was not released")
            Cell.objects.filter(
                row_id=row_id, column_id=column_id, deleted=False
            ).update(
                value=f"response for {model}",
                status=CellStatus.PASS.value,
            )
            return {"row_id": row_id, "column_id": column_id, "status": "COMPLETED"}
        finally:
            close_old_connections()

    @sync_to_async
    def setup():
        # Import the real URL tree before blocking prompt rows. Cold imports of
        # unrelated voice SDK routes can otherwise exhaust the slow-model guard.
        from django.urls import resolve

        resolve(f"/model-hub/experiments/v2/{experiment.id}/")
        fast = _make_epc(experiment, prompt_template, prompt_version, "fast-model", 0)
        slow = _make_epc(experiment, prompt_template, prompt_version, "slow-model", 1)
        existing_template = _make_template(["output"])
        existing = UserEvalMetric.objects.create(
            name="Existing output eval",
            dataset=snapshot_dataset,
            organization=organization,
            workspace=workspace,
            user=user,
            template=existing_template,
            model="",
            config={
                "mapping": {"output": "output"},
                "config": {},
                "reasonColumn": True,
            },
            status=StatusType.EXPERIMENT_EVALUATION.value,
            source_id=str(experiment.id),
        )
        experiment.user_eval_template_ids.add(existing)
        runner = ExperimentRunner(experiment.id)
        runner.load_experiment()
        runner.empty_or_create_evals_column()
        new_template = _make_template(["input", "output"])
        api_client = WorkspaceAwareAPIClient()
        api_client.force_authenticate(user=user)
        api_client.set_workspace(workspace)
        payload = {
            "user_eval_metrics": [
                {
                    "id": str(existing.id),
                    "name": existing.name,
                    "template_id": str(existing.template_id),
                    "model": "",
                    "config": existing.config,
                },
                {
                    "name": "Added input and output eval",
                    "template_id": str(new_template.id),
                    "model": "",
                    "config": {
                        "mapping": {
                            "input": str(snapshot_dataset._test_columns[0].id),
                            "output": "output",
                        },
                        "config": {},
                    },
                },
            ]
        }
        return fast.id, slow.id, api_client, payload

    fast_id, slow_id, api_client, payload = await setup()
    row_count = len(snapshot_dataset._test_rows)
    recorder = _CellRecorder(snapshot_dataset.id)

    @sync_to_async
    def outputs_inflight():
        return (
            Cell.objects.filter(column_id=fast_id, status=CellStatus.PASS.value).count()
            == row_count
            and Cell.objects.filter(
                column_id=slow_id, status=CellStatus.RUNNING.value
            ).count()
            == row_count
        )

    async def all_closed():
        running_cells = await sync_to_async(
            lambda: Cell.objects.filter(
                dataset=snapshot_dataset, deleted=False, status=CellStatus.RUNNING.value
            ).exists()
        )()
        if (
            running_cells
            or (await main_handle.describe()).status == WorkflowExecutionStatus.RUNNING
        ):
            return False
        query = (
            f'WorkflowId STARTS_WITH "rerun-experiment-cells-{experiment.id}-" '
            "AND ExecutionStatus = 'Running'"
        )
        return not any([info async for info in client.list_workflows(query=query)])

    async def get_client():
        return client

    recorder.start()
    try:
        with (
            patch("tfc.temporal.common.client.get_client", get_client),
            patch(
                "tfc.temporal.common.client._run_async_in_sync_context",
                side_effect=lambda fn: asyncio.run_coroutine_threadsafe(
                    fn(), test_loop
                ).result(timeout=60),
            ),
            patch(
                "tfc.temporal.experiments.activities._process_row_sync",
                side_effect=process_row,
            ),
            patch(
                "model_hub.views.eval_runner.EvaluationRunner._create_eval_instance",
                return_value=_StubEvalInstance(),
            ),
        ):
            async with Worker(
                client,
                task_queue="tasks_l",
                workflows=get_workflows(),
                activities=get_activities(),
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                await sync_to_async(start_experiment_v2_workflow)(
                    experiment_id=str(experiment.id),
                    max_concurrent_rows=10,
                )
                main_workflow_id = f"experiment-{experiment.id}"
                initial_run = await client.get_workflow_handle(
                    main_workflow_id
                ).describe()
                # Pin the original run: a replacement with the same ID must not hide cancellation.
                main_handle = client.get_workflow_handle(
                    main_workflow_id, run_id=initial_run.run_id
                )
                await _wait_until(
                    outputs_inflight, 30, "fast PASS and slow RUNNING outputs"
                )
                response = await sync_to_async(api_client.put)(
                    f"/model-hub/experiments/v2/{experiment.id}/",
                    payload,
                    format="json",
                )
                assert response.status_code == 200, response.data
                await asyncio.sleep(4)
                release.set()
                await _wait_until(
                    all_closed, 90, "all cells and experiment workflows to finish"
                )
                main_status = (await main_handle.describe()).status
                await asyncio.sleep(0.1)
    finally:
        release.set()
        recorder.stop_flag.set()
        recorder.join(timeout=5)
        await sync_to_async(api_client.stop_workspace_injection)()
        print("TH-5259 cell timeline:")
        for event in recorder.timeline:
            print(event)

    @sync_to_async
    def final_cells():
        new_eval = experiment.user_eval_template_ids.get(
            name="Added input and output eval"
        )
        outputs = list(
            Cell.objects.filter(
                column_id__in=[fast_id, slow_id], deleted=False
            ).values_list("status", flat=True)
        )
        evals = list(
            Cell.objects.filter(
                dataset=snapshot_dataset,
                deleted=False,
                column__source=SourceChoices.EXPERIMENT_EVALUATION.value,
                column__source_id__endswith=f"-sourceid-{new_eval.id}",
            ).values_list("status", flat=True)
        )
        return outputs, evals

    outputs, evals = await final_cells()
    failures = []
    if any(
        value == "Execution was stopped by user"
        or reason == "Execution was stopped by user"
        for _, _, _, _, value, reason in recorder.timeline
    ):
        failures.append("Cell received 'Execution was stopped by user'")
    if any(
        "Evaluation not possible on error cell." in str(reason)
        for _, _, _, _, _, reason in recorder.timeline
    ):
        failures.append("Evaluation ran against an error cell")
    if main_status != WorkflowExecutionStatus.COMPLETED:
        failures.append(f"Main workflow ended {main_status.name}")
    if outputs != [CellStatus.PASS.value] * (row_count * 2):
        failures.append(f"Fast/slow outputs did not all pass: {outputs}")
    if evals != [CellStatus.PASS.value] * (row_count * 2):
        failures.append(f"Added per-EDT evals did not pass on both prompts: {evals}")
    assert not recorder.errors, recorder.errors
    assert not failures, "\n".join(failures)
