"""Worker interruption receipts and opt-in calls through the real LLM runner.

The live test uses an already installed local OpenAI-compatible model. It never
downloads a model or treats exported in-memory spans as Observe ingestion.
"""

import asyncio
import os
import threading
import uuid
from urllib.parse import urlparse

import pytest
from asgiref.sync import sync_to_async
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agent_playground.models import GraphExecution, NodeExecution
from agent_playground.models.choices import GraphExecutionStatus
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRunItem
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services import graph_version_task_adapter as adapter
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import transition_run
from model_hub.services.graph_result_recovery import recover_graph_run_item
from tfc.constants.levels import Level
from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES
from tfc.temporal.agent_playground.tests import test_experiment_run_adapter
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
graph_run_factory = test_experiment_run_adapter.graph_run_factory


async def _client(env, monkeypatch, tracer):
    async def connect():
        return await Client.connect(
            env.client.service_client.config.target_host,
            interceptors=[TracingInterceptor(tracer=tracer)],
        )

    monkeypatch.setattr(adapter, "get_client", connect)
    return await connect()


def _worker(client, queue):
    return Worker(
        client,
        task_queue=queue,
        workflows=[GraphExecutionWorkflow],
        activities=ALL_ACTIVITIES,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


class BlockingRunner(BaseNodeRunner):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, config, inputs, execution_context):
        self.calls += 1
        self.started.set()
        if not self.release.wait(15):
            raise TimeoutError("test runner was not released")
        return {
            output: inputs[source] for source, output in config["output_map"].items()
        }


