"""Bounded cancellation control tests."""

import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services import graph_version_task_adapter as adapter
from model_hub.services.graph_cancellation import cancel_graph_experiment
from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES
from tfc.temporal.agent_playground.tests import test_experiment_run_adapter
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
graph_run_factory = test_experiment_run_adapter.graph_run_factory


class FakeHandle:
    def __init__(self, running=True):
        self.running = running
        self.cancel_calls = 0

    async def describe(self):
        return SimpleNamespace(
            status=SimpleNamespace(name="RUNNING" if self.running else "COMPLETED")
        )

    async def cancel(self):
        self.cancel_calls += 1
        self.running = False


class FakeClient:
    def __init__(self, handle):
        self.handle = handle
        self.ids = []

    def get_workflow_handle(self, workflow_id):
        self.ids.append(workflow_id)
        return self.handle


class BlockingRunner(BaseNodeRunner):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, config, inputs, execution_context):
        self.started.set()
        self.release.wait(15)
        return {
            output: inputs[source] for source, output in config["output_map"].items()
        }


def test_cancel_before_dispatch_is_durable_and_does_not_connect(
    graph_run_factory, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = create()
    monkeypatch.setattr(
        "model_hub.services.graph_cancellation.get_client",
        lambda: pytest.fail("must not connect"),
    )
    result = cancel_graph_experiment(run_id=run.id, **scope)
    assert result.status == "cancelled" and result.requested_workflows == ()


def test_cancel_claimed_workflow_requests_temporal_cancel_and_is_idempotent(
    graph_run_factory, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = create(inputs=({"question": "cancel"},))

    # Create a stable claim and workflow link, but no worker side effect.
    def lost(**kwargs):
        raise adapter.GraphRunIndeterminateError("lost")

    monkeypatch.setattr(adapter, "_default_executor", lost)
    with pytest.raises(adapter.GraphRunIndeterminateError):
        adapter.run_graph_experiment(run_id=run.id, **scope)
    handle = FakeHandle()
    client = FakeClient(handle)

    async def get_client():
        return client

    monkeypatch.setattr("model_hub.services.graph_cancellation.get_client", get_client)
    result = cancel_graph_experiment(run_id=run.id, **scope)
    assert result.status == "cancelled" and result.cancelled_workflows
    assert handle.cancel_calls == 1 and client.ids == [result.requested_workflows[0]]
    replay = cancel_graph_experiment(run_id=run.id, **scope)
    assert replay.status == "cancelled" and replay.requested_workflows == ()
    assert handle.cancel_calls == 1


async def test_cancel_service_requests_real_temporal_workflow_cancel(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = await sync_to_async(create)(inputs=({"question": "cancel me"},))
    runner = BlockingRunner()
    register_runner("test_template", runner)

    async def connected_client():
        return await Client.connect(
            workflow_environment.client.service_client.config.target_host
        )

    monkeypatch.setattr(adapter, "get_client", connected_client)
    monkeypatch.setattr(
        "model_hub.services.graph_cancellation.get_client", connected_client
    )
    queue = f"graph-cancel-control-{uuid.uuid4().hex}"
    client = await connected_client()
    async with Worker(
        client,
        task_queue=queue,
        workflows=[GraphExecutionWorkflow],
        activities=ALL_ACTIVITIES,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        task = asyncio.create_task(
            asyncio.to_thread(
                adapter.run_graph_experiment,
                run_id=run.id,
                task_queue=queue,
                timeout_seconds=30,
                **scope,
            )
        )
        assert await asyncio.to_thread(runner.started.wait, 10)
        control = await asyncio.to_thread(
            cancel_graph_experiment, run_id=run.id, **scope
        )
        runner.release.set()
        result = await asyncio.wait_for(task, 30)
    assert control.cancelled_workflows
    assert result.status == "cancelled"
    item = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(run=run)
    assert item.status == "running" and item.output is None
