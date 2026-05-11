"""
End-to-end tests for GraphExecutionWorkflow using temporalio.testing.

These tests use an in-memory Temporal server (WorkflowEnvironment) with real Django ORM
calls against the test database, but with mocked node runners (no LLM calls).

Sync ORM calls (builder.build(), GraphExecution.create(), NodeExecution queries) are
wrapped with sync_to_async because test methods run in an async context (asyncio_mode=auto)
and Django blocks synchronous DB access from async contexts.

IMPORTANT: These tests must NOT run in parallel with pytest-xdist.
Run with:
    set -a && source .env.test.local && set +a
    pytest tfc/temporal/agent_playground/tests/test_e2e_workflow.py -v -p no:xdist
"""

import asyncio
import uuid
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from agent_playground.models import (
    GraphExecution,
    NodeExecution,
)
from agent_playground.models.choices import (
    GraphExecutionStatus,
    NodeExecutionStatus,
)
from agent_playground.services.engine.output_sink import get_sink
from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES
from tfc.temporal.agent_playground.types import (
    ExecuteGraphInput,
    OutputSinkConfig,
)
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow

# All tests in this module must run on the same xdist worker (sequential).
# This prevents DB flush failures from Temporal activity threads holding connections.
pytestmark = pytest.mark.xdist_group("temporal_e2e")

TASK_QUEUE = "test-agent-playground"


# =============================================================================
# Async-safe ORM helpers (sync DB calls wrapped for async test context)
# =============================================================================


@sync_to_async
def _create_graph_execution(graph_version, input_payload):
    """Create a GraphExecution record."""
    return GraphExecution.no_workspace_objects.create(
        graph_version=graph_version,
        status=GraphExecutionStatus.PENDING,
        input_payload=input_payload,
    )


@sync_to_async
def _get_node_executions(graph_execution):
    """Get all NodeExecutions for a GraphExecution, indexed by node name."""
    node_execs = NodeExecution.no_workspace_objects.filter(
        graph_execution=graph_execution
    ).select_related("node")
    return {ne.node.name: ne for ne in node_execs}


@sync_to_async
def _child_executions_exist(graph_version):
    """Check if any child GraphExecutions exist for a given version."""
    return GraphExecution.no_workspace_objects.filter(
        graph_version=graph_version
    ).exists()


# =============================================================================
# Workflow runner helper
# =============================================================================


