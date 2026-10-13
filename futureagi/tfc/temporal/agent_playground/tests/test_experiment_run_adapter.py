"""Persist graph results through a real Temporal worker and the run contract."""

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections, transaction
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agent_playground.models import GraphExecution
from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import RunWriteError, transition_run
from model_hub.services.graph_version_task_adapter import (
    GraphExecutionResult,
    GraphRunIndeterminateError,
    run_graph_experiment,
)
from tfc.constants.levels import Level
from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def graph_run_factory(organization, workspace, user, graph_builder):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.ADMIN)
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={"level": Level.WORKSPACE_ADMIN, "role": "workspace_admin"},
    )
    first = graph_builder.add_node(
        "Retrieve", inputs=["question"], outputs=["document"]
    )
    second = graph_builder.add_node("Answer", inputs=["context"], outputs=["answer"])
    graph_builder.add_edge(first, "document", second, "context")
    graph_version = graph_builder.build()
    dataset = Dataset.no_workspace_objects.create(
        name="Graph run dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)

    def create(inputs=({"question": "one"}, {"question": "two"}), trials=(1,)):
        examples = [
            DatasetVersionItem.no_workspace_objects.create(
                dataset_version=version,
                input=value,
                expected={
                    "answer": value.get("question")
                    if isinstance(value, dict)
                    else value
                },
            )
            for value in inputs
        ]
        run = create_experiment_run(
            run_id=uuid.uuid4(),
            dataset_version=version,
            created_by=user,
            task_kind="graph",
            agent_version=graph_version,
            expected_items=[
                (item.example_id, trial) for item in examples for trial in trials
            ],
        )
        return run, examples

    return (
        create,
        graph_version,
        {
            "organization_id": organization.id,
            "workspace_id": workspace.id,
        },
    )


class RecordingRunner(BaseNodeRunner):
    def __init__(self, tracer, fail=False):
        self.tracer = tracer
        self.fail = fail
        self.calls = []

    def run(self, config, inputs, execution_context):
        self.calls.append((inputs, execution_context))
        with self.tracer.start_as_current_span("test.graph.node"):
            if self.fail:
                raise RuntimeError("deterministic graph failure")
            return {
                output: inputs[source]
                for source, output in config["output_map"].items()
            }


async def _run_worker(env, run, scope, monkeypatch, tracer, fail=False):
    from model_hub.services import graph_version_task_adapter as adapter

    async def connected_client():
        return await Client.connect(
            env.client.service_client.config.target_host,
            interceptors=[TracingInterceptor(tracer=tracer)],
        )

    monkeypatch.setattr(adapter, "get_client", connected_client)
    runner = RecordingRunner(tracer, fail=fail)
    register_runner("test_template", runner)
    worker_client = await connected_client()
    queue = f"graph-run-{uuid.uuid4().hex}"
    async with Worker(
        worker_client,
        task_queue=queue,
        workflows=[GraphExecutionWorkflow],
        activities=ALL_ACTIVITIES,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await asyncio.to_thread(
            run_graph_experiment,
            run_id=run.id,
            task_queue=queue,
            tracer=tracer,
            timeout_seconds=30,
            **scope,
        )
        replay = await asyncio.to_thread(
            run_graph_experiment,
            run_id=run.id,
            task_queue=queue,
            tracer=tracer,
            timeout_seconds=30,
            **scope,
        )
    await sync_to_async(close_old_connections)()
    return result, replay, runner


async def test_real_worker_maps_frozen_cases_trials_and_exports_child_spans(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, version, scope = graph_run_factory
    run, examples = await sync_to_async(create)(trials=(1, 2))
    # Inactive historical versions must run exactly as pinned.
    version.status = "inactive"
    await sync_to_async(version.save)(update_fields=["status"])
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("graph-adapter-test")
    result, replay, runner = await _run_worker(
        workflow_environment, run, scope, monkeypatch, tracer
    )
    assert result.status == replay.status == "completed"
    assert len(runner.calls) == 8  # two graph nodes, four case/trials, no replay
    items = await sync_to_async(list)(
        ExperimentRunItem.no_workspace_objects.filter(run=run)
    )
    assert len(items) == 4
    assert {item.trial_number for item in items} == {1, 2}
    roots = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "futureagi.experiment.graph_task"
    ]
    children = [
        span for span in exporter.get_finished_spans() if span.name == "test.graph.node"
    ]
    assert len(roots) == 4 and len(children) == 8
    by_trace = {f"{span.context.trace_id:032x}": span for span in roots}
    assert all(span.parent is None for span in roots)
    for item in items:
        assert item.output == {"answer": item.input["question"]}
        assert item.trace_id in by_trace
        assert item.root_span_id == f"{by_trace[item.trace_id].context.span_id:016x}"
        assert item.metadata["_futureagi_run_write"]["graph_execution"][
            "workflow_id"
        ].startswith("graph-run-")
    assert {span.context.trace_id for span in children} == {
        span.context.trace_id for span in roots
    }
    assert all(span.parent is not None for span in children)
    assert (
        await sync_to_async(
            GraphExecution.no_workspace_objects.filter(graph_version=version).count
        )()
        == 4
    )
    provider.shutdown()


async def test_real_worker_failure_is_terminal_and_noop_tracing_stays_null(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = await sync_to_async(create)(inputs=({"question": "fail"},))
    result, replay, runner = await _run_worker(
        workflow_environment, run, scope, monkeypatch, NoOpTracer(), fail=True
    )
    assert result.status == replay.status == "failed"
    assert len(runner.calls) == 1
    item = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(run=run)
    assert item.status == "failed"
    assert item.trace_id is item.root_span_id is None
    assert item.error["type"] == "graph_execution"


def test_timeout_retains_claim_and_second_invocation_never_dispatches(
    graph_run_factory, monkeypatch
):
    from model_hub.services import graph_version_task_adapter as adapter

    create, _, scope = graph_run_factory
    run, _ = create(inputs=({"question": "wait"},))
    calls = []

    def timeout(**kwargs):
        calls.append(kwargs)
        raise GraphRunIndeterminateError("timeout")

    monkeypatch.setattr(adapter, "_default_executor", timeout)
    with pytest.raises(GraphRunIndeterminateError, match="timeout"):
        run_graph_experiment(run_id=run.id, **scope)
    with pytest.raises(GraphRunIndeterminateError, match="claimed"):
        run_graph_experiment(run_id=run.id, **scope)
    assert len(calls) == 1
    assert ExperimentRunItem.no_workspace_objects.get(run=run).status == "running"
    run.refresh_from_db()
    assert run.completed_at is None


def test_cancellation_during_graph_prevents_result_and_remaining_cases(
    graph_run_factory, monkeypatch
):
    from model_hub.services import graph_version_task_adapter as adapter

    create, _, scope = graph_run_factory
    run, _ = create()
    calls = []

    def cancel(**kwargs):
        calls.append(kwargs)
        transition_run(run_id=run.id, status="cancelled", **scope)
        return GraphExecutionResult("SUCCESS", {"answer": "late"})

    monkeypatch.setattr(adapter, "_default_executor", cancel)
    assert run_graph_experiment(run_id=run.id, **scope).status == "cancelled"
    assert len(calls) == 1
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    assert item.status == "running" and item.output is None


@pytest.mark.parametrize("value", [{"other": 1}, {}, "scalar"])
def test_bad_inputs_fail_before_graph_dispatch(graph_run_factory, monkeypatch, value):
    from model_hub.services import graph_version_task_adapter as adapter

    create, _, scope = graph_run_factory
    run, _ = create(inputs=(value,))
    monkeypatch.setattr(
        adapter, "_default_executor", lambda **kwargs: pytest.fail("must not dispatch")
    )
    assert run_graph_experiment(run_id=run.id, **scope).status == "failed"
    assert GraphExecution.no_workspace_objects.count() == 0


def test_no_caller_transaction_and_scoped_graph_version(graph_run_factory):
    create, version, scope = graph_run_factory
    run, _ = create()
    with transaction.atomic(), pytest.raises(RunWriteError, match="outside"):
        run_graph_experiment(run_id=run.id, **scope)
    version.deleted = True
    version.save(update_fields=["deleted"])
    with pytest.raises(RunWriteError, match="pinned graph"):
        run_graph_experiment(run_id=run.id, **scope)
    assert not ExperimentRunItem.no_workspace_objects.filter(run=run).exists()
