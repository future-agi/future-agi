"""Tests for cascade soft-delete utilities."""

import pytest

from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeExecutionStatus,
    NodeType,
    PortDirection,
)
from agent_playground.models.edge import Edge
from agent_playground.models.execution_data import ExecutionData
from agent_playground.models.graph import Graph
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_execution import NodeExecution
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.utils.cascade_delete import (
    cascade_soft_delete_graph,
    cascade_soft_delete_version_content,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

# Use fixtures from conftest.py: node_template, graph, graph_version, etc.


@pytest.fixture
def graph_with_content(graph, graph_version, node_template):
    """Create a graph with versions, nodes, ports, and edges.

    Uses existing graph and graph_version fixtures.
    """
    # Update version to active
    graph_version.status = GraphVersionStatus.ACTIVE
    graph_version.save()

    # Create nodes
    node1 = Node.no_workspace_objects.create(
        graph_version=graph_version,
        type=NodeType.ATOMIC,
        name="Node 1",
        node_template=node_template,
    )
    node2 = Node.no_workspace_objects.create(
        graph_version=graph_version,
        type=NodeType.ATOMIC,
        name="Node 2",
        node_template=node_template,
    )

    # Create ports (use valid STRICT template keys)
    port1_out = Port.no_workspace_objects.create(
        node=node1,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
    )
    port2_in = Port.no_workspace_objects.create(
        node=node2,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
    )

    NodeConnection.no_workspace_objects.create(
        graph_version=graph_version,
        source_node=node1,
        target_node=node2,
    )

    # Create edge
    edge = Edge.no_workspace_objects.create(
        graph_version=graph_version,
        source_port=port1_out,
        target_port=port2_in,
    )

    return {
        "graph": graph,
        "version": graph_version,
        "nodes": [node1, node2],
        "ports": [port1_out, port2_in],
        "edge": edge,
    }


@pytest.fixture
def graph_with_executions(graph_with_content):
    """Add executions to the graph."""
    version = graph_with_content["version"]
    node1 = graph_with_content["nodes"][0]
    port1_out = graph_with_content["ports"][0]

    # Create graph execution
    graph_execution = GraphExecution.no_workspace_objects.create(
        graph_version=version,
        status=GraphExecutionStatus.SUCCESS,
    )

    # Create node execution
    node_execution = NodeExecution.no_workspace_objects.create(
        graph_execution=graph_execution,
        node=node1,
        status=NodeExecutionStatus.SUCCESS,
    )

    # Create execution data
    execution_data = ExecutionData.no_workspace_objects.create(
        node_execution=node_execution,
        node=node1,
        port=port1_out,
        payload={"result": "test"},
    )

    graph_with_content["graph_execution"] = graph_execution
    graph_with_content["node_execution"] = node_execution
    graph_with_content["execution_data"] = execution_data

    return graph_with_content


class TestCascadeSoftDeleteGraph:
    """Tests for cascade_soft_delete_graph function."""

    def test_soft_deletes_graph(self, graph_with_content):
        """Test that the graph itself is soft-deleted."""
        graph = graph_with_content["graph"]

        cascade_soft_delete_graph(graph)

        graph.refresh_from_db()
        assert graph.deleted is True

    def test_soft_deletes_versions(self, graph_with_content):
        """Test that all versions are soft-deleted."""
        graph = graph_with_content["graph"]
        version = graph_with_content["version"]

        cascade_soft_delete_graph(graph)

        # Check version is soft-deleted (not visible in regular managers)
        assert GraphVersion.objects.filter(id=version.id).exists() is False
        assert GraphVersion.no_workspace_objects.filter(id=version.id).exists() is False
        # But still exists in database (all_objects includes deleted)
        assert GraphVersion.all_objects.filter(id=version.id).exists() is True

        version.refresh_from_db()
        assert version.deleted is True

    def test_soft_deletes_nodes(self, graph_with_content):
        """Test that all nodes are soft-deleted."""
        graph = graph_with_content["graph"]
        nodes = graph_with_content["nodes"]

        cascade_soft_delete_graph(graph)

        for node in nodes:
            node.refresh_from_db()
            assert node.deleted is True

    def test_soft_deletes_ports(self, graph_with_content):
        """Test that all ports are soft-deleted."""
        graph = graph_with_content["graph"]
        ports = graph_with_content["ports"]

        cascade_soft_delete_graph(graph)

        for port in ports:
            port.refresh_from_db()
            assert port.deleted is True

    def test_soft_deletes_edges(self, graph_with_content):
        """Test that all edges are soft-deleted."""
        graph = graph_with_content["graph"]
        edge = graph_with_content["edge"]

        cascade_soft_delete_graph(graph)

        edge.refresh_from_db()
        assert edge.deleted is True

    def test_soft_deletes_prompt_template_nodes(
        self, graph_with_content, prompt_template_node
    ):
        """Test that prompt-template node links are soft-deleted."""
        graph = graph_with_content["graph"]

        cascade_soft_delete_graph(graph)

        prompt_template_node.refresh_from_db()
        assert prompt_template_node.deleted is True
        assert not PromptTemplateNode.no_workspace_objects.filter(
            id=prompt_template_node.id
        ).exists()

    def test_soft_deletes_graph_dataset(
        self, graph_with_content, graph_dataset, dataset_columns, dataset_row_with_cells
    ):
        """Test that the linked dataset and table data are soft-deleted."""
        graph = graph_with_content["graph"]
        dataset = graph_dataset.dataset
        row, cells = dataset_row_with_cells

        cascade_soft_delete_graph(graph)

        graph_dataset.refresh_from_db()
        dataset.refresh_from_db()
        row.refresh_from_db()
        for column in dataset_columns:
            column.refresh_from_db()
        for cell in cells:
            cell.refresh_from_db()

        assert graph_dataset.deleted is True
        assert dataset.deleted is True
        assert row.deleted is True
        assert all(column.deleted for column in dataset_columns)
        assert all(cell.deleted for cell in cells)
        assert not GraphDataset.no_workspace_objects.filter(id=graph_dataset.id).exists()
        assert not Dataset.no_workspace_objects.filter(id=dataset.id).exists()
        assert not Row.no_workspace_objects.filter(id=row.id).exists()
        assert not Column.no_workspace_objects.filter(
            id__in=[column.id for column in dataset_columns]
        ).exists()
        assert not Cell.no_workspace_objects.filter(
            id__in=[cell.id for cell in cells]
        ).exists()

    def test_soft_deletes_executions(self, graph_with_executions):
        """Test that all executions are soft-deleted."""
        graph = graph_with_executions["graph"]
        graph_execution = graph_with_executions["graph_execution"]
        node_execution = graph_with_executions["node_execution"]
        execution_data = graph_with_executions["execution_data"]

        cascade_soft_delete_graph(graph)

        graph_execution.refresh_from_db()
        assert graph_execution.deleted is True

        node_execution.refresh_from_db()
        assert node_execution.deleted is True

        execution_data.refresh_from_db()
        assert execution_data.deleted is True


class TestCascadeSoftDeleteVersionContent:
    """Tests for cascade_soft_delete_version_content function."""

    def test_soft_deletes_version_and_nodes(self, graph_with_content):
        """Test that the version and its nodes are soft-deleted."""
        version = graph_with_content["version"]
        nodes = graph_with_content["nodes"]

        cascade_soft_delete_version_content(version)

        # Version should be soft-deleted
        version.refresh_from_db()
        assert version.deleted is True

        # Nodes should be soft-deleted
        for node in nodes:
            node.refresh_from_db()
            assert node.deleted is True

    def test_soft_deletes_ports(self, graph_with_content):
        """Test that ports are soft-deleted."""
        version = graph_with_content["version"]
        ports = graph_with_content["ports"]

        cascade_soft_delete_version_content(version)

        for port in ports:
            port.refresh_from_db()
            assert port.deleted is True

    def test_soft_deletes_edges(self, graph_with_content):
        """Test that edges are soft-deleted."""
        version = graph_with_content["version"]
        edge = graph_with_content["edge"]

        cascade_soft_delete_version_content(version)

        edge.refresh_from_db()
        assert edge.deleted is True

    def test_soft_deletes_executions(self, graph_with_executions):
        """Test that executions and their contents are soft-deleted."""
        version = graph_with_executions["version"]
        graph_execution = graph_with_executions["graph_execution"]
        node_execution = graph_with_executions["node_execution"]
        execution_data = graph_with_executions["execution_data"]

        cascade_soft_delete_version_content(version)

        graph_execution.refresh_from_db()
        assert graph_execution.deleted is True

        node_execution.refresh_from_db()
        assert node_execution.deleted is True

        execution_data.refresh_from_db()
        assert execution_data.deleted is True