async def test_cancellation_during_real_activity_rejects_late_result(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = await sync_to_async(create)()
    runner = BlockingRunner()
    register_runner("test_template", runner)
    client = await _client(workflow_environment, monkeypatch, NoOpTracer())
    queue = f"graph-cancel-{uuid.uuid4().hex}"
    async with _worker(client, queue):
        task = asyncio.create_task(
            asyncio.to_thread(
                adapter.run_graph_experiment,
                run_id=run.id,
                task_queue=queue,
                timeout_seconds=30,
                **scope,
            )
        )
        try:
            assert await asyncio.to_thread(runner.started.wait, 10)
            await sync_to_async(transition_run)(
                run_id=run.id, status="cancelled", **scope
            )
        finally:
            runner.release.set()
        result = await asyncio.wait_for(task, 30)
    assert result.status == "cancelled"
    items = await sync_to_async(list)(
        ExperimentRunItem.no_workspace_objects.filter(run=run)
    )
    assert len(items) == 1
    assert items[0].status == "running" and items[0].output is None
    # Cancellation stops the experiment plan; it does not cancel graph effects.
    assert runner.calls == 2
    execution = await sync_to_async(GraphExecution.no_workspace_objects.get)()
    assert execution.status == GraphExecutionStatus.SUCCESS


async def test_queued_graph_survives_adapter_timeout_without_duplicate_dispatch(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = await sync_to_async(create)(inputs=({"question": "delayed worker"},))
    client = await _client(workflow_environment, monkeypatch, NoOpTracer())
    queue = f"graph-late-worker-{uuid.uuid4().hex}"
    with pytest.raises(adapter.GraphRunIndeterminateError, match="timed out"):
        await asyncio.to_thread(
            adapter.run_graph_experiment,
            run_id=run.id,
            task_queue=queue,
            timeout_seconds=0.2,
            **scope,
        )
    item = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(run=run)
    workflow_id = item.metadata["_futureagi_run_write"]["graph_execution"][
        "workflow_id"
    ]
    async with _worker(client, queue):
        completed = await asyncio.wait_for(
            client.get_workflow_handle(workflow_id).result(), 30
        )
    assert completed["status"] == "SUCCESS"
    with pytest.raises(adapter.GraphRunIndeterminateError, match="already claimed"):
        await asyncio.to_thread(
            adapter.run_graph_experiment,
            run_id=run.id,
            task_queue=queue,
            timeout_seconds=10,
            **scope,
        )
    assert await sync_to_async(GraphExecution.no_workspace_objects.count)() == 1
    assert await sync_to_async(NodeExecution.no_workspace_objects.count)() == 2
    await sync_to_async(item.refresh_from_db)()
    assert item.status == "running" and item.output is None


async def test_completed_graph_result_has_explicit_no_dispatch_recovery(
    graph_run_factory, workflow_environment, monkeypatch
):
    create, _, scope = graph_run_factory
    run, _ = await sync_to_async(create)(inputs=({"question": "recover me"},))
    client = await _client(workflow_environment, monkeypatch, NoOpTracer())
    queue = f"graph-recovery-{uuid.uuid4().hex}"
    with pytest.raises(adapter.GraphRunIndeterminateError, match="timed out"):
        await asyncio.to_thread(
            adapter.run_graph_experiment,
            run_id=run.id,
            task_queue=queue,
            timeout_seconds=0.2,
            **scope,
        )
    item = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(run=run)
    workflow_id = item.metadata["_futureagi_run_write"]["graph_execution"][
        "workflow_id"
    ]
    async with _worker(client, queue):
        completed = await asyncio.wait_for(
            client.get_workflow_handle(workflow_id).result(), 30
        )
    assert completed["status"] == "SUCCESS"
    recovered = await asyncio.to_thread(
        recover_graph_run_item, item_id=item.id, **scope
    )
    replay = await asyncio.to_thread(recover_graph_run_item, item_id=item.id, **scope)
    assert recovered.id == replay.id == item.id
    assert recovered.status == replay.status == "completed"
    assert recovered.output == {"answer": "recover me"}
    assert all(ref["status"] == "not_assessed" for ref in recovered.score_refs)
    assert await sync_to_async(GraphExecution.no_workspace_objects.count)() == 1


@pytest.mark.live_llm
@pytest.mark.parametrize("recover_after_timeout", [False, True])
@pytest.mark.skipif(
    not os.getenv("GRAPH_RUNTIME_MODEL"),
    reason="set GRAPH_RUNTIME_MODEL for local model receipt",
)
async def test_real_two_node_llm_graph_persists_output_and_trace_context(
    workflow_environment,
    graph_builder,
    organization,
    workspace,
    user,
    register_llm_runner,
    monkeypatch,
    recover_after_timeout,
):
    from traceai_litellm import LiteLLMInstrumentor

    model = os.environ["GRAPH_RUNTIME_MODEL"]
    endpoint = os.environ.get("GRAPH_RUNTIME_MODEL_URL", "http://127.0.0.1:11434/v1")
    assert urlparse(endpoint).hostname in {"127.0.0.1", "localhost", "::1"}

    @sync_to_async
    def seed():
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).update(level=Level.ADMIN)
        WorkspaceMembership.no_workspace_objects.update_or_create(
            user=user,
            workspace=workspace,
            defaults={"level": Level.WORKSPACE_ADMIN, "role": "workspace_admin"},
        )
        CustomAIModel.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            user_model_id=model,
            provider="openai",
            input_token_cost=0,
            output_token_cost=0,
            key_config={
                "api_base": endpoint,
                "api_key": "local-model-no-secret",
                "timeout": 30,
            },
        )
        first = graph_builder.add_node(
            "Draft", "llm_prompt", inputs=["question"], outputs=["response"]
        )
        second = graph_builder.add_node(
            "Review", "llm_prompt", inputs=["draft"], outputs=["response"]
        )
        graph_builder.add_edge(first, "response", second, "draft")
        version = graph_builder.build()
        for name, prompt in (
            ("Draft", "Answer this question in one short sentence: {{question}}"),
            ("Review", "Repeat the following sentence without commentary: {{draft}}"),
        ):
            template = PromptTemplate.no_workspace_objects.create(
                name=f"runtime-{name}",
                organization=organization,
                workspace=workspace,
            )
            prompt_version = PromptVersion.no_workspace_objects.create(
                original_template=template,
                template_version="1",
                prompt_config_snapshot={
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": prompt}]}
                    ],
                    "configuration": {
                        "model": model,
                        "model_detail": {"type": "chat"},
                        "temperature": 0,
                        "max_tokens": 48,
                    },
                },
            )
            PromptTemplateNode.no_workspace_objects.create(
                node=version.nodes.get(name=name),
                prompt_template=template,
                prompt_version=prompt_version,
            )
        dataset = Dataset.no_workspace_objects.create(
            name="Local model receipt",
            organization=organization,
            workspace=workspace,
            user=user,
            column_order=[],
            column_config={},
        )
        snapshot = DatasetVersion.no_workspace_objects.create(dataset=dataset)
        example = DatasetVersionItem.no_workspace_objects.create(
            dataset_version=snapshot,
            input={"question": "What is two plus two?"},
            expected=None,
        )
        return create_experiment_run(
            run_id=uuid.uuid4(),
            dataset_version=snapshot,
            created_by=user,
            task_kind="graph",
            agent_version=version,
            expected_items=[(example.example_id, 1)],
        )

    run = await seed()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("graph-live-model-receipt")
    instrumentor = LiteLLMInstrumentor()
    instrumentor.instrument(tracer_provider=provider)
    client = await _client(workflow_environment, monkeypatch, tracer)
    queue = f"graph-model-{uuid.uuid4().hex}"
    scope = {"organization_id": organization.id, "workspace_id": workspace.id}
    try:
        if recover_after_timeout:
            with pytest.raises(adapter.GraphRunIndeterminateError, match="timed out"):
                await asyncio.to_thread(
                    adapter.run_graph_experiment,
                    run_id=run.id,
                    task_queue=queue,
                    timeout_seconds=0.2,
                    tracer=tracer,
                    **scope,
                )
            claimed = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(
                run=run
            )
        async with _worker(client, queue):
            if recover_after_timeout:
                workflow_id = claimed.metadata["_futureagi_run_write"][
                    "graph_execution"
                ]["workflow_id"]
                await asyncio.wait_for(
                    client.get_workflow_handle(workflow_id).result(), 90
                )
                await asyncio.to_thread(
                    recover_graph_run_item, item_id=claimed.id, **scope
                )
                await asyncio.to_thread(
                    recover_graph_run_item, item_id=claimed.id, **scope
                )
            result = await asyncio.to_thread(
                adapter.run_graph_experiment,
                run_id=run.id,
                task_queue=queue,
                timeout_seconds=90,
                tracer=tracer,
                **scope,
            )
            replay = await asyncio.to_thread(
                adapter.run_graph_experiment,
                run_id=run.id,
                task_queue=queue,
                timeout_seconds=90,
                tracer=tracer,
                **scope,
            )
        assert result.status == replay.status == "completed"
        item = await sync_to_async(ExperimentRunItem.no_workspace_objects.get)(run=run)
        assert (
            isinstance(item.output["response"], str) and item.output["response"].strip()
        )
        assert await sync_to_async(NodeExecution.no_workspace_objects.count)() == 2
        spans = exporter.get_finished_spans()
        roots = [
            span for span in spans if span.name == "futureagi.experiment.graph_task"
        ]
        llm_spans = [
            span
            for span in spans
            if span.attributes.get("llm.model_name")
            or span.attributes.get("gen_ai.request.model")
        ]
        assert len(roots) == 1 and len(llm_spans) == 2
        assert item.trace_id == f"{roots[0].context.trace_id:032x}"
        assert item.root_span_id == f"{roots[0].context.span_id:016x}"
        assert all(
            span.context.trace_id == roots[0].context.trace_id for span in llm_spans
        )
        assert all(span.parent is not None for span in llm_spans)
        print(
            {
                "model": model,
                "graph_nodes": 2,
                "output": item.output,
                "trace_id": item.trace_id,
                "root_span_id": item.root_span_id,
                "llm_spans": len(llm_spans),
                "observe_ingestion": "not exercised",
                "recovered_after_timeout": recover_after_timeout,
            }
        )
    finally:
        instrumentor.uninstrument()
        provider.shutdown()
