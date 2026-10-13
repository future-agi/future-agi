"""
Pytest fixtures for agent_playground Temporal e2e tests.

Provides:
- WorkflowEnvironment (in-memory Temporal server)
- Mock node runners (echo, fail, slow)
- GraphBuilder for programmatically creating graph structures

IMPORTANT: These tests must run sequentially (not with pytest-xdist parallel workers)
because the Temporal in-memory server + worker threads + DB connections lifecycle
is incompatible with xdist's parallel process model.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from django.db import close_old_connections, connection, transaction
from temporalio.testing import WorkflowEnvironment

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agent_playground.models import (
    Edge,
    Graph,
    GraphExecution,
    GraphVersion,
    Node,
    NodeTemplate,
    Port,
)
from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.node_connection import NodeConnection
from agent_playground.services.engine.node_runner import (
    BaseNodeRunner,
    clear_runners,
    register_runner,
)
from agent_playground.services.engine.output_sink import (
    BaseOutputSink,
    OutputSinkContext,
    clear_sinks,
    register_sink,
)

# =============================================================================
# Mock Node Runners
# =============================================================================


def _apply_output_map(config: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Remap runner output keys using config["output_map"] if present.

    route_node_outputs() matches the returned dict keys against the node's
    output port keys.  Since the unique_node_port_key constraint prevents a
    node from having the same key for both an input and output port, the
    runner must return data under the *output* port key names.

    output_map is a dict mapping input_key -> output_key, e.g.
    {"user_input": "response"}.  The GraphBuilder auto-generates this.
    """
    output_map = config.get("output_map")
    if output_map:
        result: dict[str, Any] = {}
        for in_key, out_key in output_map.items():
            if in_key in inputs:
                result[out_key] = inputs[in_key]
        return result
    return dict(inputs)


class EchoRunner(BaseNodeRunner):
    """Echoes all inputs as outputs. Used for most test scenarios.

    Supports config["output_map"] to remap input keys to output port keys.
    """

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        return _apply_output_map(config, inputs)


class FailRunner(BaseNodeRunner):
    """Always raises RuntimeError. Used for failure propagation tests."""

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError("Intentional test failure")


class SlowRunner(BaseNodeRunner):
    """Sleeps briefly before echoing. Used for concurrency tests."""

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        time.sleep(0.1)
        return _apply_output_map(config, inputs)


# =============================================================================
# Mock Output Sinks
# =============================================================================


class MockSink(BaseOutputSink):
    """Records all store() calls for assertion in tests."""

    def __init__(self):
        self.calls: list[OutputSinkContext] = []

    def store(self, context: OutputSinkContext) -> None:
        self.calls.append(context)


class FailingSink(BaseOutputSink):
    """Always raises RuntimeError. Used to test sink failure isolation."""

    def store(self, context: OutputSinkContext) -> None:
        raise RuntimeError("Intentional sink failure")


# =============================================================================
# TransactionTestCase CASCADE fix
# =============================================================================


@pytest.fixture(autouse=True, scope="session")
def _patch_flush_to_cascade():
    """Patch TransactionTestCase to use TRUNCATE ... CASCADE.

    Django's TransactionTestCase flushes the DB after each test, but only uses
    CASCADE when ``available_apps`` is set. Without CASCADE, the flush fails on
    FK constraints between apps (e.g. tracer → model_hub). This patches
    ``_fixture_teardown`` to always pass ``allow_cascade=True``.
    """
    from django.core.management import call_command
    from django.db import connections
    from django.test.testcases import TransactionTestCase

    original = TransactionTestCase._fixture_teardown

    def _fixture_teardown_with_cascade(self):
        for db_name in self._databases_names(include_mirrors=False):
            inhibit_post_migrate = self.available_apps is not None or (
                self.serialized_rollback
                and hasattr(connections[db_name], "_test_serialized_contents")
            )
            call_command(
                "flush",
                verbosity=0,
                interactive=False,
                database=db_name,
                reset_sequences=False,
                allow_cascade=True,
                inhibit_post_migrate=inhibit_post_migrate,
            )
            if self.serialized_rollback and hasattr(
                connections[db_name], "_test_serialized_contents"
            ):
                connections[db_name].creation.deserialize_db_from_string(
                    connections[db_name]._test_serialized_contents
                )

    TransactionTestCase._fixture_teardown = _fixture_teardown_with_cascade
    yield
    TransactionTestCase._fixture_teardown = original