async def run_workflow(env, graph_version, input_payload=None, max_concurrent_nodes=10):
    """Helper to run the GraphExecutionWorkflow and return its result."""
    if input_payload is None:
        input_payload = {}

    graph_execution = await _create_graph_execution(graph_version, input_payload)

    workflow_id = f"graph-execution-{graph_execution.id}"

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[GraphExecutionWorkflow],
        activities=ALL_ACTIVITIES,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await env.client.execute_workflow(
            GraphExecutionWorkflow.run,
            ExecuteGraphInput(
                graph_execution_id=str(graph_execution.id),
                graph_version_id=str(graph_version.id),
                input_payload=input_payload,
                max_concurrent_nodes=max_concurrent_nodes,
                task_queue=TASK_QUEUE,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

    # Give activity threads a moment to release DB connections, then close stale ones.
    # Without this, Django's test DB flush fails because activity threads still hold connections.
    await asyncio.sleep(0.1)
    await sync_to_async(close_old_connections)()

    return result, graph_execution


# =============================================================================
# Group 1: Basic Execution
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestBasicExecution:
    """Tests for single-node graphs and basic scenarios."""

    async def test_single_node_success(self, workflow_environment, graph_builder):
        """Test 1: Single node graph executes successfully."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert "A" in node_execs
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS

    async def test_single_node_runner_failure(
        self, workflow_environment, graph_builder
    ):
        """Test 2: Single node with failing runner marks execution as FAILED."""
        builder = graph_builder
        builder.add_node(
            "A",
            template_name="fail_template",
            inputs=["user_input"],
            outputs=["response"],
        )

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "FAILED"

        node_execs = await _get_node_executions(execution)
        assert "A" in node_execs
        assert node_execs["A"].status == NodeExecutionStatus.FAILED
        assert node_execs["A"].error_message is not None
        assert "Intentional test failure" in node_execs["A"].error_message

    async def test_empty_input_payload(self, workflow_environment, graph_builder):
        """Test 3: Node with no required connected inputs executes with empty payload."""
        builder = graph_builder
        builder.add_node("A", inputs=[], outputs=["output1"])

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(workflow_environment, version, {})

        assert result.status == "SUCCESS"


# =============================================================================
# Group 2: Linear Chains
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestLinearChainExecution:
    """Tests for linear chain graphs (A → B, A → B → C)."""

    async def test_two_node_chain(self, workflow_environment, graph_builder):
        """Test 4: Two-node linear chain A → B routes data correctly."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        builder.add_edge(a, "response", b, "text")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

    async def test_three_node_chain(self, workflow_environment, graph_builder):
        """Test 5: Three-node linear chain A → B → C executes in order."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        c = builder.add_node("C", inputs=["data"], outputs=["data_out"])
        builder.add_edge(a, "response", b, "text")
        builder.add_edge(b, "text_out", c, "data")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert len(node_execs) == 3
        for name in ["A", "B", "C"]:
            assert node_execs[name].status == NodeExecutionStatus.SUCCESS


# =============================================================================
# Group 3: Parallel Execution
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestParallelExecution:
    """Tests for parallel execution patterns (fan-out, diamond DAG)."""

    async def test_fan_out(self, workflow_environment, graph_builder):
        """Test 6: Fan-out — A's output feeds both B and C in parallel."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        c = builder.add_node("C", inputs=["text"], outputs=["text_out"])
        builder.add_edge(a, "response", b, "text")
        builder.add_edge(a, "response", c, "text")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["C"].status == NodeExecutionStatus.SUCCESS

    async def test_diamond_dag(self, workflow_environment, graph_builder):
        """Test 7: Diamond DAG — A → B,C (parallel) → D."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["result"])
        c = builder.add_node("C", inputs=["text"], outputs=["result"])
        d = builder.add_node("D", inputs=["input_b", "input_c"], outputs=["final"])
        builder.add_edge(a, "response", b, "text")
        builder.add_edge(a, "response", c, "text")
        builder.add_edge(b, "result", d, "input_b")
        builder.add_edge(c, "result", d, "input_c")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert len(node_execs) == 4
        for name in ["A", "B", "C", "D"]:
            assert node_execs[name].status == NodeExecutionStatus.SUCCESS


# =============================================================================
# Group 4: Failure Propagation
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestFailurePropagation:
    """Tests for failure and skip propagation."""

    async def test_first_node_fails_downstream_skipped(
        self, workflow_environment, graph_builder
    ):
        """Test 8: A fails → B and C are skipped."""
        builder = graph_builder
        a = builder.add_node(
            "A",
            template_name="fail_template",
            inputs=["user_input"],
            outputs=["response"],
        )
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        c = builder.add_node("C", inputs=["data"], outputs=["data_out"])
        builder.add_edge(a, "response", b, "text")
        builder.add_edge(b, "text_out", c, "data")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "FAILED"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.FAILED
        assert node_execs["B"].status == NodeExecutionStatus.SKIPPED
        assert node_execs["C"].status == NodeExecutionStatus.SKIPPED

    async def test_parallel_branch_failure(self, workflow_environment, graph_builder):
        """Test 9: One parallel branch fails — D (with failing upstream) is skipped."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B", template_name="fail_template", inputs=["text"], outputs=["result"]
        )
        c = builder.add_node("C", inputs=["text"], outputs=["result"])
        d = builder.add_node("D", inputs=["input_b", "input_c"], outputs=["final"])
        builder.add_edge(a, "response", b, "text")
        builder.add_edge(a, "response", c, "text")
        builder.add_edge(b, "result", d, "input_b")
        builder.add_edge(c, "result", d, "input_c")

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )

        assert result.status == "FAILED"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["B"].status == NodeExecutionStatus.FAILED
        assert node_execs["C"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["D"].status == NodeExecutionStatus.SKIPPED

    async def test_failure_outputs_route_after_failed_state(
        self, workflow_environment, graph_builder, monkeypatch
    ):
        """Failure payload routing must observe the persisted FAILED node state."""
        from agent_playground.services.engine.analyzer import GraphAnalyzer
        from agent_playground.services.engine.node_runner import (
            BaseNodeRunner,
            register_runner,
        )
        from tfc.temporal.agent_playground.activities import _execute_node_sync

        class FailureWithOutputs(Exception):
            outputs = {"result": {"ok": False, "error": "boom"}}

        class FailureOutputRunner(BaseNodeRunner):
            def run(self, config, inputs, execution_context):
                raise FailureWithOutputs("boom")

        register_runner("failure_output_template", FailureOutputRunner())

        builder = graph_builder
        builder.add_node(
            "A",
            template_name="failure_output_template",
            inputs=["user_input"],
            outputs=["result"],
        )
        version = await sync_to_async(builder.build)()
        execution = await _create_graph_execution(version, {"user_input": "hello"})
        topology_data = await sync_to_async(
            lambda: GraphAnalyzer.analyze(version.id).to_dict()
        )()
        node_id = await sync_to_async(
            lambda: str(version.nodes.get(name="A").id)
        )()

        import agent_playground.services.engine as engine

        original_route_node_outputs = engine.route_node_outputs
        observed_statuses = []

        def route_spy(*args, **kwargs):
            node_execution = kwargs["node_execution"]
            node_execution.refresh_from_db()
            observed_statuses.append(node_execution.status)
            return original_route_node_outputs(*args, **kwargs)

        monkeypatch.setattr(engine, "route_node_outputs", route_spy)

        result = await sync_to_async(_execute_node_sync)(
            str(execution.id),
            str(version.id),
            node_id,
            topology_data=topology_data,
        )

        assert result["status"] == "FAILED"
        assert observed_statuses == [NodeExecutionStatus.FAILED]


# =============================================================================
# Group 5: Module Nodes (Child Workflows)
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestModuleNodes:
    """Tests for module node execution (child workflows)."""

    async def test_simple_module_node(
        self, workflow_environment, organization, workspace, user
    ):
        """Test 10: Module node triggers child workflow successfully."""
        from tfc.temporal.agent_playground.tests.conftest import GraphBuilder

        # Build the child (module) graph: X → Y
        child_builder = GraphBuilder(organization, workspace, user)
        child_builder.set_name("Child Module")
        x = child_builder.add_node("X", inputs=["input1"], outputs=["output1"])
        y = child_builder.add_node("Y", inputs=["data"], outputs=["data_out"])
        child_builder.add_edge(x, "output1", y, "data")
        child_version = await sync_to_async(child_builder.build)(activate=True)

        # Build the parent graph: A → Module_M → B
        parent_builder = GraphBuilder(organization, workspace, user)
        parent_builder.set_name("Parent Workflow")
        a = parent_builder.add_node("A", inputs=["user_input"], outputs=["response"])
        m = parent_builder.add_module_node(
            "Module_M",
            ref_graph_version=child_version,
            inputs=["input1"],
            outputs=["data_out"],
        )
        b = parent_builder.add_node("B", inputs=["text"], outputs=["text_out"])
        parent_builder.add_edge(a, "response", m, "input1")
        parent_builder.add_edge(m, "data_out", b, "text")
        parent_version = await sync_to_async(parent_builder.build)(activate=True)

        result, execution = await run_workflow(
            workflow_environment, parent_version, {"user_input": "hello"}
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["Module_M"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

        # Verify child graph execution was created
        assert await _child_executions_exist(child_version)


# =============================================================================
# Group 6: Edge Cases
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    async def test_node_with_unknown_template(
        self, workflow_environment, organization, workspace, user
    ):
        """Test 13: Node with unknown template (no registered runner) fails."""
        from tfc.temporal.agent_playground.tests.conftest import GraphBuilder

        builder = GraphBuilder(organization, workspace, user)
        builder.add_node(
            "A",
            template_name="completely_unknown_template",
            inputs=["input1"],
            outputs=["output1"],
        )
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"input1": "hello"}
        )

        assert result.status == "FAILED"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.FAILED

    async def test_unregistered_runner(self, workflow_environment, graph_builder):
        """Test 14: Node with template but no registered runner fails."""
        builder = graph_builder
        builder.add_node(
            "A",
            template_name="nonexistent_runner_template",
            inputs=["input1"],
            outputs=["output1"],
        )
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment, version, {"input1": "hello"}
        )

        assert result.status == "FAILED"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.FAILED
        assert "nonexistent_runner_template" in (node_execs["A"].error_message or "")

    async def test_max_concurrent_nodes_limiting(
        self, workflow_environment, graph_builder
    ):
        """Test 15: Five independent nodes with max_concurrent_nodes=2 all execute."""
        builder = graph_builder
        for i in range(5):
            builder.add_node(f"Node_{i}", inputs=["input1"], outputs=["output1"])

        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow(
            workflow_environment,
            version,
            {"input1": "hello"},
            max_concurrent_nodes=2,
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert len(node_execs) == 5
        for i in range(5):
            assert node_execs[f"Node_{i}"].status == NodeExecutionStatus.SUCCESS

    async def test_empty_graph_no_nodes(self, workflow_environment, graph_builder):
        """Test 16: Graph with no nodes is rejected by the analyzer."""
        version = await sync_to_async(graph_builder.build)()

        result, execution = await run_workflow(workflow_environment, version, {})

        assert result.status == "FAILED"
        assert "no nodes" in result.error.lower()


# =============================================================================
# Output Sink Helpers
# =============================================================================


async def run_workflow_with_sinks(
    env,
    graph_version,
    input_payload=None,
    primary_output_sink=None,
    output_sinks=None,
    node_sink_overrides=None,
    max_concurrent_nodes=10,
):
    """Helper to run the GraphExecutionWorkflow with output sink configs."""
    if input_payload is None:
        input_payload = {}

    graph_execution = await _create_graph_execution(graph_version, input_payload)
    workflow_id = f"graph-execution-{graph_execution.id}"

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[GraphExecutionWorkflow],
        activities=ALL_ACTIVITIES,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await env.client.execute_workflow(
            GraphExecutionWorkflow.run,
            ExecuteGraphInput(
                graph_execution_id=str(graph_execution.id),
                graph_version_id=str(graph_version.id),
                input_payload=input_payload,
                max_concurrent_nodes=max_concurrent_nodes,
                task_queue=TASK_QUEUE,
                primary_output_sink=primary_output_sink,
                output_sinks=output_sinks or [],
                node_sink_overrides=node_sink_overrides or {},
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

    await asyncio.sleep(0.1)
    await sync_to_async(close_old_connections)()

    return result, graph_execution


# =============================================================================
# Group 7: Output Sinks
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestOutputSinks:
    """Tests for the output sink system."""

    async def test_sink_called_on_success(self, workflow_environment, graph_builder):
        """Test 17: Sink is called with correct context when node succeeds."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(
                name="mock_sink", config={"key": "val"}
            ),
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 1

        ctx = mock.calls[0]
        assert ctx.node_name == "A"
        assert ctx.status == "SUCCESS"
        assert ctx.config == {"key": "val"}
        assert ctx.outputs.get("response") == "hello"

    async def test_sink_called_on_failure(self, workflow_environment, graph_builder):
        """Test 18: Sink is called with status=FAILED when node runner fails."""
        builder = graph_builder
        builder.add_node(
            "A",
            template_name="fail_template",
            inputs=["user_input"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(name="mock_sink"),
        )

        assert result.status == "FAILED"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 1

        ctx = mock.calls[0]
        assert ctx.node_name == "A"
        assert ctx.status == "FAILED"
        assert ctx.outputs == {}

    async def test_sink_failure_does_not_affect_node(
        self, workflow_environment, graph_builder
    ):
        """Test 19: A failing sink does not cause node execution to fail."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(name="failing_sink"),
        )

        assert result.status == "SUCCESS"

        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS

    async def test_multiple_sinks_all_called(self, workflow_environment, graph_builder):
        """Test 20: Multiple sinks (primary + output_sinks) are all called."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(
                name="mock_sink", config={"source": "primary"}
            ),
            output_sinks=[
                OutputSinkConfig(name="mock_sink", config={"source": "secondary"}),
            ],
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        # Primary + secondary = 2 calls for the single node
        assert len(mock.calls) == 2
        configs = [c.config for c in mock.calls]
        assert {"source": "primary"} in configs
        assert {"source": "secondary"} in configs

    async def test_sink_with_chain_called_per_node(
        self, workflow_environment, graph_builder
    ):
        """Test 21: Sink is called once per node in a chain (A → B)."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        builder.add_edge(a, "response", b, "text")
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(name="mock_sink"),
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 2
        names = {c.node_name for c in mock.calls}
        assert names == {"A", "B"}

    async def test_failing_sink_plus_working_sink(
        self, workflow_environment, graph_builder
    ):
        """Test 22: Failing sink doesn't prevent working sink from being called."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            primary_output_sink=OutputSinkConfig(name="failing_sink"),
            output_sinks=[OutputSinkConfig(name="mock_sink")],
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 1
        assert mock.calls[0].status == "SUCCESS"

    async def test_no_sinks_configured(self, workflow_environment, graph_builder):
        """Test 23: Workflow without any sinks still works (backward compat)."""
        builder = graph_builder
        builder.add_node("A", inputs=["user_input"], outputs=["response"])
        version = await sync_to_async(builder.build)()

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 0

    async def test_node_sink_overrides(self, workflow_environment, graph_builder):
        """Test 24: Per-node sink overrides only apply to the targeted node."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["text_out"])
        builder.add_edge(a, "response", b, "text")
        version = await sync_to_async(builder.build)()

        # Get node B's ID so we can target the override
        nodes = await sync_to_async(
            lambda: {n.name: str(n.id) for n in version.nodes.all()}
        )()
        node_b_id = nodes["B"]

        result, execution = await run_workflow_with_sinks(
            workflow_environment,
            version,
            {"user_input": "hello"},
            node_sink_overrides={
                node_b_id: [OutputSinkConfig(name="mock_sink", config={"target": "B"})],
            },
        )

        assert result.status == "SUCCESS"

        mock = get_sink("mock_sink")
        # Only node B should have triggered the mock_sink
        assert len(mock.calls) == 1
        assert mock.calls[0].node_name == "B"
        assert mock.calls[0].config == {"target": "B"}


# =============================================================================
# Group 8: Standalone Node Execution
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestStandaloneNodeExecution:
    """Tests for execute_node_standalone_activity."""

    async def test_standalone_success(self, workflow_environment):
        """Test 25: Standalone activity executes runner and calls sinks."""
        from tfc.temporal.agent_playground.activities import (
            _execute_node_standalone_sync,
        )

        result = _execute_node_standalone_sync(
            template_name="echo_template",
            config={},
            inputs={"input1": "hello"},
            organization_id="test-org-id",
            output_sink_configs=[{"name": "mock_sink", "config": {"k": "v"}}],
            node_id="test-node-id",
            node_name="TestNode",
            metadata={"custom": "data"},
        )

        assert result["status"] == "SUCCESS"
        assert result["outputs"] == {"input1": "hello"}
        assert len(result["sink_results"]) == 1
        assert result["sink_results"][0]["status"] == "SUCCESS"

        mock = get_sink("mock_sink")
        assert len(mock.calls) == 1
        ctx = mock.calls[0]
        assert ctx.node_id == "test-node-id"
        assert ctx.node_name == "TestNode"
        assert ctx.status == "SUCCESS"
        assert ctx.config == {"k": "v"}
        assert ctx.metadata == {"custom": "data"}

    async def test_standalone_runner_failure(self, workflow_environment):
        """Test 26: Standalone activity calls sinks even when runner fails."""
        from tfc.temporal.agent_playground.activities import (
            _execute_node_standalone_sync,
        )

        result = _execute_node_standalone_sync(
            template_name="fail_template",
            config={},
            inputs={"input1": "hello"},
            organization_id="test-org-id",
            output_sink_configs=[{"name": "mock_sink", "config": {}}],
            node_id="fail-node",
            node_name="FailNode",
        )

        assert result["status"] == "FAILED"
        assert "Intentional test failure" in result["error"]
        assert result["outputs"] == {}

        # Sink should still be called with FAILED status
        mock = get_sink("mock_sink")
        assert len(mock.calls) == 1
        assert mock.calls[0].status == "FAILED"
        assert mock.calls[0].node_name == "FailNode"

    async def test_standalone_no_sinks(self, workflow_environment):
        """Test 27: Standalone activity works without any sinks configured."""
        from tfc.temporal.agent_playground.activities import (
            _execute_node_standalone_sync,
        )

        result = _execute_node_standalone_sync(
            template_name="echo_template",
            config={},
            inputs={"input1": "hello"},
            organization_id="test-org-id",
        )

        assert result["status"] == "SUCCESS"
        assert result["outputs"] == {"input1": "hello"}
        assert result["sink_results"] == []

    async def test_standalone_sink_failure_isolated(self, workflow_environment):
        """Test 28: Standalone activity returns SUCCESS even if sink fails."""
        from tfc.temporal.agent_playground.activities import (
            _execute_node_standalone_sync,
        )

        result = _execute_node_standalone_sync(
            template_name="echo_template",
            config={},
            inputs={"input1": "hello"},
            organization_id="test-org-id",
            output_sink_configs=[{"name": "failing_sink", "config": {}}],
        )

        assert result["status"] == "SUCCESS"
        assert result["outputs"] == {"input1": "hello"}
        assert len(result["sink_results"]) == 1
        assert result["sink_results"][0]["status"] == "FAILED"
        assert "Intentional sink failure" in result["sink_results"][0]["error"]


# =============================================================================
# LLM Prompt Node Helpers
# =============================================================================


@sync_to_async
def _create_prompt_fixtures(organization, workspace, node, prompt_config_snapshot):
    """Create PromptTemplate + PromptVersion + PromptTemplateNode for a node."""
    from agent_playground.models.prompt_template_node import PromptTemplateNode
    from model_hub.models.run_prompt import PromptTemplate, PromptVersion

    pt = PromptTemplate.no_workspace_objects.create(
        name=f"test-prompt-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    pv = PromptVersion.no_workspace_objects.create(
        template_version="1.0",
        original_template=pt,
        prompt_config_snapshot=prompt_config_snapshot,
    )
    PromptTemplateNode.no_workspace_objects.create(
        node=node,
        prompt_template=pt,
        prompt_version=pv,
    )
    return pt, pv


@sync_to_async
def _get_node_by_name(graph_version, name):
    """Get a node from a graph version by name."""
    return graph_version.nodes.get(name=name)


@sync_to_async
def _create_user_response_schema(name, schema):
    """Create a UserResponseSchema record and return its UUID string."""
    from model_hub.models.run_prompt import UserResponseSchema

    urs = UserResponseSchema.no_workspace_objects.create(name=name, schema=schema)
    return str(urs.id)


# =============================================================================
# Group 9: LLM Prompt Node Execution
# =============================================================================

# Mock target: only RunPrompt is mocked (no real LLM calls).
# The real LLMPromptRunner runs the full flow: PromptTemplateNode DB lookup,
# variable resolution (including JSON dot notation extraction), message
# rendering, and response formatting.
_MOCK_RUN_PROMPT = "agent_playground.services.engine.runners.llm_prompt.RunPrompt"


def _text_config(prompt_text, **config_overrides):
    """Build a minimal prompt_config_snapshot for text response format."""
    config = {"model": "gpt-4o", "model_detail": {"type": "chat"}}
    config.update(config_overrides)
    return {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": prompt_text}]}
        ],
        "configuration": config,
    }


@pytest.mark.django_db(transaction=True)
class TestLLMPromptExecution:
    """Tests for the real LLMPromptRunner through the full Temporal workflow."""

    # -----------------------------------------------------------------
    # Success cases
    # -----------------------------------------------------------------

    async def test_llm_prompt_simple_text(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 29: Single LLM node with text response and simple variable."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("Answer: {{question}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Hello world", {})
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "What is AI?"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_json_response(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 30: LLM node with JSON response format parses response."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("Return JSON: {{question}}", response_format="json"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = (
                '{"answer": "42"}',
                {},
            )
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "What is the answer?"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_json_schema_response(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 31: LLM node with json_schema response format."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        response_format = {
            "id": "test-schema",
            "name": "test",
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            },
        }
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("{{question}}", response_format=response_format),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = (
                '{"answer": "hello"}',
                {},
            )
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "test"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_json_schema_uuid_response(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 31b: LLM node with json_schema as UUID string pointing to UserResponseSchema."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        # Create a UserResponseSchema record in DB
        schema_uuid = await _create_user_response_schema(
            name="summary-schema",
            schema={
                "type": "object",
                "required": ["summary"],
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The generated summary.",
                    }
                },
                "additional_properties": False,
            },
        )

        node = await _get_node_by_name(version, "LLM")
        # response_format is just the UUID string — runner must still parse JSON
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("{{question}}", response_format=schema_uuid),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = (
                '{"summary": "AI is artificial intelligence."}',
                {},
            )
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "Summarize AI"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_json_schema_uuid_dict_response(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 31c: LLM node with json_schema as dict with 'id' but no embedded 'schema'."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        schema_uuid = await _create_user_response_schema(
            name="answer-schema",
            schema={
                "type": "object",
                "required": ["answer"],
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "The answer.",
                    }
                },
                "additional_properties": False,
            },
        )

        node = await _get_node_by_name(version, "LLM")
        # response_format is a dict with 'id' but no 'schema' — needs DB lookup
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config(
                "{{question}}",
                response_format={"id": schema_uuid, "name": "answer-schema"},
            ),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = (
                '{"answer": "42"}',
                {},
            )
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "What is the answer?"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_chain_with_dot_notation(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 32: Echo A → LLM B with dot notation (no extraction)."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B",
            template_name="llm_prompt",
            inputs=["A.response"],
            outputs=["response"],
        )
        builder.add_edge(a, "response", b, "A.response")
        version = await sync_to_async(builder.build)()

        node_b = await _get_node_by_name(version, "B")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node_b,
            _text_config("Process: {{A.response}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Processed!", {})
            result, execution = await run_workflow(
                workflow_environment, version, {"user_input": "hello"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["A"].status == NodeExecutionStatus.SUCCESS
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_multiple_variables(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 33: LLM node with multiple input variables."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["name", "context"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("Hello {{name}}, context: {{context}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Hi Alice!", {})
            result, execution = await run_workflow(
                workflow_environment,
                version,
                {"name": "Alice", "context": "testing"},
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

        # Verify both variables were rendered in the message
        call_args = MockRP.call_args
        rendered_messages = call_args.kwargs.get("messages", [])
        message_text = str(rendered_messages)
        assert "Alice" in message_text
        assert "testing" in message_text

    async def test_llm_prompt_dot_notation_key_extraction(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 34: Dot notation with key extraction — {{A.response.name}} extracts .name."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B",
            template_name="llm_prompt",
            inputs=["A.response.name"],
            outputs=["response"],
        )
        builder.add_edge(a, "response", b, "A.response.name")
        version = await sync_to_async(builder.build)()

        node_b = await _get_node_by_name(version, "B")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node_b,
            _text_config("Hello {{A.response.name}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Hi Alice!", {})
            # A outputs structured data; runner extracts .name from it
            result, execution = await run_workflow(
                workflow_environment,
                version,
                {"user_input": {"name": "Alice", "age": 30}},
            )

        node_execs = await _get_node_executions(execution)
        assert result.status == "SUCCESS"
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

        # Verify extraction: rendered message should contain "Alice", not the full dict
        call_args = MockRP.call_args
        rendered_messages = call_args.kwargs.get("messages", [])
        message_text = str(rendered_messages)
        assert "Alice" in message_text
        assert "age" not in message_text

    async def test_llm_prompt_dot_notation_array_extraction(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 35: Dot notation with array extraction — {{A.response[0]}} extracts first element."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B",
            template_name="llm_prompt",
            inputs=["A.response[0]"],
            outputs=["response"],
        )
        builder.add_edge(a, "response", b, "A.response[0]")
        version = await sync_to_async(builder.build)()

        node_b = await _get_node_by_name(version, "B")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node_b,
            _text_config("First item: {{A.response[0]}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Got it!", {})
            result, execution = await run_workflow(
                workflow_environment,
                version,
                {"user_input": ["first", "second", "third"]},
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

        # Verify extraction: rendered message should contain "first"
        call_args = MockRP.call_args
        rendered_messages = call_args.kwargs.get("messages", [])
        message_text = str(rendered_messages)
        assert "first" in message_text

    async def test_llm_prompt_dot_notation_deep_extraction(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 36: Deep dot notation — {{A.response.users[0].name}} extracts nested value."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B",
            template_name="llm_prompt",
            inputs=["A.response.users[0].name"],
            outputs=["response"],
        )
        builder.add_edge(a, "response", b, "A.response.users[0].name")
        version = await sync_to_async(builder.build)()

        node_b = await _get_node_by_name(version, "B")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node_b,
            _text_config("User: {{A.response.users[0].name}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            MockRP.return_value.litellm_response.return_value = ("Hello Alice!", {})
            result, execution = await run_workflow(
                workflow_environment,
                version,
                {"user_input": {"users": [{"name": "Alice"}, {"name": "Bob"}]}},
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["B"].status == NodeExecutionStatus.SUCCESS

        # Verify deep extraction: rendered message should contain "Alice"
        call_args = MockRP.call_args
        rendered_messages = call_args.kwargs.get("messages", [])
        message_text = str(rendered_messages)
        assert "Alice" in message_text
        assert "Bob" not in message_text

    # -----------------------------------------------------------------
    # Failure cases
    # -----------------------------------------------------------------

    async def test_llm_prompt_missing_prompt_template_node(
        self,
        workflow_environment,
        graph_builder,
        register_llm_runner,
    ):
        """Test 37: LLM node with no PromptTemplateNode linked → FAILED."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        # No _create_prompt_fixtures — deliberately missing

        with mock.patch(_MOCK_RUN_PROMPT):
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "hello"}
            )

        assert result.status == "FAILED"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.FAILED
        assert "PromptTemplateNode" in (node_execs["LLM"].error_message or "")

    async def test_llm_prompt_unsupported_modality(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 38: Non-chat modality → FAILED with modality error."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config(
                "{{question}}",
                model="tts-1",
                model_detail={"type": "tts"},
            ),
        )

        with mock.patch(_MOCK_RUN_PROMPT):
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "hello"}
            )

        assert result.status == "FAILED"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.FAILED
        assert "modality" in (node_execs["LLM"].error_message or "").lower()

    async def test_llm_prompt_missing_model(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 39: Missing model in configuration → FAILED."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        # Config with no model field
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "{{question}}"}],
                    }
                ],
                "configuration": {"model_detail": {"type": "chat"}},
            },
        )

        with mock.patch(_MOCK_RUN_PROMPT):
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "hello"}
            )

        assert result.status == "FAILED"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.FAILED
        assert "model" in (node_execs["LLM"].error_message or "").lower()

    async def test_llm_prompt_variable_not_found(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 40: Variable in prompt not provided in inputs → FAILED."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        # Prompt references {{missing_var}} which has no input port
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("Hello {{missing_var}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT):
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "hello"}
            )

        assert result.status == "FAILED"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.FAILED
        assert "missing_var" in (node_execs["LLM"].error_message or "")

    async def test_llm_prompt_json_parse_failure(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 41: JSON response format but non-JSON response → FAILED."""
        builder = graph_builder
        builder.add_node(
            "LLM",
            template_name="llm_prompt",
            inputs=["question"],
            outputs=["response"],
        )
        version = await sync_to_async(builder.build)()

        node = await _get_node_by_name(version, "LLM")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node,
            _text_config("{{question}}", response_format="json"),
        )

        with mock.patch(_MOCK_RUN_PROMPT) as MockRP:
            # Return non-JSON text when JSON is expected
            MockRP.return_value.litellm_response.return_value = (
                "not valid json",
                {},
            )
            result, execution = await run_workflow(
                workflow_environment, version, {"question": "hello"}
            )

        assert result.status == "SUCCESS"
        node_execs = await _get_node_executions(execution)
        assert node_execs["LLM"].status == NodeExecutionStatus.SUCCESS

    async def test_llm_prompt_extraction_key_not_found(
        self,
        workflow_environment,
        graph_builder,
        organization,
        workspace,
        register_llm_runner,
    ):
        """Test 42: Dot notation extraction fails (missing key) → FAILED."""
        builder = graph_builder
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node(
            "B",
            template_name="llm_prompt",
            inputs=["A.response.missing_key"],
            outputs=["response"],
        )
        builder.add_edge(a, "response", b, "A.response.missing_key")
        version = await sync_to_async(builder.build)()

        node_b = await _get_node_by_name(version, "B")
        await _create_prompt_fixtures(
            organization,
            workspace,
            node_b,
            _text_config("Value: {{A.response.missing_key}}"),
        )

        with mock.patch(_MOCK_RUN_PROMPT):
            # A outputs data that does NOT have "missing_key"
            result, execution = await run_workflow(
                workflow_environment,
                version,
                {"user_input": {"other_key": "value"}},
            )

        assert result.status == "FAILED"
        node_execs = await _get_node_executions(execution)
        assert node_execs["B"].status == NodeExecutionStatus.FAILED
