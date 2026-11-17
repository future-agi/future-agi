"""Selected-run controls with real native results, usage rows and reservations.

Reuse the native Stop fixtures and the run-control suite's loopback Temporal
worker. Provider replies and evaluator transport are replaced; admission,
workflow checkpoints, row activities, guarded_completion and storage are real.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import ExperimentPromptConfig, ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import credit_ledger_port as route_port
from model_hub.services import experiment_run_control as control_service
from model_hub.services.experiment_run_control_gate import run_control_enabled
from model_hub.services.experiment_submit import state_of
from model_hub.tests.test_experiment_run_control import call
from model_hub.tests.test_experiment_stop_default_path import running
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    create,
    create_body,
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.views.experiment_run_control import (
    ExperimentResumeV2View,
    ExperimentRunControlView,
)
from model_hub.views.experiments import ExperimentStopV2View
from tfc.temporal.experiments import activities
from tfc.temporal.experiments.provider_policy import guarded_completion
from tfc.temporal.experiments.run_control_activities import (
    bind_experiment_run_activity,
    persist_experiment_run_state_activity,
)
from tfc.temporal.experiments.types import (
    ProcessPromptV2WorkflowInput,
    ProcessRowInput,
    ProcessRowOutput,
    SetupPromptV2Input,
    SetupPromptV2Output,
)
from tfc.temporal.experiments.workflows import ProcessPromptV2Workflow
from tfc.utils.default_on import _overridden

pytestmark = pytest.mark.django_db(transaction=True)
SETTING = "EXPERIMENT_RUN_CONTROL_ENABLED"
USAGE = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


@pytest.fixture(autouse=True, params=[None, " \t "], ids=["unset", "blank"])
def default_run_control(request, monkeypatch, settings):
    if request.param is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, request.param)
    # Enable the separate create-time limits surface, not run control.
    settings.EXPERIMENT_SCOPE_ENABLED = True
    assert not _overridden(SETTING)
    assert run_control_enabled() is True


def native_world(tenants):
    actor = tenants["a"]
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    for order in (1, 2):
        row = Row.no_workspace_objects.create(dataset=source, order=order)
        Cell.no_workspace_objects.create(
            dataset=source, column=source_column, row=row, value=f"row {order}"
        )
    body = create_body(actor)
    body["limits"] = {"timeout_s": 3600}
    response, experiment, _, _ = create(actor, body)
    assert response.status_code == 200, response.content
    running(experiment)
    config = ExperimentPromptConfig.no_workspace_objects.get(
        experiment_dataset__experiment=experiment
    )
    context = state_of(experiment)["workflow_args"]["native_runs"][str(config.id)]
    binding = NativeRunBinding.objects.select_related("variant", "snapshot").get(
        run_id=context["run_id"]
    )
    return actor, experiment, config, context, binding


class NativeActivities:
    """Record scheduling without replacing the setup or row implementation."""

    def __init__(self):
        self.prepared = None
        self.queued = []

    @activity.defn(name="setup_prompt_v2_activity")
    async def setup(self, data: SetupPromptV2Input) -> SetupPromptV2Output:
        self.prepared = await activities.setup_prompt_v2_activity(data)
        return self.prepared

    @activity.defn(name="process_row_activity")
    async def row(self, data: ProcessRowInput) -> ProcessRowOutput:
        self.queued.append(data.row_id)
        return await activities.process_row_activity(data)


class ProviderReplies:
    """Pause the second reply after the real guard has reserved its row."""

    def __init__(self, missing_usage=False):
        self.missing_usage = missing_usage
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def completion(self, **payload):
        self.calls += 1
        index = self.calls
        if index == 2:
            self.entered.set()
            assert self.release.wait(60), "second provider reply was not released"
        reply = {"choices": [{"message": {"content": f"result-{index}"}}]}
        if not (self.missing_usage and index == 2):
            reply["usage"] = dict(USAGE)
        return reply

    def response(self, provider, *args, **kwargs):
        # The reply adapter still enters the production reserve/settle path.
        reply = guarded_completion(
            {
                "model": provider.model,
                "messages": provider.messages,
                "max_tokens": 64,
            }
        )
        return reply["choices"][0]["message"]["content"], {
            "metadata": {"usage": reply.get("usage")}
        }


def budget_rows(experiment, prepared, execution_id):
    return list(
        ExperimentRowBudget.objects.filter(
            experiment=experiment,
            variant=f"prompt:{prepared.column_id}:{execution_id}",
        )
        .order_by("id")
        .values()
    )


def completed_snapshot(experiment, binding, row_ids, execution_id, prepared):
    examples = list(
        binding.snapshot.examples.filter(snapshot_row_id__in=row_ids).values_list(
            "version_item__example_id", flat=True
        )
    )
    return {
        "cells": list(
            Cell.no_workspace_objects.filter(
                dataset_id=experiment.snapshot_dataset_id, row_id__in=row_ids
            )
            .order_by("id")
            .values()
        ),
        "items": list(
            ExperimentRunItem.no_workspace_objects.filter(
                run_id=binding.run_id, example_id__in=examples
            )
            .order_by("id")
            .values()
        ),
        "usage_rows": [
            row
            for row in budget_rows(experiment, prepared, execution_id)
            if str(row["row_id"]) in row_ids
        ],
    }


@asynccontextmanager
async def stopped_selected_run(tenants, monkeypatch, *, missing_usage=False):
    actor, experiment, config, context, binding = await sync_to_async(native_world)(
        tenants
    )
    recorded = NativeActivities()
    replies = ProviderReplies(missing_usage)
    monkeypatch.setattr(
        route_port,
        "resolve_payer_route",
        lambda **_: route_port.PayerRoute.byok("openai"),
    )
    monkeypatch.setattr("litellm.completion", replies.completion)

    # Patch the class method with a function so the provider instance is retained.
    def respond(provider, *args, **kwargs):
        return replies.response(provider, *args, **kwargs)

    monkeypatch.setattr(PROVIDER, respond)

    def no_whole_stop(*args, **kwargs):
        pytest.fail("selected-run stop reached whole-experiment cancellation")

    monkeypatch.setattr(
        "tfc.temporal.experiments.cancel_all_experiment_workflows", no_whole_stop
    )

    async def request(view, *, method="post"):
        return await sync_to_async(call)(
            view,
            method,
            experiment.id,
            actor["user"],
            actor["workspace"],
            {"run_ids": [str(binding.run_id)]} if method == "post" else None,
        )

    async with await WorkflowEnvironment.start_local() as env:

        async def client():
            return await Client.connect(env.client.service_client.config.target_host)

        monkeypatch.setattr(control_service, "get_client", client)
        queue = f"selected-persistence-{uuid4()}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflow_runner=UnsandboxedWorkflowRunner(),
            workflows=[ProcessPromptV2Workflow],
            activities=[
                bind_experiment_run_activity,
                persist_experiment_run_state_activity,
                recorded.setup,
                recorded.row,
                activities.check_column_status_activity,
                activities.check_experiment_dataset_status_activity,
            ],
        ):
            handle = await env.client.start_workflow(
                ProcessPromptV2Workflow.run,
                ProcessPromptV2WorkflowInput(
                    experiment_id=str(experiment.id),
                    dataset_id=str(experiment.snapshot_dataset_id),
                    experiment_prompt_config_id=str(config.id),
                    max_concurrent_rows=1,
                    native_context=context,
                ),
                id=f"selected-persistence-{uuid4()}",
                task_queue=queue,
            )
            try:
                assert await asyncio.to_thread(replies.entered.wait, 30)
                prepared = recorded.prepared
                assert prepared is not None and len(prepared.row_ids) == 3
                execution = await sync_to_async(ExperimentRunControl.objects.get)(
                    config_id=config.id, experiment_dataset_id=binding.variant_id
                )
                execution_id = str(execution.workflow_run_id)
                reserved = await sync_to_async(budget_rows)(
                    experiment, prepared, execution_id
                )
                assert len(reserved) == 2
                assert [
                    str(row["row_id"]) for row in reserved if row["reservations"]
                ] == [prepared.row_ids[1]]
                completed_before = await sync_to_async(completed_snapshot)(
                    experiment, binding, prepared.row_ids[:1], execution_id, prepared
                )
                assert len(completed_before["items"]) == 1
                assert completed_before["items"][0]["status"] == "completed"
                assert completed_before["items"][0]["token_usage"] == USAGE
                assert len(completed_before["usage_rows"]) == 1
                assert completed_before["usage_rows"][0]["usage"]["tokens"] == 10
                response = await request(ExperimentStopV2View)
                assert response.status_code == 202, response.data
                assert response.data["result"]["runs"] == [
                    {
                        "run_id": str(binding.run_id),
                        "name": binding.variant.name,
                        "status": "STOPPING",
                    }
                ]
                replies.release.set()

                async def wait_stopped():
                    while True:
                        status = await request(ExperimentRunControlView, method="get")
                        assert status.status_code == 200, status.data
                        phases = {
                            row["run_id"]: row["status"]
                            for row in status.data["result"]["runs"]
                        }
                        if phases[str(binding.variant_id)] == "STOPPED":
                            return
                        await asyncio.sleep(0.05)

                await asyncio.wait_for(wait_stopped(), 30)
                await sync_to_async(execution.refresh_from_db)()
                assert execution.phase == "STOPPED"
                assert recorded.queued == prepared.row_ids[:2]
                completed_after = await sync_to_async(completed_snapshot)(
                    experiment, binding, prepared.row_ids[:1], execution_id, prepared
                )
                assert completed_after == completed_before
                if missing_usage:
                    stopped_rows = await sync_to_async(budget_rows)(
                        experiment, prepared, execution_id
                    )
                    before = next(row for row in reserved if row["reservations"])
                    after = next(
                        row for row in stopped_rows if row["id"] == before["id"]
                    )
                    assert after["exhausted"] is True
                    assert after["usage"] == before["usage"]
                yield (
                    experiment,
                    binding,
                    execution_id,
                    recorded,
                    replies,
                    handle,
                    request,
                )
            finally:
                replies.release.set()
                description = await handle.describe()
                if description.status == WorkflowExecutionStatus.RUNNING:
                    await handle.cancel()
                    try:
                        await asyncio.wait_for(handle.result(), 30)
                    except WorkflowFailureError:
                        pass


@pytest.mark.asyncio
async def test_selected_resume_keeps_completed_results_and_usage_rows(
    tenants, monkeypatch
):
    async with stopped_selected_run(tenants, monkeypatch) as state:
        experiment, binding, execution_id, recorded, replies, handle, request = state
        prepared = recorded.prepared
        completed = prepared.row_ids[:2]
        unfinished = prepared.row_ids[2:]
        before = await sync_to_async(completed_snapshot)(
            experiment, binding, completed, execution_id, prepared
        )
        assert len(before["items"]) == len(before["usage_rows"]) == 2
        assert all(item["status"] == "completed" for item in before["items"])
        assert all(item["token_usage"] == USAGE for item in before["items"])
        outputs = [
            cell
            for cell in before["cells"]
            if str(cell["column_id"]) == prepared.column_id
        ]
        assert len(outputs) == 2
        assert all(cell["status"] == CellStatus.PASS.value for cell in outputs)
        assert all(row["usage"]["tokens"] == 10 for row in before["usage_rows"])
        assert all(row["reservations"] == {} for row in before["usage_rows"])
        queued_before = len(recorded.queued)
        response = await request(ExperimentResumeV2View)
        assert response.status_code == 202, response.data
        assert [row["run_id"] for row in response.data["result"]["runs"]] == [
            str(binding.run_id)
        ]
        result = await asyncio.wait_for(handle.result(), 30)
        assert result.status == "COMPLETED"
        assert recorded.queued[queued_before:] == unfinished
        assert recorded.queued == prepared.row_ids
        assert replies.calls == 3
        after = await sync_to_async(completed_snapshot)(
            experiment, binding, completed, execution_id, prepared
        )
        # Full row equality includes IDs, usage, timestamps and every result field.
        assert after == before
        assert (
            await sync_to_async(
                ExperimentRunItem.no_workspace_objects.filter(
                    run_id=binding.run_id
                ).count
            )()
            == 3
        )
        all_usage = await sync_to_async(budget_rows)(experiment, prepared, execution_id)
        assert len(all_usage) == 3
        assert {str(row["row_id"]) for row in all_usage} == set(prepared.row_ids)
        assert all(row["reservations"] == {} for row in all_usage)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_usage", [False, True], ids=["reported", "unreported"])
async def test_selected_stop_leaves_no_reserved_rows(
    tenants, monkeypatch, missing_usage
):
    async with stopped_selected_run(
        tenants, monkeypatch, missing_usage=missing_usage
    ) as state:
        experiment, binding, execution_id, recorded, replies, _, _ = state
        rows = await sync_to_async(budget_rows)(
            experiment, recorded.prepared, execution_id
        )
        assert len(rows) == 2
        assert replies.calls == 2
        assert {str(row["row_id"]) for row in rows} == set(
            recorded.prepared.row_ids[:2]
        )
        assert (
            await sync_to_async(
                ExperimentRunItem.no_workspace_objects.filter(
                    run_id=binding.run_id, status__in=["pending", "running"]
                ).exists
            )()
            is False
        )
        assert all(row["reservations"] == {} for row in rows), [
            {"id": str(row["id"]), "reserved": sorted(row["reservations"])}
            for row in rows
        ]