# =============================================================================
# Temporal Environment Fixture
# =============================================================================


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def workflow_environment():
    """Start an in-memory Temporal server for the entire test session."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()


# =============================================================================
# Mock Runner Registration
# =============================================================================


@pytest.fixture(autouse=True)
def register_mock_runners():
    """Register mock runners before each test, clear after."""
    clear_runners()
    register_runner("test_template", EchoRunner())
    register_runner("echo_template", EchoRunner())
    register_runner("fail_template", FailRunner())
    register_runner("slow_template", SlowRunner())
    yield
    clear_runners()


@pytest.fixture(autouse=True)
def register_mock_sinks():
    """Register mock sinks before each test, clear after."""
    clear_sinks()
    register_sink("mock_sink", MockSink())
    register_sink("failing_sink", FailingSink())
    yield
    clear_sinks()


@pytest.fixture(autouse=True)
def cleanup_db_connections():
    """Close stale DB connections after each test.

    Temporal activities run in threads that open their own DB connections.
    After the workflow completes, those connections may still be held open,
    which prevents Django's test DB flush from succeeding. Closing old
    connections here ensures clean state between tests.
    """
    yield
    close_old_connections()


# =============================================================================
# Base DB Fixtures (override root conftest to avoid CURRENT_WORKSPACE issues)
# =============================================================================


@pytest.fixture
def organization(db):
    """Create a test organization with a unique name per test."""
    return Organization.objects.create(name=f"Test Org {uuid.uuid4().hex[:8]}")


@pytest.fixture
def user(db, organization):
    """Create a test user with a unique email per test."""
    unique = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        email=f"temporal_test_{unique}@futureagi.com",
        password="testpassword123",
        name="Temporal Test User",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
    )
    return user


@pytest.fixture
def workspace(db, organization, user):
    """Create a test workspace without setting CURRENT_WORKSPACE."""
    return Workspace.objects.create(
        name=f"Test Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )


# =============================================================================
# Graph Builder
# =============================================================================


@dataclass
class _NodeSpec:
    """Internal spec for a node to be created."""

    name: str
    template_name: str
    node_type: str
    input_keys: list[str]
    output_keys: list[str]
    config: dict[str, Any]
    ref_graph_version: GraphVersion | None = None


@dataclass
class _EdgeSpec:
    """Internal spec for an edge to be created."""

    source_node_idx: int
    source_port_key: str
    target_node_idx: int
    target_port_key: str


class GraphBuilder:
    """
    Fluent builder for creating test graph structures in the database.

    Usage:
        builder = GraphBuilder(organization, workspace, user)
        a = builder.add_node("A", inputs=["user_input"], outputs=["response"])
        b = builder.add_node("B", inputs=["text"], outputs=["result"])
        builder.add_edge(a, "response", b, "text")
        version = builder.build()
    """

    def __init__(self, organization, workspace, user):
        self.organization = organization
        self.workspace = workspace
        self.user = user
        self._nodes: list[_NodeSpec] = []
        self._edges: list[_EdgeSpec] = []
        self._graph_name: str = "Test Graph"

    def set_name(self, name: str) -> "GraphBuilder":
        """Set the graph name."""
        self._graph_name = name
        return self

    def set_type(self, graph_type: str) -> "GraphBuilder":
        """Set the graph type (no-op, Graph model has no type field)."""
        return self

    def add_node(
        self,
        name: str,
        template_name: str = "test_template",
        inputs: list[str] | None = None,
        outputs: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> int:
        """
        Add an atomic node. Returns the node index for edge references.

        Args:
            name: Display name for the node
            template_name: Name of the NodeTemplate to use
            inputs: List of input port keys (default: ["input1"])
            outputs: List of output port keys (default: ["output1"])
            config: Node config dict (default: {})
        """
        if inputs is None:
            inputs = ["input1"]
        if outputs is None:
            outputs = ["output1"]
        if config is None:
            config = {}

        # Auto-generate output_map so the mock runner returns data under the
        # correct output port keys.  Maps input keys → output keys positionally.
        if "output_map" not in config and inputs and outputs:
            output_map = {}
            for i, out_key in enumerate(outputs):
                if i < len(inputs):
                    output_map[inputs[i]] = out_key
            config["output_map"] = output_map

        idx = len(self._nodes)
        self._nodes.append(
            _NodeSpec(
                name=name,
                template_name=template_name,
                node_type=NodeType.ATOMIC,
                input_keys=inputs,
                output_keys=outputs,
                config=config,
            )
        )
        return idx

    def add_module_node(
        self,
        name: str,
        ref_graph_version: GraphVersion,
        inputs: list[str] | None = None,
        outputs: list[str] | None = None,
    ) -> int:
        """
        Add a module node. Returns the node index for edge references.

        Args:
            name: Display name
            ref_graph_version: The active GraphVersion of the module graph
            inputs: Input port keys
            outputs: Output port keys
        """
        if inputs is None:
            inputs = ["input1"]
        if outputs is None:
            outputs = ["output1"]

        idx = len(self._nodes)
        self._nodes.append(
            _NodeSpec(
                name=name,
                template_name="",
                node_type=NodeType.SUBGRAPH,
                input_keys=inputs,
                output_keys=outputs,
                config={},
                ref_graph_version=ref_graph_version,
            )
        )
        return idx

    def add_edge(
        self,
        source_node_idx: int,
        source_port_key: str,
        target_node_idx: int,
        target_port_key: str,
    ) -> "GraphBuilder":
        """Add an edge between two nodes by index and port key."""
        self._edges.append(
            _EdgeSpec(
                source_node_idx=source_node_idx,
                source_port_key=source_port_key,
                target_node_idx=target_node_idx,
                target_port_key=target_port_key,
            )
        )
        return self

    def _get_or_create_template(self, template_name: str) -> NodeTemplate:
        """Get existing template or create a minimal one for testing."""
        template, _ = NodeTemplate.no_workspace_objects.get_or_create(
            name=template_name,
            defaults={
                "display_name": template_name.replace("_", " ").title(),
                "description": f"Test template: {template_name}",
                "categories": ["testing"],
                "input_definition": [],
                "output_definition": [],
                "input_mode": PortMode.DYNAMIC,
                "output_mode": PortMode.DYNAMIC,
                "config_schema": {},
            },
        )
        return template

    @transaction.atomic
    def build(self, activate: bool = True) -> GraphVersion:
        """
        Create all DB objects and return the GraphVersion.

        Args:
            activate: If True, set version status to ACTIVE (default: True)
        """
        # Create Graph
        graph = Graph.no_workspace_objects.create(
            organization=self.organization,
            workspace=self.workspace,
            name=self._graph_name,
            created_by=self.user,
        )

        # Create GraphVersion
        status = GraphVersionStatus.ACTIVE if activate else GraphVersionStatus.DRAFT
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=status,
        )

        # Create Nodes and Ports
        created_nodes: list[Node] = []
        # Map: (node_idx, port_key) -> Port
        port_map: dict[tuple[int, str], Port] = {}

        for idx, node_spec in enumerate(self._nodes):
            if node_spec.node_type == NodeType.SUBGRAPH:
                node = Node.no_workspace_objects.create(
                    graph_version=version,
                    ref_graph_version=node_spec.ref_graph_version,
                    type=NodeType.SUBGRAPH,
                    name=node_spec.name,
                    config={},
                    position={"x": idx * 200, "y": 100},
                )
            else:
                template = self._get_or_create_template(node_spec.template_name)
                node = Node.no_workspace_objects.create(
                    graph_version=version,
                    node_template=template,
                    type=NodeType.ATOMIC,
                    name=node_spec.name,
                    config=node_spec.config,
                    position={"x": idx * 200, "y": 100},
                )

            created_nodes.append(node)

            # For subgraph nodes, build a lookup of child graph boundary ports
            # so we can set ref_port on the module node's ports.
            child_boundary_ports: dict[tuple[str, str], Port] = {}
            if node_spec.node_type == NodeType.SUBGRAPH and node_spec.ref_graph_version:
                child_ports = Port.no_workspace_objects.filter(
                    node__graph_version=node_spec.ref_graph_version,
                )
                # Find which child ports are connected (have incoming/outgoing edges)
                child_edges = Edge.no_workspace_objects.filter(
                    graph_version=node_spec.ref_graph_version,
                )
                connected_input_ids = {e.target_port_id for e in child_edges}
                connected_output_ids = {e.source_port_id for e in child_edges}

                for cp in child_ports:
                    if (
                        cp.direction == PortDirection.INPUT
                        and cp.id not in connected_input_ids
                    ):
                        child_boundary_ports[(cp.routing_key, "input")] = cp
                    elif (
                        cp.direction == PortDirection.OUTPUT
                        and cp.id not in connected_output_ids
                    ):
                        child_boundary_ports[(cp.routing_key, "output")] = cp

            # Create input ports
            # Use key="custom" for DYNAMIC-mode templates; display_name
            # carries the logical name and is used as routing_key.
            for key in node_spec.input_keys:
                ref_port = child_boundary_ports.get((key, "input"))
                port = Port(
                    node=node,
                    key=(
                        key
                        if node.node_template
                        and node.node_template.input_mode == PortMode.STRICT
                        else "custom"
                    ),
                    display_name=key,
                    direction=PortDirection.INPUT,
                    data_schema={},
                    required=True,
                    ref_port=ref_port,
                )
                port.save()
                port_map[(idx, key)] = port

            # Create output ports
            for key in node_spec.output_keys:
                ref_port = child_boundary_ports.get((key, "output"))
                port = Port(
                    node=node,
                    key=(
                        key
                        if node.node_template
                        and node.node_template.output_mode == PortMode.STRICT
                        else "custom"
                    ),
                    display_name=key,
                    direction=PortDirection.OUTPUT,
                    data_schema={},
                    required=True,
                    ref_port=ref_port,
                )
                port.save()
                port_map[(idx, key)] = port

        # Create NodeConnections (required before Edges)
        connection_pairs: set[tuple[int, int]] = set()
        for edge_spec in self._edges:
            pair = (edge_spec.source_node_idx, edge_spec.target_node_idx)
            if pair not in connection_pairs:
                connection_pairs.add(pair)
                NodeConnection.no_workspace_objects.create(
                    graph_version=version,
                    source_node=created_nodes[edge_spec.source_node_idx],
                    target_node=created_nodes[edge_spec.target_node_idx],
                )

        # Create Edges
        for edge_spec in self._edges:
            source_port = port_map[
                (edge_spec.source_node_idx, edge_spec.source_port_key)
            ]
            target_port = port_map[
                (edge_spec.target_node_idx, edge_spec.target_port_key)
            ]
            Edge.no_workspace_objects.create(
                graph_version=version,
                source_port=source_port,
                target_port=target_port,
            )

        return version


@pytest.fixture
def graph_builder(organization, workspace, user):
    """Provide a fresh GraphBuilder for each test."""
    return GraphBuilder(organization, workspace, user)


# =============================================================================
# LLM Prompt Runner Fixtures
# =============================================================================


@pytest.fixture
def register_llm_runner(register_mock_runners):
    """Register the real LLMPromptRunner on top of mock runners."""
    from agent_playground.services.engine.runners.llm_prompt import LLMPromptRunner

    register_runner("llm_prompt", LLMPromptRunner())
